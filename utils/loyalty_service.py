"""Customer loyalty and rewards logic.

Everything the routes need to award, reverse, redeem or validate lives here
rather than in routes/loyalty.py, for two reasons:

1. routes/sales.py has to call the earn/reverse hooks from inside its payment
   and refund paths, and those must not import a blueprint.
2. The rules are the part most likely to change -- the final earning rules are
   still pending adviser approval -- so they need one place to be edited and
   one place to be tested.

Every tunable lives in the ``loyalty`` system-settings document. Nothing here
reads a rule out of the code except the defaults in DEFAULT_SETTINGS, which
exist only so a settings document that was never written still behaves
sensibly.

Concurrency: balance changes go through Firestore transactions. The
read-modify-write on loyalty_accounts is not safe otherwise -- two concurrent
redemptions would both read the pre-spend balance and both write, overdrawing.
"""
import math
import secrets
from datetime import datetime, timedelta

from flask import current_app

from firebase_config import get_db
from models.loyalty import (
    LoyaltyAccount, LoyaltyTransaction, Reward, RewardRedemption,
    TX_EARN, TX_REVERSAL, TX_EXPIRE, TX_REDEEM, TX_ADJUSTMENT,
    REWARD_DISCOUNT, VOUCHER_ACTIVE, VOUCHER_USED, VOUCHER_CANCELLED,
    TIER_BRONZE, TIER_SILVER, TIER_GOLD,
)

SETTINGS_DOC_ID = 'loyalty'

# Defaults only. Every one of these is admin-editable through
# GET/PUT /api/loyalty/admin/settings and is read back out of the settings
# document on every use -- these are never the value actually applied once a
# settings document exists.
DEFAULT_SETTINGS = {
    # Master switch. Off means no earning, no redemption; reads still work.
    'is_enabled': True,
    # Earning: floor(amount / amount_per_point * points_per_amount).
    # 1 point per PHP 100 at the defaults.
    'amount_per_point': 100.0,
    'points_per_amount': 1.0,
    # Vouchers expire this many days after they're issued.
    'voucher_expiry_days': 90,
    # Warn the customer this many days before a voucher expires.
    'voucher_expiry_warning_days': 3,
    # Abuse limits.
    'max_redemption_per_transaction': 1000,
    'daily_redemption_cap': 2000,
    # 0 = points never expire (lazy expiry is off entirely).
    'points_expiry_days': 0,
    # The day boundary for the daily cap. Recorded here rather than assumed so
    # the rule is visible to an admin reading the settings page -- a cap
    # counted in UTC would reset at a different hour than a shop in Manila
    # expects.
    'timezone': 'Asia/Manila',
    # Lifetime points earned -> tier.
    'tier_thresholds': {TIER_BRONZE: 0, TIER_SILVER: 500, TIER_GOLD: 2000},
}

# Settings that must be whole numbers, with a lower bound. Anything not in
# here (the booleans, the timezone string, the tier map) is validated
# separately.
_INT_SETTINGS = {
    'voucher_expiry_days': (1, 3650),
    'voucher_expiry_warning_days': (0, 365),
    'max_redemption_per_transaction': (1, 1000000),
    'daily_redemption_cap': (1, 1000000),
    'points_expiry_days': (0, 3650),
}
_FLOAT_SETTINGS = {
    'amount_per_point': (0.01, 1000000.0),
    'points_per_amount': (0.0, 1000000.0),
}

VOUCHER_CODE_PREFIX = 'AMR'
VOUCHER_CODE_LENGTH = 8


class LoyaltyError(Exception):
    """A rule the caller violated. Carries a user-facing message.

    Distinct from a genuine bug: routes turn this into a 400/409, whereas an
    unexpected exception is a 500 and a reason to look at the logs.
    """

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Deterministic transaction ids
# ---------------------------------------------------------------------------
def earn_txn_id(sale_id):
    return f'earn_{sale_id}'


def reversal_txn_id(sale_id):
    return f'reversal_{sale_id}'


def expire_txn_id(earn_transaction_id):
    return f'expire_{earn_transaction_id}'


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings(refresh=False):
    """Read the loyalty settings document, merged over the defaults.

    Cached per request-ish on the settings document itself: a missing doc is
    simply the defaults, and a partially written doc (an admin saved only
    some fields) keeps the default for each field it doesn't have.
    """
    from models.system_setting import SystemSetting

    stored = {}
    try:
        doc = SystemSetting.get(SETTINGS_DOC_ID)
        if doc is not None:
            stored = {
                k: v for k, v in doc.__dict__.items()
                if not k.startswith('_') and k != 'id' and v is not None
            }
    except Exception as e:
        current_app.logger.warning('loyalty: could not read settings: %s', e)

    settings = dict(DEFAULT_SETTINGS)
    for key, value in stored.items():
        if key in settings:
            settings[key] = value
    thresholds = stored.get('tier_thresholds') or settings['tier_thresholds']
    if not isinstance(thresholds, dict):
        thresholds = DEFAULT_SETTINGS['tier_thresholds']
    settings['tier_thresholds'] = {
        TIER_BRONZE: int(thresholds.get(TIER_BRONZE, 0)),
        TIER_SILVER: int(thresholds.get(TIER_SILVER, 500)),
        TIER_GOLD: int(thresholds.get(TIER_GOLD, 2000)),
    }
    return settings


