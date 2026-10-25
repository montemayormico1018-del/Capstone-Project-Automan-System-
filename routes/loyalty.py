"""Customer loyalty and rewards API.

Two audiences share one blueprint, split by URL prefix:

- ``/me/*``, ``/rewards``, ``/redeem``   the logged-in customer. The
  customer is always resolved from the JWT identity -- a customer_id in a
  request body is never trusted, because that's the difference between "view
  my points" and "view anyone's points".
- ``/admin/*``                            admin/staff. Every write here goes
  through log_audit.
"""
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime

from models.customer import Customer
from models.loyalty import Reward, RewardRedemption, REWARD_DISCOUNT, VALID_REWARD_TYPES
from models.user import User
from utils.audit import log_audit
from utils import loyalty_service as svc

loyalty_bp = Blueprint('loyalty', __name__)

STAFF_ROLES = ('admin', 'staff')
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100


def get_identity():
    """Parse JWT identity consistently (handles string or dict)."""
    try:
        identity = get_jwt_identity()
        if isinstance(identity, str):
            import json
            identity = json.loads(identity)
        if not isinstance(identity, dict):
            identity = {'id': None, 'role': 'unknown'}
        return identity
    except Exception as e:
        current_app.logger.exception('get_identity failed: %s', e)
        return {'id': None, 'role': 'unknown'}


def _non_negative_number(value, field_name, required=True):
    """Same validation gap fixed in routes/sales.py and routes/job_orders.py:
    numeric input went straight into float()/the model with no bound.
    Returns (value, error_message)."""
    if value is None:
        if required:
            return None, f'{field_name} is required'
        return None, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f'{field_name} must be a number'
    if number < 0:
        return None, f'{field_name} cannot be negative'
    return number, None


def _capped_text(value, max_length, field_label):
    """Length-cap a free-text field. Returns (cleaned_value, error)."""
    if value is None:
        return value, None
    text = str(value)
    if len(text) > max_length:
        return None, f'{field_label} must be {max_length} characters or fewer'
    return text, None


def _positive_int(value, field_name, required=True, default=None):
    """Coerce to a whole number > 0. Returns (value, error)."""
    if value is None:
        if required:
            return None, f'{field_name} is required'
        return default, None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, f'{field_name} must be a whole number'
    if number <= 0:
        return None, f'{field_name} must be greater than zero'
    return number, None


def _customer_for_identity(identity):
    """The Customer document belonging to the logged-in account.

    Matched on email, the same linkage routes/appointments.py uses -- a
    Customer document carries no user_id. Returns (customer, error_response).
    """
    user = User.get(identity.get('id'))
    if not user:
        return None, (jsonify({'error': 'User not found. Please login again.'}), 404)
    if not user.email:
        return None, (jsonify({'error': 'This account has no email address to match a customer record'}), 404)
    customer = Customer.query.filter_by(email=user.email).first()
    if not customer:
        return None, (jsonify({'error': 'No customer record found for this account'}), 404)
    return customer, None


def _require_staff(identity):
    return identity.get('role') in STAFF_ROLES


def _page_args():
    limit, err = _positive_int(request.args.get('limit'), 'limit', required=False, default=PAGE_SIZE_DEFAULT)
    if err:
        return None, None, (jsonify({'error': err}), 400)
    limit = min(limit, PAGE_SIZE_MAX)
    offset, err = _positive_int(request.args.get('offset'), 'offset', required=False, default=0)
    if err:
        return None, None, (jsonify({'error': err}), 400)
    if offset is None:
        offset = 0
    return limit, offset, None


# ---------------------------------------------------------------------------
# Customer endpoints
# ---------------------------------------------------------------------------
@loyalty_bp.route('/me', methods=['GET'])
@jwt_required()
def get_my_account():
    """Account, tier and progress toward the next tier."""
    identity = get_identity()
    customer, error = _customer_for_identity(identity)
    if error:
        return error

    account = svc.get_account(customer.id)
    account['customer'] = {'id': customer.id, 'name': customer.name}
    return jsonify(account), 200


@loyalty_bp.route('/me/transactions', methods=['GET'])
@jwt_required()
def get_my_transactions():
    """Paginated points history, newest first."""
    identity = get_identity()
    customer, error = _customer_for_identity(identity)
    if error:
        return error

    limit, offset, error = _page_args()
    if error:
        return error

    types = request.args.getlist('type')
    return jsonify(svc.list_transactions(customer.id, limit=limit, offset=offset, types=types)), 200


