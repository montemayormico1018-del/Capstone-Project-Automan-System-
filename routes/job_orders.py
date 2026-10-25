from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from models import db
from models.appointment import Appointment
from models.job_order import JobOrder
from models.job_order_item import JobOrderItem
from models.inventory import InventoryItem
from models.enhanced_models import InventoryMovement
from models.user import User
from utils.audit import log_audit

job_orders_bp = Blueprint('job_orders', __name__)

# Valid status transitions per role
# Mechanic (own job): Pending -> In Progress -> Parts Requested -> Parts Released -> Completed
# Staff/Admin:        any status transition
# Stockman:           Parts Requested -> Parts Released ONLY
VALID_TRANSITIONS = {
    'admin':    ['Pending', 'In Progress', 'Parts Requested', 'Parts Released', 'Completed', 'Cancelled'],
    'staff':    ['Pending', 'In Progress', 'Parts Requested', 'Parts Released', 'Completed', 'Cancelled'],
    'mechanic': ['In Progress', 'Parts Requested', 'Completed'],   # own job only, enforced separately
    'stockman': ['Parts Released'],                                  # can only release parts
}


def generate_job_order_number():
    """Generate unique job order number."""
    today = datetime.now().strftime('%Y%m%d')
    prefix = f'JO-{today}-'

    todays_orders = [
        jo for jo in JobOrder.query.all()
        if jo.job_order_number and jo.job_order_number.startswith(prefix)
    ]
    if todays_orders:
        last_num = max(int(jo.job_order_number.split('-')[-1]) for jo in todays_orders)
        new_num = last_num + 1
    else:
        new_num = 1

    return f"{prefix}{new_num:04d}"


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


def _capped_text(value, max_length, field_label):
    """Length-cap a free-text field (diagnosis, notes, etc). These are
    legitimately free text -- no character-set restriction -- but still need
    a bound so one bad request can't write an arbitrarily large document.
    Returns (cleaned_value, error) -- error is None when value is missing/None
    (these fields are all optional) or within bounds."""
    if value is None:
        return value, None
    text = str(value)
    if len(text) > max_length:
        return None, f'{field_label} must be {max_length} characters or fewer'
    return text, None


def _non_negative_number(value, field_name, required=True):
    """Same validation gap fixed in routes/inventory.py and routes/sales.py:
    labor_cost fed straight into float(labor_cost) with no bound, so a
    negative value would silently corrupt the job order's total, and a
    non-numeric one would crash with an unhandled ValueError. Returns
    (value, error_message)."""
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