def save_settings(values, identity=None):
    """Persist a validated subset of settings. Returns the merged result.

    Raises LoyaltyError on an out-of-range value -- an admin must not be able
    to save a 0-point earn rate or a negative cap and silently break earning
    for the whole shop.
    """
    from models.system_setting import SystemSetting
    from utils.audit import log_audit

    incoming = dict(values or {})
    current = get_settings()
    merged = dict(current)

    for key, (minimum, maximum) in _INT_SETTINGS.items():
        if key not in incoming:
            continue
        try:
            number = int(incoming[key])
        except (TypeError, ValueError):
            raise LoyaltyError(f'{key} must be a whole number')
        if number < minimum or number > maximum:
            raise LoyaltyError(f'{key} must be between {minimum} and {maximum}')
        merged[key] = number

    for key, (minimum, maximum) in _FLOAT_SETTINGS.items():
        if key not in incoming:
            continue
        try:
            number = float(incoming[key])
        except (TypeError, ValueError):
            raise LoyaltyError(f'{key} must be a number')
        if number < minimum or number > maximum:
            raise LoyaltyError(f'{key} must be between {minimum} and {maximum}')
        merged[key] = number

    if 'is_enabled' in incoming:
        merged['is_enabled'] = bool(incoming['is_enabled'])

    if 'timezone' in incoming:
        tz = str(incoming['timezone'] or '').strip()
        if tz and _is_valid_timezone(tz):
            merged['timezone'] = tz

    if 'tier_thresholds' in incoming:
        raw = incoming['tier_thresholds']
        if not isinstance(raw, dict):
            raise LoyaltyError('tier_thresholds must be an object')
        thresholds = dict(merged['tier_thresholds'])
        for tier in (TIER_BRONZE, TIER_SILVER, TIER_GOLD):
            if tier not in raw:
                continue
            try:
                value = int(raw[tier])
            except (TypeError, ValueError):
                raise LoyaltyError(f'{tier} threshold must be a whole number')
            if value < 0:
                raise LoyaltyError(f'{tier} threshold cannot be negative')
            thresholds[tier] = value
        if not (thresholds[TIER_BRONZE] <= thresholds[TIER_SILVER] <= thresholds[TIER_GOLD]):
            raise LoyaltyError('Tier thresholds must be in ascending order: Bronze <= Silver <= Gold')
        merged['tier_thresholds'] = thresholds

    setting = SystemSetting.get(SETTINGS_DOC_ID) or SystemSetting(id=SETTINGS_DOC_ID)
    setting.updated_at = datetime.utcnow()
    for key, value in merged.items():
        setattr(setting, key, value)
    setting.save(merge=True)

    if identity:
        log_audit(identity, 'update', 'loyalty_settings', SETTINGS_DOC_ID,
                  'Updated loyalty settings: ' + ', '.join(
                      f'{k}={merged[k]}' for k in sorted(incoming) if k in merged))

    return merged


def _is_valid_timezone(name):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False


def _local_now(settings):
    """Current time in the shop's timezone. Falls back to naive UTC."""
    return _to_shop_local(datetime.utcnow(), settings)


def _to_shop_local(value, settings):
    """Reinterpret a stored (UTC, naive) timestamp in the shop's timezone.

    Everything is stamped with ``datetime.utcnow()`` and Firestore has no
    timezone concept, so stored timestamps are naive UTC. Comparing one
    against a Manila "today" without converting first silently breaks the
    daily cap for eight hours a day: at 00:30 Manila it is still the
    previous day in UTC, so that morning's redemptions would not count
    against the cap an admin expects them to.
    """
    if value is None:
        return None
    tz_name = (settings or {}).get('timezone') or 'Asia/Manila'
    try:
        from datetime import timezone as _timezone
        from zoneinfo import ZoneInfo
        if value.tzinfo is None:
            value = value.replace(tzinfo=_timezone.utc)
        return value.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------
def tier_for(lifetime_points, settings=None):
    settings = settings or get_settings()
    thresholds = settings['tier_thresholds']
    points = int(lifetime_points or 0)
    if points >= thresholds[TIER_GOLD]:
        return TIER_GOLD
    if points >= thresholds[TIER_SILVER]:
        return TIER_SILVER
    return TIER_BRONZE


def tier_progress(lifetime_points, settings=None):
    """Progress toward the next tier, for the dashboard progress bar."""
    settings = settings or get_settings()
    thresholds = settings['tier_thresholds']
    points = int(lifetime_points or 0)
    current = tier_for(points, settings)
    order = [TIER_BRONZE, TIER_SILVER, TIER_GOLD]
    index = order.index(current)
    if index == len(order) - 1:
        return {
            'tier': current,
            'next_tier': None,
            'points_to_next': 0,
            'progress_percent': 100,
            'current_threshold': thresholds[TIER_GOLD],
            'next_threshold': None,
        }
    next_tier = order[index + 1]
    current_threshold = thresholds[current]
    next_threshold = thresholds[next_tier]
    span = max(1, next_threshold - current_threshold)
    percent = int(min(100, max(0, ((points - current_threshold) / span) * 100)))
    return {
        'tier': current,
        'next_tier': next_tier,
        'points_to_next': max(0, next_threshold - points),
        'progress_percent': percent,
        'current_threshold': current_threshold,
        'next_threshold': next_threshold,
    }


