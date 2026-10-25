from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from models import db
from models.job_order import JobOrder
from models.sale import Sale, SaleItem
from models.service import Service
from utils.audit import log_audit
from utils.loyalty_service import (
    LoyaltyError, apply_voucher_to_sale, award_points_for_sale, reverse_points_for_sale,
)

sales_bp = Blueprint('sales', __name__)


def _non_negative_number(value, field_name, required=True):
    """Coerce a request field to a non-negative float, matching the same
    validation gap fixed in routes/inventory.py -- money fields here
    (tax, discount, amount_paid) had no validation at all, so a negative
    or non-numeric value would either corrupt a sale's total silently or
    crash inside Sale.calculate_total(). Returns (value, error_message)."""
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


def generate_invoice_number():
    """Generate unique invoice number."""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'INV-{today}-'

    todays_sales = [
        s for s in Sale.query.all()
        if s.invoice_number and s.invoice_number.startswith(prefix)
    ]
    if todays_sales:
        last_num = max(int(s.invoice_number.split('-')[-1]) for s in todays_sales)
        new_num = last_num + 1
    else:
        new_num = 1

    return f"{prefix}{new_num:04d}"


def _apply_voucher(sale, voucher_code, identity):
    """Apply a loyalty voucher to a just-written sale, if a code was given.

    Returns an (error_message) tuple on failure so the caller can turn it
    into a 400/409 without duplicating the try/except at both call sites.
    """
    if not voucher_code:
        return None
    try:
        apply_voucher_to_sale(sale, voucher_code, identity)
    except LoyaltyError as e:
        return e.message
    except Exception as e:
        current_app.logger.exception('_apply_voucher %s: %s', sale.id, e)
        return 'Could not apply the voucher code, please try again'
    return None


def _sync_points_on_payment(sale, identity, was_paid):
    """Award loyalty points when a sale becomes fully paid.

    Wrapped so a loyalty failure can never block the sale: the points rules
    are still pending adviser approval, and a bug or a Firestore hiccup in
    this module must not stop a customer from paying and their invoice from
    being recorded. Log the failure, return the sale.

    award_points_for_sale is itself idempotent per sale, so re-running this
    (a second payment call, a re-save) is a no-op rather than a double award.
    """
    if sale.payment_status != 'Paid' or was_paid:
        return
    try:
        award_points_for_sale(sale, identity)
    except Exception as e:
        current_app.logger.exception(
            'loyalty: failed to award points for sale %s (sale was saved): %s', sale.id, e)


def _sync_points_on_refund(sale, identity, was_refunded, reason):
    """Reverse loyalty points when a sale is refunded or deleted.

    Same never-block-the-sale contract as _sync_points_on_payment.
    """
    if sale.payment_status != 'Refunded' or was_refunded:
        return
    try:
        reverse_points_for_sale(sale, reason, identity)
    except Exception as e:
        current_app.logger.exception(
            'loyalty: failed to reverse points for sale %s (sale was saved): %s', sale.id, e)


@sales_bp.route('', methods=['GET'])
@jwt_required()
def get_sales():
    """Get all sales."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role == 'customer':
        return jsonify({'error': 'Customers cannot view all sales'}), 403
    
    status = request.args.get('payment_status')
    
    query = Sale.query
    if status:
        query = query.filter_by(payment_status=status)
    
    sales = query.order_by(Sale.created_at.desc()).all()
    Sale.prefetch(sales)

    result = []
    for sale in sales:
        try:
            result.append(sale.to_dict())
        except Exception as e:
            current_app.logger.exception('Failed to serialize sale %s: %s', sale.id, e)
            continue
    
    return jsonify(result), 200


@sales_bp.route('/<sale_id>', methods=['GET'])
@jwt_required()
def get_sale(sale_id):
    """Get a specific sale."""
    sale = Sale.query.get(sale_id)
    if not sale:
        return jsonify({'error': 'Sale not found'}), 404
    
    return jsonify(sale.to_dict()), 200


@sales_bp.route('', methods=['POST'])
@jwt_required()
def create_sale():
    """Create a sale from a completed job order."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    job_order_id = data.get('job_order_id')
    
    if not job_order_id:
        return jsonify({'error': 'Job order ID is required'}), 400
    
    # Verify job order exists and is completed
    job_order = JobOrder.query.get(job_order_id)
    if not job_order:
        return jsonify({'error': 'Job order not found'}), 404
    
    if job_order.status != 'Completed':
        return jsonify({'error': 'Can only create sale from completed job orders'}), 400
    
    # Check if sale already exists for this job order
    existing_sale = Sale.query.filter_by(job_order_id=job_order_id).first()
    if existing_sale:
        return jsonify({'error': 'Sale already exists for this job order'}), 400
    
    tax, err = _non_negative_number(data.get('tax', 0.0), 'tax')
    if err:
        return jsonify({'error': err}), 400
    discount, err = _non_negative_number(data.get('discount', 0.0), 'discount')
    if err:
        return jsonify({'error': err}), 400

    # Create sale
    sale = Sale(
        job_order_id=job_order_id,
        invoice_number=generate_invoice_number(),
        subtotal=job_order.parts_cost + job_order.labor_cost,
        tax=tax,
        discount=discount,
        total=job_order.total_cost,
        payment_status='Pending'
    )

    # Recalculate with tax and discount
    sale.total = sale.subtotal + sale.tax - sale.discount

    db.session.add(sale)
    db.session.commit()  # Get sale ID

    # Add parts from job order as sale items
    for item in job_order.items:
        sale_item = SaleItem(
            sale_id=sale.id,
            item_name=item.inventory_item.name,
            item_type='Part',
            quantity=item.quantity_used,
            unit_price=item.unit_price
        )
        db.session.add(sale_item)

    # Add services from job order
    services = [s for s in (Service.get(sid) for sid in (job_order.service_ids or [])) if s]

    for service in services:
        sale_item = SaleItem(
            sale_id=sale.id,
            item_name=service.name,
            item_type='Service',
            quantity=1,
            unit_price=service.price
        )
        db.session.add(sale_item)

    db.session.commit()

    # Optional loyalty voucher. Applied after the sale exists (it needs a
    # document id and a subtotal to compute the discounted total), and before
    # the response so a rejected code comes back as a clean 400/409 rather
    # than a sale that mysteriously didn't get its discount.
    voucher_error = _apply_voucher(sale, data.get('voucher_code'), identity)
    if voucher_error:
        return jsonify({'error': voucher_error}), 400
    if data.get('voucher_code'):
        db.session.commit()

    log_audit(identity, 'create', 'sale', sale.id, f"Created sale {sale.invoice_number} for job order {job_order_id}")

    return jsonify({
        'message': 'Sale created successfully',
        'sale': sale.to_dict()
    }), 201