@job_orders_bp.route('', methods=['GET'])
@jwt_required()
def get_job_orders():
    """Get job orders — filtered by role."""
    try:
        identity = get_identity()
        role    = identity.get('role')
        user_id = identity.get('id')
        if user_id is not None:
            try:
                user_id = user_id
            except (ValueError, TypeError):
                user_id = None

        # Build base query
        query = JobOrder.query.filter_by(is_deleted=False)
        job_orders = None  # set directly below for the chunked-IN customer case; every other role still uses `query` + .all() further down

        # Mechanic: only their assigned orders
        if role == 'mechanic':
            query = query.filter_by(user_id=user_id)
        # Stockman: only orders that need parts releasing
        elif role == 'stockman':
            query = query.filter(JobOrder.status.in_(['Parts Requested', 'Parts Released']))
        # Customer: orders linked to their appointments
        elif role == 'customer':
            from models.customer import Customer
            user = User.query.get(user_id)
            if user:
                customer = Customer.query.filter_by(email=user.email).first()
                if customer:
                    appt_ids = [a.id for a in customer.appointments]
                    if not appt_ids:
                        job_orders = []
                    elif len(appt_ids) <= 30:
                        query = query.filter(JobOrder.appointment_id.in_(appt_ids))
                    else:
                        # Firestore's IN operator caps at 30 comparison
                        # values -- a customer with more appointments than
                        # that 400s the whole query (this is exactly what
                        # happened live: 'IN' supports up to 30 comparison
                        # values). Same chunk-and-merge pattern already used
                        # by Customer.prefetch() for the same limit.
                        seen = {}
                        for i in range(0, len(appt_ids), 30):
                            chunk = appt_ids[i:i + 30]
                            for jo in JobOrder.query.filter_by(is_deleted=False).filter(JobOrder.appointment_id.in_(chunk)).all():
                                seen[jo.id] = jo
                        job_orders = list(seen.values())
                else:
                    return jsonify([]), 200
        # Admin / Staff: see all
        # (no additional filter)

        # Optional filters
        status     = request.args.get('status')
        priority   = request.args.get('priority')
        vehicle_id = request.args.get('vehicle_id')
        if job_orders is None:
            if status:
                query = query.filter_by(status=status)
            if priority:
                query = query.filter_by(priority=priority)
            if vehicle_id:
                # Safe to apply after the role-based scoping above -- for a
                # customer this can only narrow an already-owned set of job
                # orders, never broaden it to another customer's vehicle.
                query = query.filter_by(vehicle_id=vehicle_id)
        else:
            # The chunked-IN customer case above already has its full,
            # deduped result set as a plain list -- apply the same optional
            # filters in Python instead of against a Query object.
            if status:
                job_orders = [jo for jo in job_orders if jo.status == status]
            if priority:
                job_orders = [jo for jo in job_orders if jo.priority == priority]
            if vehicle_id:
                job_orders = [jo for jo in job_orders if jo.vehicle_id == vehicle_id]

        # Sort in Python rather than Firestore .order_by() — combining it with
        # the is_deleted/status/priority filters above would need a composite
        # index that doesn't exist in this project (and 500s until it's built).
        # created_at can be a string (older records) or a Firestore
        # DatetimeWithNanoseconds (newer ones), so compare as ISO strings
        # rather than sorting the raw mixed-type values directly.
        def _created_at_key(jo):
            ca = jo.created_at
            if ca is None:
                return ''
            return ca.isoformat() if hasattr(ca, 'isoformat') else str(ca)

        if job_orders is None:
            job_orders = query.all()
        job_orders.sort(key=_created_at_key, reverse=True)
        JobOrder.prefetch(job_orders)

        result = []
        for jo in job_orders:
            try:
                result.append(jo.to_dict())
            except Exception as e:
                current_app.logger.exception('Failed to serialize job order %s: %s', jo.id, e)
                continue

        return jsonify(result), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@job_orders_bp.route('/<job_order_id>', methods=['GET'])
@jwt_required()
def get_job_order(job_order_id):
    """Get a specific job order."""
    identity = get_identity()
    role    = identity.get('role')
    user_id = identity.get('id')
    if user_id is not None:
        try:
            user_id = user_id
        except (ValueError, TypeError):
            user_id = None

    job_order = JobOrder.query.get(job_order_id)
    if not job_order or job_order.is_deleted:
        return jsonify({'error': 'Job order not found'}), 404

    # Mechanic: only see their own
    if role == 'mechanic' and job_order.user_id != user_id:
        return jsonify({'error': 'Unauthorized'}), 403

    # Calculate totals before returning
    job_order.calculate_totals()
    
    return jsonify(job_order.to_dict()), 200