# ---------------------------------------------------------------------------
# Earning
# ---------------------------------------------------------------------------
def points_for_amount(amount, settings=None):
    """Points earned on `amount`, floored.

    Flooring (not rounding) is deliberate: a 99-peso sale earns 0 points, so
    rounding would hand out points the shop is always down on.
    """
    settings = settings or get_settings()
    amount_per_point = float(settings['amount_per_point']) or 100.0
    points_per_amount = float(settings['points_per_amount'])
    if amount <= 0:
        return 0
    return int(math.floor((float(amount) / amount_per_point) * points_per_amount))


def earning_base_amount(sale):
    """The amount points are calculated on, per the agreed rule.

    basis = total - voucher_discount - points_redeemed_amount.

    Note the un-fold: Sale.calculate_total() subtracts `discount` from
    `total`, and a voucher is applied by adding its value to `discount`, so
    `total` is the amount actually payable and already has the voucher
    removed once. The rule wants the *pre-voucher* amount minus both
    exclusions, so the fold is undone first -- without this the voucher would
    be discounted a second time.
    """
    total = float(sale.total or 0)
    voucher_discount = float(getattr(sale, 'voucher_discount', 0) or 0)
    points_paid = float(getattr(sale, 'points_redeemed_amount', 0) or 0)
    pre_voucher_total = total + voucher_discount
    return max(0.0, pre_voucher_total - voucher_discount - points_paid)


def resolve_customer_id_for_sale(sale):
    """The loyalty customer a sale's points belong to, or None.

    A sale only reaches a customer through its job order, so walk that chain
    rather than storing a customer id on the sale (which could disagree with
    the vehicle the work was actually done on).
    """
    try:
        job_order = sale.job_order
        if not job_order:
            return None
        vehicle = job_order.vehicle
        if vehicle and vehicle.customer:
            return vehicle.customer.id
        appointment = job_order.appointment
        if appointment and appointment.customer:
            return appointment.customer.id
    except Exception as e:
        current_app.logger.exception(
            'loyalty: could not resolve customer for sale %s: %s', getattr(sale, 'id', None), e)
    return None


def _account_ref(customer_id):
    return get_db().collection('loyalty_accounts').document(str(customer_id))


def _txn_ref(txn_id):
    return get_db().collection('loyalty_transactions').document(str(txn_id))


def award_points_for_sale(sale, identity=None):
    """Award points for a fully-paid sale. Idempotent per sale.

    The transaction id is deterministic (``earn_<sale_id>``) and the
    existence of both that transaction and the earn row is re-checked *inside*
    the transaction, so re-marking a sale Paid, re-saving it, or two requests
    racing each other all still produce exactly one earn. Returns the awarded
    points, or None when nothing was awarded (not paid, already awarded, not
    eligible, loyalty disabled, no customer).

    Callers on the payment path must not let an exception here break the
    sale -- see routes/sales.py.
    """
    # The "fully paid" rule is enforced here rather than only at the call
    # site: this is the function that implements it, and routes/sales.py is
    # not the only thing that will ever want to award points. A
    # Completed-but-unpaid job order must earn nothing even if some future
    # caller reaches this directly.
    if sale.payment_status != 'Paid':
        current_app.logger.info(
            'loyalty: sale %s is %s, not Paid -- no points earned', sale.id, sale.payment_status)
        return None

    settings = get_settings()
    if not settings.get('is_enabled', True):
        current_app.logger.info('loyalty: disabled in settings, skipping earn for sale %s', sale.id)
        return None

    customer_id = resolve_customer_id_for_sale(sale)
    if not customer_id:
        current_app.logger.info('loyalty: no customer resolved for sale %s, skipping earn', sale.id)
        return None

    amount = earning_base_amount(sale)
    points = points_for_amount(amount, settings)
    if points <= 0:
        current_app.logger.info('loyalty: sale %s earns 0 points (basis %.2f)', sale.id, amount)
        return None

    txn_id = earn_txn_id(sale.id)
    expiry_days = int(settings.get('points_expiry_days') or 0)
    expires_at = datetime.utcnow() + timedelta(days=expiry_days) if expiry_days > 0 else None
    now = datetime.utcnow()
    account_ref = _account_ref(customer_id)
    txn_ref = _txn_ref(txn_id)
    sale_id = sale.id
    job_order_id = sale.job_order_id
    note = f'Earned on sale {sale.invoice_number or sale_id}'

    with get_db().transaction() as tx:
        if txn_ref.get().exists:
            current_app.logger.info('loyalty: sale %s already awarded (idempotent no-op)', sale_id)
            return None

        account_snap = tx.get(account_ref)
        account_data = account_snap.to_dict() if account_snap.exists else {}
        balance = int(account_data.get('points_balance') or 0)
        lifetime = int(account_data.get('lifetime_points') or 0)
        new_balance = balance + points
        new_lifetime = lifetime + points
        tier = tier_for(new_lifetime, settings)

        tx.set(account_ref, {
            'customer_id': str(customer_id),
            'points_balance': new_balance,
            'lifetime_points': new_lifetime,
            'tier': tier,
            'created_at': account_data.get('created_at') or now,
            'updated_at': now,
        })

        tx.set(txn_ref, {
            'customer_id': str(customer_id),
            'type': TX_EARN,
            'points': points,
            'balance_after': new_balance,
            'sale_id': sale_id,
            'job_order_id': job_order_id,
            'note': note,
            'created_by': (identity or {}).get('id'),
            'expires_at': expires_at,
            'created_at': now,
        })

    current_app.logger.info(
        'loyalty: awarded %d point(s) to customer %s for sale %s (basis %.2f)',
        points, customer_id, sale_id, amount)

    notify_points_earned(customer_id, points, new_balance, tier)
    return points