@sales_bp.route('/<sale_id>', methods=['PUT'])
@jwt_required()
def update_sale(sale_id):
    """Update a sale (payment info)."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    sale = Sale.query.get(sale_id)
    if not sale:
        return jsonify({'error': 'Sale not found'}), 404
    
    data = request.get_json()

    # Snapshot the payment state before anything is changed: awarding points
    # and reversing them are both transitions, and each is keyed to the
    # transition happening, not to the sale merely being Paid/Refunded.
    was_paid = sale.payment_status == 'Paid'
    was_refunded = sale.payment_status == 'Refunded'

    VALID_PAYMENT_STATUSES = {'Pending', 'Partial', 'Paid', 'Refunded'}
    if 'payment_status' in data:
        if data['payment_status'] not in VALID_PAYMENT_STATUSES:
            return jsonify({'error': f"payment_status must be one of {sorted(VALID_PAYMENT_STATUSES)}"}), 400
        sale.payment_status = data['payment_status']

    if 'payment_method' in data:
        sale.payment_method = data['payment_method']

    if 'amount_paid' in data:
        amount_paid, err = _non_negative_number(data['amount_paid'], 'amount_paid')
        if err:
            return jsonify({'error': err}), 400
        sale.amount_paid = amount_paid
        # Auto-update payment status
        if sale.amount_paid >= sale.total:
            sale.payment_status = 'Paid'
        elif sale.amount_paid > 0:
            sale.payment_status = 'Partial'

    if 'tax' in data:
        tax, err = _non_negative_number(data['tax'], 'tax')
        if err:
            return jsonify({'error': err}), 400
        sale.tax = tax
        sale.calculate_total()

    if 'discount' in data:
        discount, err = _non_negative_number(data['discount'], 'discount')
        if err:
            return jsonify({'error': err}), 400
        sale.discount = discount
        sale.calculate_total()
    
    if 'notes' in data:
        sale.notes = data['notes']

    if 'voucher_code' in data:
        voucher_error = _apply_voucher(sale, data.get('voucher_code'), identity)
        if voucher_error:
            return jsonify({'error': voucher_error}), 400

    db.session.commit()

    log_audit(identity, 'update', 'sale', sale.id, f"Updated sale {sale.invoice_number}")

    _sync_points_on_payment(sale, identity, was_paid)
    _sync_points_on_refund(sale, identity, was_refunded, 'Sale marked Refunded')

    return jsonify({
        'message': 'Sale updated successfully',
        'sale': sale.to_dict()
    }), 200


@sales_bp.route('/<sale_id>/payment', methods=['POST'])
@jwt_required()
def record_payment(sale_id):
    """Record a payment for a sale. Admin/staff only — there is no real
    payment gateway behind this, it just trusts whatever amount is sent, so
    customers must never be able to call it directly (they'd be able to
    mark their own invoice Paid without actually paying)."""
    identity = get_jwt_identity()
    role = identity.get('role')

    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    sale = Sale.query.get(sale_id)
    if not sale:
        return jsonify({'error': 'Sale not found'}), 404
    
    data = request.get_json()
    payment_method = data.get('payment_method', 'Cash')
    reference_number = data.get('reference_number', '')
    notes = data.get('notes', '')

    was_paid = sale.payment_status == 'Paid'

    amount, err = _non_negative_number(data.get('amount'), 'amount')
    if err or not amount:
        return jsonify({'error': 'Valid payment amount is required'}), 400
    
    sale.amount_paid += amount
    sale.payment_method = payment_method
    
    # Update payment notes
    if notes:
        sale.notes = (sale.notes or '') + '\n' + f"Payment: {payment_method} - {notes}" if sale.notes else f"Payment: {payment_method} - {notes}"
    
    # Update payment status
    if sale.amount_paid >= sale.total:
        sale.payment_status = 'Paid'
    elif sale.amount_paid > 0:
        sale.payment_status = 'Partial'

    db.session.commit()

    log_audit(identity, 'update', 'sale', sale.id, f"Recorded payment of {amount} on sale {sale.invoice_number} via {payment_method}")

    # Points are earned on payment, not on completion -- a completed job
    # order the customer hasn't paid for is work in progress, not a reason to
    # hand out points. Idempotent per sale, and after the commit so a
    # loyalty failure can't cost us the payment record.
    _sync_points_on_payment(sale, identity, was_paid)

    # Send SMS notification to customer
    if sale.payment_status == 'Paid':
        try:
            from utils.sms_utils import send_sms
            job_order = sale.job_order
            if job_order and job_order.vehicle:
                customer = job_order.vehicle.customer
                if customer and hasattr(customer, 'phone') and customer.phone:
                    message = f"Thank you for your payment! Invoice #{sale.invoice_number} has been paid in full. Your vehicle is ready for pickup at Automan Car Care Center."
                    send_sms(customer.phone, message)
        except Exception as e:
            current_app.logger.exception('record_payment %s: failed to send payment SMS: %s', sale.id, e)
    
    return jsonify({
        'message': 'Payment recorded successfully',
        'sale': sale.to_dict()
    }), 200


@sales_bp.route('/<sale_id>/send-payment-link', methods=['POST'])
@jwt_required()
def send_payment_link(sale_id):
    """Send payment link to customer via SMS."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    sale = Sale.query.get(sale_id)
    if not sale:
        return jsonify({'error': 'Sale not found'}), 404
    
    # Get customer info
    job_order = sale.job_order
    if not job_order or not job_order.vehicle:
        return jsonify({'error': 'Customer not found'}), 404
    
    customer = job_order.vehicle.customer
    if not customer or not hasattr(customer, 'phone') or not customer.phone:
        return jsonify({'error': 'Customer phone not found'}), 404
    
    # Send SMS with payment link
    try:
        from utils.sms_utils import send_sms
        
        message = f"Hi {customer.name}! Your service at Automan Car Care is complete.\n\n"
        message += f"Invoice: {sale.invoice_number}\n"
        message += f"Total: PHP {sale.total:,.2f}\n\n"
        message += f"To pay, please visit our shop or contact us."
        
        result = send_sms(customer.phone, message)
        # send_sms never raises on a provider-side rejection — it returns
        # {'success': False, ...} instead — so the old code marked every
        # sale "Notified" (and told the customer it worked) even when the
        # SMS never actually went out. Check the result before doing either.
        if not isinstance(result, dict) or not result.get('success'):
            error_detail = (result or {}).get('error') if isinstance(result, dict) else None
            current_app.logger.warning('send_payment_link %s: SMS failed: %s', sale.id, error_detail)
            return jsonify({'error': f'Failed to send SMS: {error_detail}' if error_detail else 'Failed to send SMS'}), 502

        # Update status to Notified so button is removed
        sale.payment_status = 'Notified'
        db.session.commit()
        current_app.logger.info('send_payment_link %s: sent, status set to Notified', sale.id)

        return jsonify({'message': 'Payment link sent successfully'}), 200
    except Exception as e:
        current_app.logger.exception('send_payment_link %s: error sending payment link: %s', sale_id, e)
        return jsonify({'error': 'Failed to send payment link'}), 500


@sales_bp.route('/<sale_id>', methods=['DELETE'])
@jwt_required()
def delete_sale(sale_id):
    """Delete a sale (admin only)."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    
    sale = Sale.query.get(sale_id)
    if not sale:
        return jsonify({'error': 'Sale not found'}), 404
    
    sale.is_deleted = True
    sale.deleted_at = datetime.utcnow()
    sale.deleted_reason = 'Soft deleted via API'
    db.session.commit()

    # A deleted sale is money the shop never collected, so the points it
    # earned have to go back. Idempotent (reversal_<sale_id>) and clamped at
    # a zero balance. Any voucher spent on this sale is deliberately left
    # used -- restoring it is a judgement call for staff via
    # POST /api/loyalty/admin/adjust, with a reason.
    try:
        reverse_points_for_sale(sale, 'Sale deleted by admin', identity)
    except Exception as e:
        current_app.logger.exception(
            'loyalty: failed to reverse points for deleted sale %s: %s', sale.id, e)

    log_audit(identity, 'delete', 'sale', sale.id, f"Deleted sale {sale.invoice_number}")

    return jsonify({'message': 'Sale deleted successfully'}), 200