@job_orders_bp.route('', methods=['POST'])
@jwt_required()
def create_job_order():
    """Create a new job order from an approved appointment. Admin/Staff only."""
    identity = get_identity()
    role = identity.get('role')

    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    data = request.get_json()

    appointment_id   = data.get('appointment_id')
    mechanic_id      = data.get('mechanic_id')
    diagnosis        = data.get('diagnosis', '')
    work_description = data.get('work_description', '')
    priority         = data.get('priority', 'Normal')
    notes            = data.get('notes', '')
    labor_cost       = data.get('labor_cost', 0.0)
    plate_number     = data.get('plate_number', '')
    mileage          = data.get('mileage')

    if not appointment_id:
        return jsonify({'error': 'Appointment ID is required'}), 400
    if not mechanic_id:
        return jsonify({'error': 'Mechanic assignment is required'}), 400

    diagnosis, err = _capped_text(diagnosis, 2000, 'Diagnosis')
    if err:
        return jsonify({'error': err}), 400
    work_description, err = _capped_text(work_description, 2000, 'Work description')
    if err:
        return jsonify({'error': err}), 400
    notes, err = _capped_text(notes, 1000, 'Notes')
    if err:
        return jsonify({'error': err}), 400
    labor_cost, err = _non_negative_number(labor_cost, 'labor_cost', required=False)
    if err:
        return jsonify({'error': err}), 400
    labor_cost = labor_cost or 0.0

    appointment = Appointment.query.get(appointment_id)
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    if appointment.status != 'Approved':
        return jsonify({'error': 'Only approved appointments can be converted to job orders'}), 400

    existing = JobOrder.query.filter_by(appointment_id=appointment_id).first()
    if existing:
        return jsonify({'error': 'Job order already exists for this appointment'}), 400

    # Recheck at submission time: the dashboard state could have changed
    # after staff opened this form.
    mechanic = User.query.get(mechanic_id)
    if not mechanic or mechanic.role != 'mechanic' or not mechanic.is_active:
        return jsonify({'error': 'Invalid mechanic selected'}), 400
    if mechanic.availability != 'available':
        return jsonify({'error': 'Selected mechanic is not available'}), 409

    job_order = JobOrder(
        appointment_id=appointment_id,
        vehicle_id=appointment.vehicle_id,
        user_id=mechanic_id,
        job_order_number=generate_job_order_number(),
        status='Pending',
        priority=priority,
        plate_number=plate_number or getattr(appointment, 'plate_number', '') or '',
        mileage=mileage,
        diagnosis=diagnosis,
        work_description=work_description,
        notes=notes,
        labor_cost=labor_cost
    )
    db.session.add(job_order)
    db.session.commit()

    log_audit(identity, 'create', 'job_order', job_order.id, f"Created job order '{job_order.job_order_number}'")

    return jsonify({
        'message': 'Job order created successfully',
        'job_order': job_order.to_dict()
    }), 201