def reverse_points_for_sale(sale, reason, identity=None):
    """Reverse a sale's earn transaction with a negative 'reversal' row.

    Idempotent (``reversal_<sale_id>``) and clamped: a customer who already
    spent the points this sale earned can't go negative, so the reversal is
    capped at the current balance and lifetime points are floored at 0.

    Vouchers already used on the sale are NOT restored here -- refunding a
    voucher is a judgement call for staff, made through the manual
    adjustment endpoint with a reason.

    Returns the (possibly clamped) number of points reversed, or None when
    there was nothing to reverse.
    """
    earn_id = earn_txn_id(sale.id)
    txn_ref = _txn_ref(reversal_txn_id(sale.id))
    earn_ref = _txn_ref(earn_id)
    now = datetime.utcnow()
    note = f'Reversal for sale {sale.invoice_number or sale.id}: {reason}'
    sale_id = sale.id
    actor_id = (identity or {}).get('id')

    with get_db().transaction() as tx:
        if txn_ref.get().exists:
            current_app.logger.info('loyalty: sale %s already reversed (idempotent no-op)', sale_id)
            return None

        earn_snap = tx.get(earn_ref)
        if not earn_snap.exists:
            current_app.logger.info('loyalty: sale %s has no earn transaction to reverse', sale_id)
            return None
        earned = int((earn_snap.to_dict() or {}).get('points') or 0)
        if earned <= 0:
            return None

        customer_id = (earn_snap.to_dict() or {}).get('customer_id')
        if not customer_id:
            return None

        account_ref = _account_ref(customer_id)
        account_snap = tx.get(account_ref)
        account_data = account_snap.to_dict() if account_snap.exists else {}
        balance = int(account_data.get('points_balance') or 0)
        lifetime = int(account_data.get('lifetime_points') or 0)

        # Clamp: never debit more than the customer actually holds.
        reversed_points = min(earned, balance)
        new_balance = balance - reversed_points
        new_lifetime = max(0, lifetime - min(earned, lifetime))
        tier = tier_for(new_lifetime)

        if reversed_points:
            tx.set(account_ref, {
                'customer_id': str(customer_id),
                'points_balance': new_balance,
                'lifetime_points': new_lifetime,
                'tier': tier,
                'created_at': account_data.get('created_at') or now,
                'updated_at': now,
            })

        tx.set(txn_ref, {
            'customer_id': str(customer_id),
            'type': TX_REVERSAL,
            'points': -reversed_points,
            'balance_after': new_balance,
            'sale_id': sale_id,
            'job_order_id': sale.job_order_id,
            'note': note,
            'created_by': actor_id,
            'created_at': now,
        })

    current_app.logger.info(
        'loyalty: reversed %d of %d point(s) for sale %s (reason=%s)',
        reversed_points, earned, sale_id, reason)
    return reversed_points


# ---------------------------------------------------------------------------
# Manual adjustment
# ---------------------------------------------------------------------------
def adjust_points(customer_id, points, reason, identity=None):
    """Manual +/- adjustment. `reason` is required -- this is the escape
    hatch for the cases the rules don't cover (a goodwill gesture, correcting
    a voucher that was refunded by hand), and every use is audited."""
    if not reason or not str(reason).strip():
        raise LoyaltyError('A reason is required for a manual points adjustment')
    try:
        amount = int(points)
    except (TypeError, ValueError):
        raise LoyaltyError('points must be a whole number')
    if amount == 0:
        raise LoyaltyError('Adjustment cannot be zero')

    settings = get_settings()
    now = datetime.utcnow()
    account_ref = _account_ref(customer_id)
    txn_ref = _txn_ref(f'adj_{secrets.token_hex(8)}')
    actor_id = (identity or {}).get('id')

    with get_db().transaction() as tx:
        account_snap = tx.get(account_ref)
        account_data = account_snap.to_dict() if account_snap.exists else {}
        balance = int(account_data.get('points_balance') or 0)
        lifetime = int(account_data.get('lifetime_points') or 0)
        new_balance = max(0, balance + amount)
        # A manual debit never reduces lifetime points (tier is about what a
        # customer has earned over time, not a running balance).
        new_lifetime = max(0, lifetime + amount) if amount > 0 else lifetime
        tier = tier_for(new_lifetime, settings)

        tx.set(account_ref, {
            'customer_id': str(customer_id),
            'points_balance': new_balance,
            'lifetime_points': new_lifetime,
            'tier': tier,
            'created_at': account_data.get('created_at') or now,
            'updated_at': now,
        })
        tx.set(txn_ref, {
            'customer_id': str(customer_id),
            'type': TX_ADJUSTMENT,
            'points': amount,
            'balance_after': new_balance,
            'note': str(reason).strip(),
            'created_by': actor_id,
            'created_at': now,
        })

    return new_balance