@loyalty_bp.route('/me/vouchers', methods=['GET'])
@jwt_required()
def get_my_vouchers():
    """The customer's vouchers. Expiry is evaluated on read, so a voucher
    past its expiry date reports 'expired' without needing a background job."""
    identity = get_identity()
    customer, error = _customer_for_identity(identity)
    if error:
        return error

    include_expired = request.args.get('include_expired', '1') not in ('0', 'false')
    vouchers = svc.list_vouchers(customer.id, include_expired=include_expired)

    # Opportunistic expiry warning -- see notify_expiring_vouchers.
    try:
        svc.notify_expiring_vouchers()
    except Exception as e:
        current_app.logger.warning('notify_expiring_vouchers: %s', e)

    return jsonify({'vouchers': vouchers, 'total': len(vouchers)}), 200


@loyalty_bp.route('/rewards', methods=['GET'])
@jwt_required()
def get_rewards():
    """Active rewards catalog, with per-customer redemption state so the
    UI can disable a Redeem button before the customer taps it."""
    identity = get_identity()
    customer, error = _customer_for_identity(identity)
    if error:
        return error

    settings = svc.get_settings()
    account = svc.get_account(customer.id)
    balance = account['points_balance']

    rewards = [r for r in Reward.query.all() if r.is_active and not r.is_deleted]
    rewards.sort(key=lambda r: r.points_cost or 0)

    redeemed_counts = {}
    for row in RewardRedemption.query.filter_by(customer_id=str(customer.id)).all():
        redeemed_counts[row.reward_id] = redeemed_counts.get(row.reward_id, 0) + 1

    per_tx_cap = int(settings['max_redemption_per_transaction'])
    result = []
    for reward in rewards:
        data = reward.to_dict()
        limit = int(reward.per_customer_limit or 0)
        redeemed = redeemed_counts.get(reward.id, 0)
        reasons = []
        if not reward.is_in_stock:
            reasons.append('Out of stock')
        if reward.points_cost > balance:
            reasons.append('Not enough points')
        if reward.points_cost > per_tx_cap:
            reasons.append(f'Costs more than the {per_tx_cap}-point per-transaction limit')
        if limit > 0 and redeemed >= limit:
            reasons.append(f'Redeemed limit reached ({limit})')
        data['can_redeem'] = not reasons
        data['redeemed_count'] = redeemed
        data['blocked_reason'] = reasons[0] if reasons else None
        result.append(data)

    return jsonify({'rewards': result, 'points_balance': balance, 'tier': account['tier']}), 200


@loyalty_bp.route('/redeem', methods=['POST'])
@jwt_required()
def redeem_reward():
    """Redeem points for a reward. Returns the issued voucher."""
    identity = get_identity()
    customer, error = _customer_for_identity(identity)
    if error:
        return error

    data = request.get_json(silent=True) or {}
    reward_id = data.get('reward_id')
    if not reward_id:
        return jsonify({'error': 'reward_id is required'}), 400

    try:
        redemption = svc.redeem_reward(customer.id, reward_id, identity)
    except svc.LoyaltyError as e:
        return jsonify({'error': e.message}), e.status

    log_audit(identity, 'create', 'reward_redemption', redemption.id,
              f'Redeemed reward {reward_id} for {redemption.points_spent} points '
              f'(voucher {redemption.voucher_code})')

    return jsonify({
        'message': 'Reward redeemed',
        'redemption': redemption.to_dict(),
    }), 201


# ---------------------------------------------------------------------------
# Admin / staff: rewards catalog
# ---------------------------------------------------------------------------
@loyalty_bp.route('/admin/rewards', methods=['GET'])
@jwt_required()
def admin_list_rewards():
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    include_inactive = request.args.get('include_inactive', '1') not in ('0', 'false')
    rewards = Reward.query.all()
    if not include_inactive:
        rewards = [r for r in rewards if r.is_active and not r.is_deleted]
    rewards.sort(key=lambda r: r.name or '')
    return jsonify({'rewards': [r.to_dict() for r in rewards], 'total': len(rewards)}), 200