@job_orders_bp.route('/<job_order_id>/additional-items', methods=['POST'])
@jwt_required()
def add_additional_item(job_order_id):
    """Persist a custom labor/part line item for irregular work in Firestore."""
    identity = get_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403
    job_order = JobOrder.get(job_order_id)
    if not job_order or job_order.is_deleted:
        return jsonify({'error': 'Job order not found'}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    item_type = data.get('type') or 'labor'
    try:
        quantity, unit_price = float(data.get('quantity', 1)), float(data.get('unit_price'))
        if not name or quantity <= 0 or unit_price < 0 or item_type not in ('labor', 'part', 'other'):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({'error': 'Name, valid type, positive quantity, and non-negative unit price are required'}), 400
    item = {'id': datetime.utcnow().strftime('%Y%m%d%H%M%S%f'), 'name': name, 'type': item_type,
            'quantity': quantity, 'unit_price': unit_price, 'notes': (data.get('notes') or '').strip()}
    job_order.additional_items.append(item)
    job_order.calculate_totals()
    db.session.commit()
    return jsonify({'message': 'Additional work item added', 'item': item, 'job_order': job_order.to_dict()}), 201


@job_orders_bp.route('/<job_order_id>', methods=['PUT'])
@jwt_required()
def update_job_order(job_order_id):
    """Update job order. Role-based field access enforced here."""
    identity = get_identity()
    role    = identity.get('role')
    user_id = identity.get('id')
    if user_id is not None:
        try:
            user_id = user_id
        except (ValueError, TypeError):
            user_id = None

    job_order = JobOrder.query.get(job_order_id)
    if not job_order or job_order.is_deleted:
        return jsonify({'error': 'Job order not found'}), 404

    data = request.get_json()

    # ── VALIDATION (before any mutation) ───────────────────────────
    for field, label, max_length in (
        ('diagnosis', 'Diagnosis', 2000),
        ('work_description', 'Work description', 2000),
        ('recommendations', 'Recommendations', 2000),
        ('mechanic_notes', 'Mechanic notes', 2000),
        ('notes', 'Notes', 1000),
    ):
        if field in data:
            data[field], err = _capped_text(data[field], max_length, label)
            if err:
                return jsonify({'error': err}), 400

    if 'labor_cost' in data:
        data['labor_cost'], err = _non_negative_number(data['labor_cost'], 'labor_cost')
        if err:
            return jsonify({'error': err}), 400

    # ── AUTHORIZATION ──────────────────────────────────────────────
    if role == 'mechanic':
        # Mechanic: only their own job
        if job_order.user_id != user_id:
            return jsonify({'error': 'Unauthorized: Not assigned to this job order'}), 403
        # Mechanic CAN edit their own job even when Parts Requested (can update labor, diagnosis, notes, etc.)
        # They just cannot change status to Completed until parts are released

    elif role == 'stockman':
        # Stockman: ONLY allowed to change status to Parts Released
        if 'status' not in data or data['status'] != 'Parts Released':
            return jsonify({'error': 'Stockman can only release parts (set status to Parts Released)'}), 403
        if job_order.status != 'Parts Requested':
            return jsonify({'error': 'Can only release parts when status is Parts Requested'}), 400

    elif role == 'customer':
        return jsonify({'error': 'Unauthorized'}), 403

    elif role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403

    # ── STATUS TRANSITIONS ────────────────────────────────────────
    status_change_desc = None
    if 'status' in data:
        new_status = data['status']
        old_status = job_order.status
        status_change_desc = f"Status changed from '{old_status}' to '{new_status}'"
        
        current_app.logger.debug(
            'update_job_order %s: role=%s old_status=%s new_status=%s',
            job_order_id, role, old_status, new_status
        )

        # Validate allowed statuses for role
        allowed = VALID_TRANSITIONS.get(role, [])
        if new_status not in allowed:
            return jsonify({'error': f'Role {role} cannot set status to {new_status}. Allowed: {allowed}'}), 403

        # Mechanic: cannot complete if still "Parts Requested"
        if role == 'mechanic' and new_status == 'Completed' and old_status == 'Parts Requested':
            return jsonify({'error': 'Parts must be released by stockman before completing'}), 400

        # Mechanic: can only request parts when In Progress or Parts Released
        if role == 'mechanic' and new_status == 'Parts Requested':
            if old_status not in ['In Progress', 'Parts Released']:
                return jsonify({'error': 'Can only request parts when job is In Progress or Parts Released'}), 400

        # Completion gate: this used to only be enforced in the browser
        # (job_orders.html's changeStatus()), so a direct API call could mark
        # a job Completed with checklist items / services still unchecked.
        # Enforce it here too, for every role, using the same progress the
        # frontend already displays.
        if new_status == 'Completed':
            services_completed, services_total = job_order.get_services_progress()
            package_completed, package_total = job_order.get_package_progress()
            if services_total > 0 and services_completed < services_total:
                return jsonify({'error': 'Cannot complete: not all services are marked done yet'}), 400
            if package_total > 0 and package_completed < package_total:
                return jsonify({'error': 'Cannot complete: not all package checklist items are marked done yet'}), 400

        # Apply the status change BEFORE running any side-effects below.
        # send_sms_notification() (called from the 'In Progress' branch)
        # commits the session internally to log the SMS attempt — if that
        # fires while job_order.status still held its OLD value, it would
        # persist the old status and clear the pending-write list, leaving
        # nothing left to save the real change (the bug that caused "Start"
        # to report success while the status silently stayed Pending).
        job_order.status = new_status
        db.session.flush()

        # Handle side-effects per transition (wrapped in try-except for robustness)
        try:
            if new_status == 'In Progress' and old_status == 'Pending':
                job_order.started_at = datetime.utcnow()
                appt = job_order.appointment
                if appt:
                    appt.status = 'In Progress'

                try:
                    from utils.sms_utils import send_sms_notification
                    from utils.push_utils import send_push, prune_invalid_tokens
                    from models.user import User
                    customer = None
                    if job_order.appointment and getattr(job_order.appointment, 'customer', None):
                        customer = job_order.appointment.customer
                    elif job_order.vehicle and getattr(job_order.vehicle, 'customer', None):
                        customer = job_order.vehicle.customer

                    if customer and getattr(customer, 'phone', None):
                        msg = (
                            f"Hi {customer.name}! Your vehicle service at Automan Car Care is now in progress. "
                            f"We will notify you when it is complete."
                        )
                        send_sms_notification(customer.phone, msg, job_order_id=job_order.id)

                    # Push, separately from SMS -- keyed by the logged-in
                    # User account, not the Customer record (Customer has no
                    # direct link to a User; they're matched by email, same
                    # pattern used everywhere else this codebase resolves
                    # one from the other).
                    if customer and getattr(customer, 'email', None):
                        account = User.find_by(email=customer.email)
                        if account and account.fcm_tokens:
                            result = send_push(
                                account.fcm_tokens,
                                title='Service Started',
                                body="Your vehicle service is now in progress. We'll notify you when it's complete.",
                                data={'type': 'job_order_started', 'job_order_id': job_order.id},
                            )
                            prune_invalid_tokens(account, result.get('invalid_tokens'))
                except Exception as e:
                    current_app.logger.exception('JO %s: failed to send start notification: %s', job_order.id, e)

                # send_sms_notification() commits internally and clears the
                # pending-write set — re-add the mutated appointment so the
                # final commit persists its new status.
                if appt:
                    db.session.add(appt)

            # STOCK DEDUCTION: Only when status changes to Parts Released
            if new_status == 'Parts Released' and old_status == 'Parts Requested':
                items = [item for item in job_order.items if not item.is_released]
                if not items or len(items) == 0:
                    current_app.logger.debug('JO %s: no unreleased items to process', job_order.id)
                else:
                    # First pass: validate ALL items have sufficient stock
                    for item in items:
                        inv_item = item.inventory_item
                        if not inv_item:
                            return jsonify({'error': f'Inventory item not found for item {item.id}'}), 400
                        if inv_item.stock < item.quantity_used:
                            return jsonify({
                                'error': f'Insufficient stock for {inv_item.name}. Available: {inv_item.stock}, Requested: {item.quantity_used}'
                            }), 400
                    # Second pass: deduct stock and mark as released
                    for item in items:
                        inv_item = item.inventory_item
                        try:
                            movement = InventoryMovement.record_usage(
                                inventory_item_id=inv_item.id,
                                quantity=item.quantity_used,
                                reference_type='job_order',
                                reference_id=job_order.id,
                                reason=f'Job Order #{job_order.id} - {job_order.vehicle.make} {job_order.vehicle.model}' if job_order.vehicle else f'Job Order #{job_order.id}',
                                user_id=identity.get('id')
                            )
                            item.is_released = True
                            item.released_at = datetime.utcnow()
                            current_app.logger.info(
                                'JO %s: recorded usage of %s x %s, remaining stock=%s',
                                job_order.id, item.quantity_used, inv_item.id, inv_item.stock
                            )
                        except ValueError as e:
                            current_app.logger.warning('JO %s: failed to record usage for item %s: %s', job_order.id, inv_item.id, e)
                            inv_item.deduct_stock(item.quantity_used)
                            item.is_released = True
                            item.released_at = datetime.utcnow()
                            current_app.logger.info(
                                'JO %s: fallback-deducted %s from item %s, remaining stock=%s',
                                job_order.id, item.quantity_used, inv_item.id, inv_item.stock
                            )

            if new_status == 'Completed':
                job_order.completed_at = datetime.utcnow()
                appt = job_order.appointment
                if appt:
                    appt.status = 'Completed'
                job_order.calculate_totals()
                try:
                    from utils.sms_utils import send_sms
                    from utils.push_utils import send_push, prune_invalid_tokens
                    from models.user import User
                    customer = job_order.vehicle.customer if job_order.vehicle else None
                    if customer and hasattr(customer, 'phone') and customer.phone:
                        msg = (f"Hi {customer.name}! Your vehicle service at Automan Car Care is complete. "
                               f"Total: PHP {job_order.total_cost:,.2f}. Please proceed to payment.")
                        send_sms(customer.phone, msg)

                    # Push, same customer-account resolution as the 'In
                    # Progress' branch above (Customer has no direct link to
                    # a User; matched by email).
                    if customer and getattr(customer, 'email', None):
                        account = User.find_by(email=customer.email)
                        if account and account.fcm_tokens:
                            result = send_push(
                                account.fcm_tokens,
                                title='Service Completed',
                                body=f"Your vehicle service is complete. Total: PHP {job_order.total_cost:,.2f}. Please proceed to payment.",
                                data={'type': 'job_order_completed', 'job_order_id': job_order.id},
                            )
                            prune_invalid_tokens(account, result.get('invalid_tokens'))
                except Exception as e:
                    current_app.logger.exception('JO %s: failed to send completion notification: %s', job_order.id, e)
                # Persist the appointment's Completed status through the final
                # commit — the appointment instance read here is not otherwise
                # in the pending-write set after the early flush above (this
                # was why "Completed" never lit up for the customer).
                if appt:
                    db.session.add(appt)

        except Exception as e:
            current_app.logger.exception('Status transition side-effects failed for JO %s: %s', job_order_id, e)
            # Continue anyway — status itself was already applied above,
            # before these side-effects ran, so a side-effect failure here
            # doesn't lose the actual status change.

    # ── FIELD UPDATES — ROLE-GATED ────────────────────────────────
    # Admin / Staff only fields
    if role in ['admin', 'staff', 'mechanic']:
        if 'priority' in data:
            job_order.priority = data['priority']
        if 'labor_cost' in data:
            job_order.labor_cost = data['labor_cost']
            job_order.calculate_totals()
        if 'notes' in data:
            job_order.notes = data['notes']
        if 'diagnosis' in data:
            job_order.diagnosis = data['diagnosis']
        if 'mileage' in data:
            job_order.mileage = data['mileage']
        if 'plate_number' in data:
            job_order.plate_number = data['plate_number']
        if 'mechanic_id' in data:
            job_order.user_id = data['mechanic_id']

    # Mechanic + Admin/Staff fields (own work notes)
    if role in ['admin', 'staff', 'mechanic']:
        if 'work_description' in data:
            job_order.work_description = data['work_description']
        if 'mechanic_notes' in data:
            job_order.mechanic_notes = data['mechanic_notes']
        if 'recommendations' in data:
            job_order.recommendations = data['recommendations']
        if 'completed_services' in data:
            job_order.completed_services = data['completed_services']
        if 'completed_package_items' in data:
            job_order.completed_package_items = data['completed_package_items']

    # Re-register job_order before the final commit: the early flush() above
    # (and any nested commit triggered by SMS logging inside the side-effect
    # block) clears the session's pending-write set, so mutations made after
    # that point (started_at/completed_at/calculate_totals(), the field
    # updates just above) would otherwise silently never get saved.
    db.session.add(job_order)
    db.session.commit()

    log_audit(identity, 'update', 'job_order', job_order.id, status_change_desc or f"Updated job order '{job_order.job_order_number}'")

    return jsonify({
        'message': 'Job order updated successfully',
        'job_order': job_order.to_dict()
    }), 200


@job_orders_bp.route('/<job_order_id>/services', methods=['PUT'])
@jwt_required()
def update_service_progress(job_order_id):
    """Update service completion status. Admin/Staff/Mechanic (own job)."""
    identity = get_identity()
    role    = identity.get('role')
    user_id = identity.get('id')
    if user_id is not None:
        try:
            user_id = user_id
        except (ValueError, TypeError):
            user_id = None

    job_order = JobOrder.query.get(job_order_id)
    if not job_order:
        return jsonify({'error': 'Job order not found'}), 404

    if role not in ['admin', 'staff', 'mechanic']:
        return jsonify({'error': 'Unauthorized'}), 403
    if role == 'mechanic' and job_order.user_id != user_id:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    if 'completed_services' in data:
        job_order.completed_services = data['completed_services']
        db.session.commit()

    return jsonify({
        'message': 'Service progress updated',
        'completed_services': job_order.completed_services
    }), 200


@job_orders_bp.route('/<job_order_id>/package-items', methods=['PUT'])
@jwt_required()
def update_package_item_progress(job_order_id):
    """Update package checklist-item completion. Admin/Staff/Mechanic (own job)."""
    identity = get_identity()
    role = identity.get('role')
    user_id = identity.get('id')

    job_order = JobOrder.query.get(job_order_id)
    if not job_order:
        return jsonify({'error': 'Job order not found'}), 404

    if role not in ['admin', 'staff', 'mechanic']:
        return jsonify({'error': 'Unauthorized'}), 403
    if role == 'mechanic' and job_order.user_id != user_id:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    if 'completed_package_items' in data:
        job_order.completed_package_items = data['completed_package_items']
        db.session.commit()

    return jsonify({
        'message': 'Package item progress updated',
        'completed_package_items': job_order.completed_package_items
    }), 200


# ── PARTS (Job Order Items) ───────────────────────────────────────────

@job_orders_bp.route('/<job_order_id>/items', methods=['GET'])
@jwt_required()
def get_job_order_items(job_order_id):
    """Get parts used in a job order."""
    job_order = JobOrder.query.get(job_order_id)
    if not job_order:
        return jsonify({'error': 'Job order not found'}), 404
    items = JobOrderItem.query.filter_by(job_order_id=job_order_id).all()
    # Batch the inventory_item lookup each item.to_dict() needs instead of
    # one InventoryItem.get() round trip per part.
    inv_items = InventoryItem.get_many(i.inventory_item_id for i in items)
    for i in items:
        i._inventory_item = inv_items.get(str(i.inventory_item_id))
    return jsonify([item.to_dict() for item in items]), 200


@job_orders_bp.route('/<job_order_id>/items', methods=['POST'])
@jwt_required()
def add_job_order_item(job_order_id):
    """Add a part to a job order.

    WORKFLOW:
    - Mechanic/Staff/Admin calls this to add parts
    - Frontend then sets status to 'Parts Requested'
    - Staff/Admin prints the JO and hands it to the stockman
    - Stockman sets status to 'Parts Released'
    - Stock is ONLY deducted when Parts Released (not at request time)
    """
    try:
        identity = get_identity()
        role    = identity.get('role')
        user_id = identity.get('id')
        if user_id is not None:
            try:
                user_id = user_id
            except (ValueError, TypeError):
                user_id = None

        job_order = JobOrder.query.get(job_order_id)
        if not job_order:
            return jsonify({'error': 'Job order not found'}), 404

        # Mechanic: only their own job
        if role == 'mechanic' and job_order.user_id != user_id:
            return jsonify({'error': 'Unauthorized: Not your job order'}), 403

        if job_order.status == 'Completed':
            return jsonify({'error': 'Cannot add items to a completed job order'}), 400

        data = request.get_json()

        # Accept both field names for compatibility
        inventory_item_id = data.get('inventory_item_id') or data.get('part_id')
        quantity_used     = data.get('quantity_used') or data.get('quantity')

        if not inventory_item_id or not quantity_used:
            return jsonify({'error': 'inventory_item_id and quantity_used are required'}), 400

        try:
            inventory_item_id = inventory_item_id
            quantity_used     = int(quantity_used)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid inventory_item_id or quantity'}), 400

        inventory_item = InventoryItem.query.get(inventory_item_id)
        if not inventory_item:
            return jsonify({'error': 'Inventory item not found'}), 404

        if inventory_item.stock < quantity_used:
            return jsonify({'error': f'Insufficient stock. Available: {inventory_item.stock}'}), 400

        # Check if part already in this job order
        existing_item = JobOrderItem.query.filter_by(
            job_order_id=job_order_id,
            inventory_item_id=inventory_item_id
        ).first()

        if existing_item:
            new_qty = existing_item.quantity_used + quantity_used
            if inventory_item.stock < new_qty:
                return jsonify({'error': f'Insufficient stock for update. Available: {inventory_item.stock}'}), 400
            existing_item.quantity_used = new_qty
            # Make item eligible for release again when adding more
            existing_item.is_released = False
            existing_item.released_at = None
            # NO stock deduction here - only save the request
            db.session.commit()
            job_order.calculate_totals()
            db.session.commit()
            return jsonify({'message': 'Item quantity updated', 'item': existing_item.to_dict()}), 200

        job_order_item = JobOrderItem(
            job_order_id=job_order_id,
            inventory_item_id=inventory_item_id,
            quantity_used=quantity_used,
            unit_price=inventory_item.price,
            is_released=False  # Explicitly set to False for new parts request
        )
        # NO stock deduction here - only save the requested parts
        db.session.add(job_order_item)
        db.session.commit()
        job_order.calculate_totals()
        db.session.commit()

        log_audit(identity, 'update', 'job_order', job_order.id, f"Added part '{inventory_item.name}' x{quantity_used}")

        return jsonify({
            'message': 'Part added to job order (stock deducted on release)',
            'item': job_order_item.to_dict()
        }), 201
        
    except Exception as e:
        current_app.logger.exception('add_job_order_item failed for JO %s: %s', job_order_id, e)
        return jsonify({'error': str(e)}), 500


@job_orders_bp.route('/<job_order_id>/items/<item_id>', methods=['DELETE'])
@jwt_required()
def remove_job_order_item(job_order_id, item_id):
    """Remove a part from a job order and restore stock. Admin/Staff only."""
    identity = get_identity()
    role = identity.get('role')

    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    job_order = JobOrder.query.get(job_order_id)
    if not job_order:
        return jsonify({'error': 'Job order not found'}), 404

    if job_order.status == 'Completed':
        return jsonify({'error': 'Cannot remove items from a completed job order'}), 400

    job_order_item = JobOrderItem.query.get(item_id)
    if not job_order_item or job_order_item.job_order_id != job_order_id:
        return jsonify({'error': 'Item not found in this job order'}), 404

    # Only restore stock if parts have already been released (stock already deducted)
    if job_order.status == 'Parts Released' and job_order_item.inventory_item:
        try:
            movement = InventoryMovement.record_receipt(
                inventory_item_id=job_order_item.inventory_item.id,
                quantity=job_order_item.quantity_used,
                unit_price=job_order_item.inventory_item.cost,
                purchase_order_num=None,
                reason=f'Job Order #{job_order.id} item removal - {job_order.vehicle.make} {job_order.vehicle.model}' if job_order.vehicle else f'Job Order #{job_order.id} item removal',
                user_id=identity.get('id')
            )
            current_app.logger.info(
                'JO %s: recorded receipt of %s to item %s',
                job_order.id, job_order_item.quantity_used, job_order_item.inventory_item.id
            )
        except ValueError as e:
            current_app.logger.warning('JO %s: failed to record receipt for item %s: %s', job_order.id, job_order_item.inventory_item.id, e)
            # Fallback to old method if movement recording fails
            job_order_item.inventory_item.add_stock(job_order_item.quantity_used)
            current_app.logger.info(
                'JO %s: fallback-restored %s to item %s',
                job_order.id, job_order_item.quantity_used, job_order_item.inventory_item.id
            )
    elif job_order.status != 'Parts Released':
        current_app.logger.debug('JO %s: skipping stock restore, parts not yet released (status=%s)', job_order.id, job_order.status)

    removed_item_name = job_order_item.inventory_item.name if job_order_item.inventory_item else 'Unknown part'
    removed_qty = job_order_item.quantity_used

    db.session.delete(job_order_item)
    db.session.commit()
    job_order.calculate_totals()
    db.session.commit()

    log_audit(identity, 'update', 'job_order', job_order.id, f"Removed part '{removed_item_name}' x{removed_qty}")

    return jsonify({'message': 'Item removed from job order'}), 200


@job_orders_bp.route('/<job_order_id>', methods=['DELETE'])
@jwt_required()
def delete_job_order(job_order_id):
    """Soft-delete a job order. Admin only."""
    identity = get_identity()
    if identity.get('role') != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    job_order = JobOrder.query.get(job_order_id)
    if not job_order:
        return jsonify({'error': 'Job order not found'}), 404

    # Soft delete
    job_order.is_deleted = True
    job_order.deleted_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'message': 'Job order deleted successfully'}), 200