# ---------------------------------------------------------------------------
# Lazy points expiry
# ---------------------------------------------------------------------------
def apply_expiring_points(customer_id, settings=None):
    """Expire points past their window, evaluated on read (no scheduler).

    A no-op unless points_expiry_days > 0. Each expired earn is closed out
    with one ``expire_<earn_txn_id>`` row, so running it on every read is
    safe: the second run finds the row already there.
    """
    settings = settings or get_settings()
    expiry_days = int(settings.get('points_expiry_days') or 0)
    if expiry_days <= 0:
        return 0

    now = datetime.utcnow()
    expired = 0
    earns = LoyaltyTransaction.query.filter_by(customer_id=str(customer_id), type=TX_EARN).all()
    for earn in earns:
        if not earn.expires_at or earn.expires_at > now:
            continue
        expire_id = expire_txn_id(earn.id)
        account_ref = _account_ref(customer_id)
        txn_ref = _txn_ref(expire_id)

        with get_db().transaction() as tx:
            if txn_ref.get().exists:
                continue
            account_snap = tx.get(account_ref)
            account_data = account_snap.to_dict() if account_snap.exists else {}
            balance = int(account_data.get('points_balance') or 0)
            lifetime = int(account_data.get('lifetime_points') or 0)
            # Clamp for the same reason reversal does: the points may already
            # have been spent.
            drop = min(int(earn.points or 0), balance)
            new_balance = balance - drop
            tx.set(account_ref, {
                'customer_id': str(customer_id),
                'points_balance': new_balance,
                'lifetime_points': lifetime,
                'tier': tier_for(lifetime, settings),
                'created_at': account_data.get('created_at') or now,
                'updated_at': now,
            })
            tx.set(txn_ref, {
                'customer_id': str(customer_id),
                'type': TX_EXPIRE,
                'points': -drop,
                'balance_after': new_balance,
                'note': f'Expired points from {now:%Y-%m-%d}',
                'created_by': 'system',
                'created_at': now,
            })
        expired += 1

    if expired:
        current_app.logger.info('loyalty: expired %d point grant(s) for customer %s', expired, customer_id)
    return expired


# ---------------------------------------------------------------------------
# Redemptions
# ---------------------------------------------------------------------------
def generate_voucher_code():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no I/O/0/1 -- read aloud at the counter
    body = ''.join(secrets.choice(alphabet) for _ in range(VOUCHER_CODE_LENGTH))
    return f'{VOUCHER_CODE_PREFIX}-{body}'


def _redemption_ref(code):
    return get_db().collection('reward_redemptions').document(str(code))


def _today_str(settings):
    return _local_now(settings).strftime('%Y-%m-%d')


def _points_spent_today(customer_id, settings):
    """Points the customer has already redeemed today, for the daily cap.

    "Today" is the shop's calendar day, not UTC's -- see _to_shop_local.
    """
    today = _today_str(settings)
    total = 0
    for row in RewardRedemption.query.filter_by(customer_id=str(customer_id)).all():
        created = _to_shop_local(row.created_at, settings)
        if created is None:
            continue
        if hasattr(created, 'strftime') and created.strftime('%Y-%m-%d') == today:
            total += int(row.points_spent or 0)
    return total