def _validate_reward_payload(data, require_all=True, existing_type=None):
    """Shared field validation for reward create/update. Returns (fields, error)."""
    fields = {}
    now = datetime.utcnow()

    if 'name' in data or require_all:
        name, err = _capped_text(data.get('name'), 120, 'Name')
        if err:
            return None, err
        if not name or not str(name).strip():
            return None, 'Name is required'
        fields['name'] = str(name).strip()

    if 'description' in data or require_all:
        description, err = _capped_text(data.get('description'), 500, 'Description')
        if err:
            return None, err
        fields['description'] = description

    if 'points_cost' in data or require_all:
        cost, err = _positive_int(data.get('points_cost'), 'points_cost')
        if err:
            return None, err
        fields['points_cost'] = cost

    if 'reward_type' in data:
        reward_type = data.get('reward_type')
        if reward_type not in VALID_REWARD_TYPES:
            return None, f'reward_type must be one of {sorted(VALID_REWARD_TYPES)}'
        fields['reward_type'] = reward_type
    elif require_all:
        fields['reward_type'] = 'discount'

    if 'value' in data or require_all:
        # A discount reward's value is the PHP amount off, so it must be a
        # number. A service/item reward's value is free text describing what
        # staff deliver ("Free exterior car wash"), so only that case gets
        # length-capped instead.
        if data.get('value') is None:
            fields['value'] = data.get('value')
        else:
            # On update, `reward_type` may not be in the body -- fall back to
            # the type the reward already has, so editing a service reward's
            # value isn't validated as a discount amount.
            effective_type = fields.get('reward_type') or existing_type or 'discount'
            if effective_type == REWARD_DISCOUNT:
                value, err = _non_negative_number(data.get('value'), 'value', required=False)
                if err:
                    return None, err
                fields['value'] = value
            else:
                value, err = _capped_text(data.get('value'), 300, 'Value')
                if err:
                    return None, err
                fields['value'] = value

    if 'stock' in data:
        # None/null means unlimited, which is a legitimate setting, not a
        # missing one -- so this one is optional-but-typed rather than
        # required.
        if data.get('stock') is None:
            fields['stock'] = None
        else:
            stock, err = _non_negative_number(data.get('stock'), 'stock', required=False)
            if err:
                return None, err
            if stock is not None and stock != int(stock):
                return None, 'stock must be a whole number'
            fields['stock'] = int(stock) if stock is not None else None

    if 'per_customer_limit' in data:
        limit, err = _non_negative_number(data.get('per_customer_limit') or 0, 'per_customer_limit',
                                          required=False)
        if err:
            return None, err
        fields['per_customer_limit'] = int(limit or 0)

    if 'image_url' in data:
        url, err = _capped_text(data.get('image_url'), 500, 'Image URL')
        if err:
            return None, err
        fields['image_url'] = url

    if 'is_active' in data:
        fields['is_active'] = bool(data.get('is_active'))

    fields['updated_at'] = now
    return fields, None


@loyalty_bp.route('/admin/rewards', methods=['POST'])
@jwt_required()
def admin_create_reward():
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    fields, err = _validate_reward_payload(data, require_all=True)
    if err:
        return jsonify({'error': err}), 400

    settings = svc.get_settings()
    cap = int(settings['max_redemption_per_transaction'])
    if fields['points_cost'] > cap:
        # Worth refusing loudly: a reward priced above the per-transaction cap
        # can never be redeemed by anybody, so it would sit in the catalog
        # looking fine and be unbuyable forever.
        return jsonify({
            'error': f'points_cost is above the per-transaction limit of {cap} points, '
                     f'so this reward could never be redeemed'
        }), 400

    reward = Reward(is_deleted=False, created_at=datetime.utcnow(), **fields)
    reward.save()

    log_audit(identity, 'create', 'reward', reward.id,
              f'Created reward "{reward.name}" for {reward.points_cost} points')
    return jsonify({'message': 'Reward created', 'reward': reward.to_dict()}), 201


@loyalty_bp.route('/admin/rewards/<reward_id>', methods=['PUT'])
@jwt_required()
def admin_update_reward(reward_id):
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    reward = Reward.get(reward_id)
    if not reward or reward.is_deleted:
        return jsonify({'error': 'Reward not found'}), 404

    data = request.get_json(silent=True) or {}
    fields, err = _validate_reward_payload(data, require_all=False, existing_type=reward.reward_type)
    if err:
        return jsonify({'error': err}), 400
    if not fields:
        return jsonify({'error': 'No valid fields to update'}), 400

    new_cost = fields.get('points_cost', reward.points_cost)
    settings = svc.get_settings()
    cap = int(settings['max_redemption_per_transaction'])
    if new_cost > cap:
        return jsonify({
            'error': f'points_cost is above the per-transaction limit of {cap} points, '
                     f'so this reward could never be redeemed'
        }), 400

    for key, value in fields.items():
        setattr(reward, key, value)
    reward.save()

    log_audit(identity, 'update', 'reward', reward.id,
              f'Updated reward "{reward.name}"')
    return jsonify({'message': 'Reward updated', 'reward': reward.to_dict()}), 200