def redeem_reward(customer_id, reward_id, identity=None):
    """Redeem points for a reward and issue a voucher.

    Every check and every write happens inside one transaction, so two
    simultaneous redemptions of the last voucher (or against the same
    balance) serialize rather than both succeeding.

    Returns the created RewardRedemption.
    """
    settings = get_settings()
    if not settings.get('is_enabled', True):
        raise LoyaltyError('The loyalty program is not currently available', 403)

    reward_ref = get_db().collection('rewards').document(str(reward_id))
    account_ref = _account_ref(customer_id)
    now = datetime.utcnow()
    actor_id = (identity or {}).get('id')

    # Voucher codes are random, so this only ever collides by astronomical
    # chance -- but "astronomical" isn't "impossible", and a duplicate code
    # would silently hand one customer's voucher to another.
    code = generate_voucher_code()
    for _ in range(10):
        if not _redemption_ref(code).get().exists:
            break
        code = generate_voucher_code()
    else:
        raise LoyaltyError('Could not generate a unique voucher code, please try again', 503)

    redemption_ref = _redemption_ref(code)
    expiry_days = int(settings['voucher_expiry_days'])

    with get_db().transaction() as tx:
        reward_snap = tx.get(reward_ref)
        if not reward_snap.exists:
            raise LoyaltyError('Reward not found', 404)
        reward_data = reward_snap.to_dict() or {}
        reward = Reward(id=reward_snap.id, **reward_data)

        if reward.is_deleted or not reward.is_active:
            raise LoyaltyError('This reward is no longer available')
        if not reward.is_in_stock:
            raise LoyaltyError('This reward is out of stock', 409)

        cost = int(reward.points_cost or 0)
        if cost <= 0:
            raise LoyaltyError('This reward has an invalid points cost')

        per_tx_cap = int(settings['max_redemption_per_transaction'])
        if cost > per_tx_cap:
            raise LoyaltyError(
                f'This reward costs {cost} points, above the per-transaction limit of {per_tx_cap}',
                409)

        # Per-customer limit, counted across redemptions of this reward.
        limit = int(reward.per_customer_limit or 0)
        if limit > 0:
            already = len([r for r in RewardRedemption.query.filter_by(
                customer_id=str(customer_id), reward_id=str(reward_id)).all()])
            if already >= limit:
                raise LoyaltyError(
                    f'You have already redeemed this reward {already} time(s); the limit is {limit}',
                    409)

        account_snap = tx.get(account_ref)
        account_data = account_snap.to_dict() if account_snap.exists else {}
        balance = int(account_data.get('points_balance') or 0)
        if balance < cost:
            raise LoyaltyError(
                f'Insufficient points: this reward costs {cost} but your balance is {balance}',
                409)

        daily_cap = int(settings['daily_redemption_cap'])
        spent_today = _points_spent_today(customer_id, settings)
        if spent_today + cost > daily_cap:
            raise LoyaltyError(
                f'Daily redemption limit reached ({daily_cap} points per day). '
                f'You have redeemed {spent_today} today.',
                429)

        new_balance = balance - cost
        tx.set(account_ref, {
            'customer_id': str(customer_id),
            'points_balance': new_balance,
            'lifetime_points': int(account_data.get('lifetime_points') or 0),
            'tier': tier_for(int(account_data.get('lifetime_points') or 0), settings),
            'created_at': account_data.get('created_at') or now,
            'updated_at': now,
        })

        if reward.stock is not None:
            reward_data['stock'] = int(reward.stock) - 1
            reward_data['updated_at'] = now
            tx.set(reward_ref, reward_data, merge=True)

        tx.set(redemption_ref, {
            'customer_id': str(customer_id),
            'reward_id': str(reward_id),
            'reward_name': reward.name,
            'voucher_code': code,
            'points_spent': cost,
            'status': VOUCHER_ACTIVE,
            'expires_at': now + timedelta(days=expiry_days),
            'used_at': None,
            'used_on_sale_id': None,
            'created_at': now,
        })

        tx.set(_txn_ref(f'redeem_{code}'), {
            'customer_id': str(customer_id),
            'type': TX_REDEEM,
            'points': -cost,
            'balance_after': new_balance,
            'reward_id': str(reward_id),
            'note': f"Redeemed {reward.name}",
            'created_by': actor_id,
            'created_at': now,
        })

    current_app.logger.info(
        'loyalty: customer %s redeemed reward %s for %d point(s), voucher %s',
        customer_id, reward_id, cost, code)

    notify_reward_redeemed(customer_id, reward.name, cost, code, new_balance)
    return RewardRedemption.get(code)


# ---------------------------------------------------------------------------
# Vouchers
# ---------------------------------------------------------------------------
def voucher_status_now(redemption, now=None):
    """Effective status, accounting for expiry.

    Stored 'active' is not the whole truth: a voucher past expires_at is
    reported expired without being rewritten, so listing vouchers stays a
    read, and a voucher that gets un-used would still show correctly.
    """
    now = now or datetime.utcnow()
    if redemption.status in (VOUCHER_USED, VOUCHER_CANCELLED):
        return redemption.status
    if redemption.expires_at and redemption.expires_at <= now:
        return 'expired'
    return redemption.status or VOUCHER_ACTIVE


def validate_voucher(code, sale_total=None):
    """Check a voucher code. Returns a dict describing validity and value.

    `sale_total` (optional) is used to report whether the voucher's value
    exceeds the sale it's being applied to.
    """
    code = (code or '').strip().upper()
    result = {
        'voucher_code': code,
        'valid': False,
        'status': None,
        'reason': None,
        'reward_id': None,
        'reward_name': None,
        'reward_type': None,
        'discount_amount': 0.0,
        'points_spent': 0,
        'expires_at': None,
        'customer_id': None,
    }
    if not code:
        result['reason'] = 'Enter a voucher code'
        return result

    redemption = RewardRedemption.get(code)
    if not redemption:
        result['reason'] = 'Voucher code not found'
        return result

    reward = Reward.get(redemption.reward_id) if redemption.reward_id else None
    status = voucher_status_now(redemption)
    result.update({
        'status': status,
        'customer_id': redemption.customer_id,
        'reward_id': redemption.reward_id,
        'reward_name': redemption.reward_name or (reward.name if reward else None),
        'reward_type': reward.reward_type if reward else None,
        'points_spent': redemption.points_spent,
        'expires_at': redemption.expires_at.isoformat()
        if hasattr(redemption.expires_at, 'isoformat') else redemption.expires_at,
    })

    if status == VOUCHER_USED:
        result['reason'] = 'This voucher has already been used'
        return result
    if status == 'expired':
        result['reason'] = 'This voucher has expired'
        return result
    if status == VOUCHER_CANCELLED:
        result['reason'] = 'This voucher has been cancelled'
        return result

    if reward and reward.reward_type == REWARD_DISCOUNT:
        try:
            result['discount_amount'] = float(reward.value or 0)
        except (TypeError, ValueError):
            result['discount_amount'] = 0.0
        if sale_total is not None and result['discount_amount'] > float(sale_total):
            result['reason'] = 'Voucher value is greater than the sale total'
            return result

    result['valid'] = True
    return result


def apply_voucher_to_sale(sale, code, identity=None):
    """Apply a voucher to a sale as a discount, marking it used atomically.

    The sale's discount/total update and the voucher's used-marking happen in
    one transaction: a voucher must never end up used on a sale that failed
    to save, or left active on a sale that did.

    Returns (sale, validation_result).
    """
    settings = get_settings()
    code = (code or '').strip().upper()
    validation = validate_voucher(code)
    if not validation['valid']:
        raise LoyaltyError(validation['reason'] or 'Voucher is not valid', 400)

    redemption_ref = _redemption_ref(code)
    sale_ref = get_db().collection('sales').document(str(sale.id))
    now = datetime.utcnow()
    actor_id = (identity or {}).get('id')
    discount_amount = validation['discount_amount']
    sale_id = sale.id

    with get_db().transaction() as tx:
        redemption_snap = tx.get(redemption_ref)
        if not redemption_snap.exists:
            raise LoyaltyError('Voucher code not found', 404)
        redemption = RewardRedemption(id=redemption_snap.id, **(redemption_snap.to_dict() or {}))

        # Re-check inside the transaction. validate_voucher() above read the
        # voucher outside it, so between the two reads another cashier could
        # have applied the same code to a different sale.
        status = voucher_status_now(redemption, now)
        if status != VOUCHER_ACTIVE:
            raise LoyaltyError(
                'This voucher has already been used' if status == VOUCHER_USED
                else f'This voucher is {status}', 409)
        if redemption.used_on_sale_id and str(redemption.used_on_sale_id) == str(sale_id):
            raise LoyaltyError('This voucher is already applied to this sale', 409)

        sale_snap = tx.get(sale_ref)
        if not sale_snap.exists:
            raise LoyaltyError('Sale not found', 404)
        sale_data = sale_snap.to_dict() or {}

        existing_voucher = sale_data.get('voucher_code')
        if existing_voucher and str(existing_voucher) != code:
            # Replace rather than stack: two vouchers on one sale is not a
            # thing the rules describe, and silently summing them would make
            # the earning base wrong.
            raise LoyaltyError(
                f'This sale already has voucher {existing_voucher} applied; remove it first', 409)

        subtotal = float(sale_data.get('subtotal') or 0)
        tax = float(sale_data.get('tax') or 0)
        discount = float(sale_data.get('discount') or 0) + discount_amount
        total = round(max(0.0, subtotal + tax - discount), 2)

        tx.set(sale_ref, {
            'discount': discount,
            'voucher_discount': discount_amount,
            'voucher_code': code,
            'total': total,
            'updated_at': now,
        }, merge=True)

        tx.set(redemption_ref, {
            'status': VOUCHER_USED,
            'used_at': now,
            'used_on_sale_id': str(sale_id),
        }, merge=True)

    # Reflect the transaction's writes on the in-memory object the caller
    # still holds, so its subsequent db.session.commit() (see
    # routes/sales.py) can't write the pre-voucher values back over ours.
    sale.discount = float(sale.discount or 0) + discount_amount
    sale.voucher_discount = discount_amount
    sale.voucher_code = code
    sale.total = round(max(0.0, float(sale.subtotal or 0) + float(sale.tax or 0) - sale.discount), 2)
    sale.updated_at = now

    current_app.logger.info(
        'loyalty: voucher %s applied to sale %s by %s (%.2f off)',
        code, sale_id, actor_id, discount_amount)

    if identity:
        from utils.audit import log_audit
        log_audit(identity, 'update', 'sale', sale_id,
                  f'Applied voucher {code} (PHP {discount_amount:,.2f} off)')

    return sale, validation


def remove_voucher_from_sale(sale, identity=None):
    """Take a voucher back off a sale, returning it to active.

    Only valid while the sale isn't cancelled -- a voucher on a dead sale
    should be handled by the refund path, not quietly released.
    """
    code = (sale.voucher_code or '').strip().upper()
    if not code:
        raise LoyaltyError('This sale has no voucher applied')
    if sale.is_cancelled or sale.payment_status == 'Refunded':
        raise LoyaltyError('This sale is cancelled or refunded; its voucher cannot be released', 409)

    redemption_ref = _redemption_ref(code)
    now = datetime.utcnow()
    sale_ref = get_db().collection('sales').document(str(sale.id))
    discount_amount = float(sale.voucher_discount or 0)

    with get_db().transaction() as tx:
        redemption_snap = tx.get(redemption_ref)
        if not redemption_snap.exists:
            raise LoyaltyError('Voucher code not found', 404)
        redemption = RewardRedemption(id=redemption_snap.id, **(redemption_snap.to_dict() or {}))
        if redemption.status != VOUCHER_USED or str(redemption.used_on_sale_id) != str(sale.id):
            raise LoyaltyError('This voucher is not applied to this sale', 409)

        sale_snap = tx.get(sale_ref)
        sale_data = sale_snap.to_dict() or {}
        subtotal = float(sale_data.get('subtotal') or 0)
        tax = float(sale_data.get('tax') or 0)
        discount = max(0.0, float(sale_data.get('discount') or 0) - discount_amount)
        total = round(max(0.0, subtotal + tax - discount), 2)

        tx.set(sale_ref, {
            'discount': discount,
            'voucher_discount': 0.0,
            'voucher_code': None,
            'total': total,
            'updated_at': now,
        }, merge=True)
        tx.set(redemption_ref, {
            'status': VOUCHER_ACTIVE,
            'used_at': None,
            'used_on_sale_id': None,
        }, merge=True)

    sale.discount = max(0.0, float(sale.discount or 0) - discount_amount)
    sale.voucher_discount = 0.0
    sale.voucher_code = None
    sale.total = round(max(0.0, float(sale.subtotal or 0) + float(sale.tax or 0) - sale.discount), 2)
    sale.updated_at = now

    if identity:
        from utils.audit import log_audit
        log_audit(identity, 'update', 'sale', sale.id,
                  f'Removed voucher {code} from sale')
    return sale


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def user_for_customer(customer_id):
    """The login account for a customer, matched on email.

    Same linkage routes/appointments.py uses (see its get_vehicles): a
    Customer document has no user_id, and the account is matched by email.
    """
    from models.customer import Customer
    from models.user import User

    customer = Customer.get(customer_id)
    if not customer or not customer.email:
        return None
    return User.query.filter_by(email=customer.email).first()