@loyalty_bp.route('/admin/rewards/<reward_id>', methods=['DELETE'])
@jwt_required()
def admin_delete_reward(reward_id):
    """Deactivate a reward. Never a hard delete -- outstanding vouchers
    reference it and it appears in past redemption history."""
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    reward = Reward.get(reward_id)
    if not reward or reward.is_deleted:
        return jsonify({'error': 'Reward not found'}), 404

    reward.is_active = False
    reward.is_deleted = True
    reward.deleted_at = datetime.utcnow()
    reward.deleted_reason = 'Deactivated via API'
    reward.updated_at = datetime.utcnow()
    reward.save()

    log_audit(identity, 'delete', 'reward', reward.id,
              f'Deactivated reward "{reward.name}"')
    return jsonify({'message': 'Reward deactivated', 'reward': reward.to_dict()}), 200


# ---------------------------------------------------------------------------
# Admin / staff: customers
# ---------------------------------------------------------------------------
@loyalty_bp.route('/admin/customers/<customer_id>', methods=['GET'])
@jwt_required()
def admin_get_customer_account(customer_id):
    """One customer's account plus recent history."""
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    customer = Customer.get(customer_id)
    if not customer:
        return jsonify({'error': 'Customer not found'}), 404

    limit, offset, error = _page_args()
    if error:
        return error

    account = svc.get_account(customer.id)
    account['customer'] = customer.to_dict()
    history = svc.list_transactions(customer.id, limit=limit, offset=offset)
    history['vouchers'] = svc.list_vouchers(customer.id)

    return jsonify({**account, **history}), 200


@loyalty_bp.route('/admin/adjust', methods=['POST'])
@jwt_required()
def admin_adjust_points():
    """Manual +/- adjustment. `reason` is mandatory -- this is the audited
    escape hatch, not a general-purpose points editor."""
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    customer_id = data.get('customer_id')
    if not customer_id:
        return jsonify({'error': 'customer_id is required'}), 400

    customer = Customer.get(customer_id)
    if not customer:
        return jsonify({'error': 'Customer not found'}), 404

    points, err = _positive_int(data.get('points'), 'points')
    if err:
        # Signed adjustments are expected here, so a negative value is legal
        # and _positive_int's "greater than zero" complaint would be wrong.
        try:
            points = int(data.get('points'))
        except (TypeError, ValueError):
            return jsonify({'error': 'points must be a whole number'}), 400

    reason, err = _capped_text(data.get('reason'), 300, 'Reason')
    if err:
        return jsonify({'error': err}), 400
    if not reason or not reason.strip():
        return jsonify({'error': 'A reason is required for a manual points adjustment'}), 400

    try:
        new_balance = svc.adjust_points(customer.id, points, reason.strip(), identity)
    except svc.LoyaltyError as e:
        return jsonify({'error': e.message}), e.status

    log_audit(identity, 'adjust', 'loyalty_account', customer.id,
              f'Adjusted points for {customer.name} by {points:+d} (new balance {new_balance}). '
              f'Reason: {reason.strip()}')

    return jsonify({
        'message': 'Points adjusted',
        'customer_id': customer.id,
        'points_balance': new_balance,
    }), 200


# ---------------------------------------------------------------------------
# Admin / staff: vouchers
# ---------------------------------------------------------------------------
@loyalty_bp.route('/admin/vouchers/validate', methods=['POST'])
@jwt_required()
def admin_validate_voucher():
    """Check a voucher code without consuming it."""
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    code = data.get('code') or data.get('voucher_code')
    if not code:
        return jsonify({'error': 'code is required'}), 400

    sale_total, err = _non_negative_number(data.get('sale_total'), 'sale_total', required=False)
    if err:
        return jsonify({'error': err}), 400

    result = svc.validate_voucher(code, sale_total=sale_total)
    if result['valid']:
        customer = Customer.get(result['customer_id']) if result['customer_id'] else None
        result['customer_name'] = customer.name if customer else None
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Admin / staff: settings
# ---------------------------------------------------------------------------
@loyalty_bp.route('/admin/settings', methods=['GET'])
@jwt_required()
def admin_get_settings():
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify(svc.get_settings()), 200


@loyalty_bp.route('/admin/settings', methods=['PUT'])
@jwt_required()
def admin_update_settings():
    identity = get_identity()
    if not _require_staff(identity):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'error': 'No settings supplied'}), 400

    try:
        settings = svc.save_settings(data, identity)
    except svc.LoyaltyError as e:
        return jsonify({'error': e.message}), e.status

    return jsonify({'message': 'Settings updated', 'settings': settings}), 200