def _push_to_customer(customer_id, title, body, data=None):
    """Best-effort push. Never raises -- see utils/push_utils.send_push."""
    try:
        user = user_for_customer(customer_id)
        if not user or not user.fcm_tokens:
            return
        from utils.push_utils import send_push, prune_invalid_tokens
        result = send_push(list(user.fcm_tokens), title, body, data=data)
        if isinstance(result, dict) and result.get('invalid_tokens'):
            prune_invalid_tokens(user, result['invalid_tokens'])
    except Exception as e:
        current_app.logger.warning('loyalty: push notification failed: %s', e)


def notify_points_earned(customer_id, points, balance, tier):
    _push_to_customer(
        customer_id,
        f'+{points} points earned',
        f'Thanks for your payment! You now have {balance} points ({tier} tier).',
        {'type': 'loyalty_points_earned', 'points': str(points), 'balance': str(balance)},
    )


def notify_reward_redeemed(customer_id, reward_name, points_spent, code, balance):
    _push_to_customer(
        customer_id,
        'Reward redeemed',
        f'"{reward_name}" redeemed for {points_spent} points. '
        f'Your code is {code}. Balance: {balance} points.',
        {'type': 'loyalty_reward_redeemed', 'voucher_code': code},
    )


def notify_voucher_expiring(redemption):
    _push_to_customer(
        redemption.customer_id,
        'Voucher expiring soon',
        f'Your voucher {redemption.voucher_code} expires soon. '
        f'Use it on your next visit.',
        {'type': 'loyalty_voucher_expiring', 'voucher_code': redemption.voucher_code},
    )


def notify_expiring_vouchers():
    """Push a warning for vouchers expiring within the warning window.

    Deliberately not scheduled: it runs off the customer-facing read that
    already happens constantly (GET /api/loyalty/me/vouchers), so no new
    infrastructure is needed. Each redemption is marked once it has been
    warned about, so a voucher doesn't get a push on every page load.
    """
    settings = get_settings()
    warning_days = int(settings.get('voucher_expiry_warning_days') or 0)
    if warning_days <= 0:
        return 0

    now = datetime.utcnow()
    cutoff = now + timedelta(days=warning_days)
    warned = 0
    for redemption in RewardRedemption.query.all():
        if redemption.status != VOUCHER_ACTIVE:
            continue
        if not redemption.expires_at:
            continue
        if redemption.expires_at > cutoff or redemption.expires_at <= now:
            continue
        if getattr(redemption, 'expiry_notified_at', None):
            continue
        try:
            notify_voucher_expiring(redemption)
            redemption.expiry_notified_at = now
            redemption.save(merge=True)
            warned += 1
        except Exception as e:
            current_app.logger.warning(
                'loyalty: expiry notification failed for %s: %s', redemption.voucher_code, e)
    return warned


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def get_account(customer_id, settings=None):
    """The customer's account, creating a zeroed one on first read.

    Lazy expiry is evaluated here, on read, rather than by a scheduler.
    """
    settings = settings or get_settings()
    try:
        apply_expiring_points(customer_id, settings)
    except Exception as e:
        current_app.logger.exception('loyalty: expiry sweep failed for %s: %s', customer_id, e)

    account = LoyaltyAccount.get(str(customer_id))
    if account is None:
        now = datetime.utcnow()
        account = LoyaltyAccount(
            id=str(customer_id),
            customer_id=str(customer_id),
            points_balance=0,
            lifetime_points=0,
            tier=TIER_BRONZE,
            created_at=now,
            updated_at=now,
        )
        account.save(merge=True)

    result = account.to_dict()
    result['tier_progress'] = tier_progress(account.lifetime_points, settings)
    return result


def list_transactions(customer_id, limit=20, offset=0, types=None):
    """Newest-first page of a customer's ledger."""
    query = LoyaltyTransaction.query.filter_by(customer_id=str(customer_id))
    if types:
        query = query.filter_by(type=types)
    rows = sorted(query.all(), key=lambda t: t.created_at or datetime.min, reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit] if limit else rows[offset:]
    return {
        'transactions': [t.to_dict() for t in page],
        'total': total,
        'limit': limit,
        'offset': offset,
        'has_more': offset + len(page) < total,
    }


def list_vouchers(customer_id, include_expired=True):
    now = datetime.utcnow()
    rows = RewardRedemption.query.filter_by(customer_id=str(customer_id)).all()
    rows.sort(key=lambda r: r.created_at or datetime.min, reverse=True)
    out = []
    for row in rows:
        effective = voucher_status_now(row, now)
        if not include_expired and effective != VOUCHER_ACTIVE:
            continue
        data = row.to_dict()
        data['status'] = effective
        out.append(data)
    return out
