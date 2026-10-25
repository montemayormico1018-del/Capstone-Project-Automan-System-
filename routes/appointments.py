from flask import Blueprint, request, jsonify, current_app
import traceback
import re
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

# The shop is in the Philippines; Cloud Run containers run in UTC by
# default (no TZ env var is set anywhere in this project), so a naive
# datetime.now() here is off by 8 hours from the actual local time this
# "has the slot already passed today" check needs. Real customer browsers
# already send a correctly-local-time client-side check first (see
# customer_dashboard.html's isSlotPast()), so this server-side guard was
# masked in normal use -- it only mattered for whoever bypassed the client
# (a slow page load, a direct API call), where it was effectively
# ineffective for up to 8 hours of the business day.
_MANILA_TZ = ZoneInfo('Asia/Manila')
from models import db
from models.customer import Customer
from models.vehicle import Vehicle
from models.appointment import Appointment
from models.service import Service
from models.sms_log import SMSLog
from models.user import User
from models.service_package import ServicePackage
from models.system_setting import SystemSetting
from utils.vehicle_applicability import (
    normalize_vehicle_powertrain, is_service_applicable_by_flag,
)
from utils.vehicle_size_class import guess_size_class, normalize_size_class
from utils.plate_utils import validate_plate
from utils.audit import log_audit

appointments_bp = Blueprint('appointments', __name__)


def _validate_service_combinations(services):
    """Keep tire choices explicit and reject only known invalid pairings."""
    names = {str(s.name or '').lower() for s in services}
    tire_services = {n for n in names if 'tire' in n or 'wheel alignment' in n}
    if len(tire_services) <= 1:
        return None
    allowed_keywords = ('replacement', 'rotation', 'alignment')
    if not all(any(keyword in name for keyword in allowed_keywords) for name in tire_services):
        return 'The selected tire services cannot be combined. Choose tire replacement, rotation, and/or wheel alignment only.'
    return None


def generate_appointment_number():
    """Generate a human-friendly appointment number like APT-20260719-0001.

    Used to scan the ENTIRE appointments collection (Appointment.query.all())
    on every single booking just to count today's -- fine when there were a
    handful of documents, but this collection has grown large over months of
    testing/real use, so every mobile/web booking paid for a full-collection
    fetch over the network. Scoped to today only via created_at, same
    range-filter pattern already used in routes/reports.py.
    """
    now = datetime.utcnow()
    start_of_today = datetime(now.year, now.month, now.day)
    today = now.strftime('%Y%m%d')
    prefix = f'APT-{today}-'
    todays = [
        a for a in Appointment.query.filter(Appointment.created_at >= start_of_today).all()
        if getattr(a, 'appointment_number', None) and str(a.appointment_number).startswith(prefix)
    ]
    return f'{prefix}{len(todays) + 1:04d}'


def _validate_vehicle_year(year):
    """Coerce/validate a vehicle year. Accepts an int or a numeric string
    holding exactly one 4-digit year — rejects anything else (multiple
    numbers, garbage text, out-of-range years) so bad data never reaches
    Firestore regardless of which form/client submitted it.

    Returns (is_valid, normalized_year_or_None, error_message_or_None).
    """
    if year is None or year == '':
        return True, None, None
    if isinstance(year, bool):
        return False, None, 'Invalid vehicle year'
    if isinstance(year, float) and not year.is_integer():
        return False, None, 'Invalid vehicle year'
    year_str = str(year).strip()
    if not re.fullmatch(r'\d{4}', year_str):
        return False, None, 'Vehicle year must be a single 4-digit year'
    year_int = int(year_str)
    max_year = datetime.now().year + 1
    if year_int < 1980 or year_int > max_year:
        return False, None, f'Vehicle year must be between 1980 and {max_year}'
    return True, year_int, None


def _validate_vehicle_text_fields(data):
    """Length/charset-cap the free-text vehicle fields present in `data`
    (make/model/color/vin/notes). Only checks keys actually present, so the
    same helper works for both create (all fields) and patch-style update
    (whichever fields the caller sent) — mirrors _validate_vehicle_year's
    "reject bad data regardless of which form/client submitted it" approach.

    Returns (cleaned_data, error) -- error is a ready-to-return message, or
    None when everything present in `data` is within bounds. `cleaned_data`
    is a new dict with the validated fields trimmed/normalized; callers
    should use it in place of the original `data` from that point on.
    """
    cleaned = dict(data)
    text_rules = {
        'make':  (50, r"^[A-Za-z0-9À-ɏ' \-]+$", 'Make'),
        'model': (50, r"^[A-Za-z0-9À-ɏ' \-]+$", 'Model'),
        'color': (30, r"^[A-Za-zÀ-ɏ \-]+$", 'Color'),
    }
    for field, (max_len, pattern, label) in text_rules.items():
        if field in cleaned and cleaned[field]:
            value = str(cleaned[field]).strip()
            if len(value) > max_len or not re.match(pattern, value):
                return None, f'{label} must be {max_len} characters or fewer and contain only letters, numbers, spaces, or hyphens'
            cleaned[field] = value

    if 'vin' in cleaned and cleaned['vin']:
        vin = str(cleaned['vin']).strip().upper()
        # ISO 3779: 17 chars, letters + digits, excluding I/O/Q (too easily
        # confused with 1/0).
        if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
            return None, 'VIN must be exactly 17 letters/digits (no I, O, or Q)'
        cleaned['vin'] = vin

    if 'notes' in cleaned and cleaned['notes']:
        notes = str(cleaned['notes'])
        if len(notes) > 500:
            return None, 'Notes must be 500 characters or fewer'
        cleaned['notes'] = notes

    return cleaned, None


def _check_plate_duplicate(normalized_plate, customer_id, exclude_vehicle_id=None):
    """Look for another vehicle already using this normalized plate/sticker.

    Returns (same_customer_conflict, other_customer_vehicle):
    - same_customer_conflict: True if this customer already has a vehicle
      with this exact plate/sticker (always rejected — can't register the
      same plate twice on one account).
    - other_customer_vehicle: the first Vehicle found under a DIFFERENT
      customer, or None. Ownership can transfer, so this is a warning the
      caller may override, not a hard rejection.
    """
    matches = [
        v for v in Vehicle.query.filter_by(plate_or_sticker_no=normalized_plate).all()
        if not v.is_deleted and v.id != exclude_vehicle_id
    ]
    same_customer_conflict = any(v.customer_id == customer_id for v in matches)
    other_customer_vehicle = next((v for v in matches if v.customer_id != customer_id), None)
    return same_customer_conflict, other_customer_vehicle


# Appointment statuses that still hold a slot. Cancelled/Rejected free it up.
ACTIVE_APPOINTMENT_STATUSES = ['Pending', 'Quotation Sent', 'Approved', 'In Progress', 'Completed']

# The single source of truth for offered booking times. The booking form's
# time-slot buttons are server-rendered from BOOKING_TIME_SLOTS_DISPLAY (see
# customer_dashboard() in app.py) and the /availability endpoint reads
# BOOKING_TIME_SLOTS, so the form and the API can never disagree about what
# times exist — change the list here and both follow automatically.
BOOKING_TIME_SLOTS = ['08:00', '09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00']

# Shop hours: Monday–Saturday only (date.weekday(): Monday=0 ... Sunday=6).
CLOSED_WEEKDAY = 6


def _format_slot_label(slot_24h):
    """'08:00' -> '8:00 AM', '13:00' -> '1:00 PM'."""
    t = datetime.strptime(slot_24h, '%H:%M')
    hour12 = t.hour % 12 or 12
    period = 'AM' if t.hour < 12 else 'PM'
    return f'{hour12}:{t.minute:02d} {period}'


BOOKING_TIME_SLOTS_DISPLAY = [(s, _format_slot_label(s)) for s in BOOKING_TIME_SLOTS]


def _is_slot_past(target_date, slot_time, now=None):
    if now is None:
        now = datetime.now(_MANILA_TZ)
    return target_date < now.date() or (
        target_date == now.date() and slot_time <= now.time()
    )


def _created_at_sort_key(obj):
    # created_at may be a real datetime, a Firestore DatetimeWithNanoseconds,
    # or (older docs) a plain ISO string — compare as ISO strings rather than
    # sorting the raw mixed types directly (same pattern as job_orders.py).
    ca = getattr(obj, 'created_at', None)
    if ca is None:
        return ''
    return ca.isoformat() if hasattr(ca, 'isoformat') else str(ca)


def _active_bookings_for_slot(preferred_date, preferred_time, exclude_id=None):
    """All non-deleted, non-cancelled/rejected appointments in this exact
    date+time slot. Firestore can't filter on two fields without a composite
    index, so fetch by date (cheap, indexed by default) and check time/status
    in Python — same avoidance pattern used throughout this data layer."""
    same_date = Appointment.query.filter_by(preferred_date=preferred_date).all()
    return [
        a for a in same_date
        if not a.is_deleted
        and a.id != exclude_id
        and a.status in ACTIVE_APPOINTMENT_STATUSES
        and a.preferred_time == preferred_time
    ]


def _slot_capacity_reject_response():
    return jsonify({'error': 'This time slot is fully booked. Please choose another time.'}), 409


def _enforce_slot_capacity_after_create(preferred_date, preferred_time, new_appointment_id, max_per_slot):
    """Post-write compensating check for the race condition where two
    requests both pass the pre-create count check and both create a
    document for an already-nearly-full slot. This data layer has no
    cross-document Firestore transaction support, so: re-count immediately
    after creating, and if the slot is now over capacity, keep only the
    earliest max_per_slot bookings (by created_at, id as tiebreak) and
    delete any newer overflow — including this request's own document if
    it lost the race.

    Returns True if THIS request's appointment was one of the deleted
    overflow bookings (caller must return 409 — the slot never exceeds
    max_per_slot, even transiently, once this returns).
    """
    active = _active_bookings_for_slot(preferred_date, preferred_time)
    if len(active) <= max_per_slot:
        return False

    active.sort(key=lambda a: (_created_at_sort_key(a), str(a.id)))
    overflow = active[max_per_slot:]
    for a in overflow:
        a.delete()

    return any(a.id == new_appointment_id for a in overflow)


def send_sms_notification(phone_number, message, appointment_id=None):
    """Send SMS notification using iProg API."""
    from utils.sms_utils import send_sms as iprog_send_sms
    
    result = iprog_send_sms(phone_number, message)

    # No phone number or message text here -- both are PII/customer content,
    # and result may itself echo them back ('to'/'body') -- log only the
    # non-PII status fields.
    current_app.logger.debug(
        'send_sms_notification: appointment_id=%s success=%s status=%s',
        appointment_id, result.get('success') if isinstance(result, dict) else None,
        result.get('status') if isinstance(result, dict) else type(result).__name__
    )

    # Ensure result is a dict before processing
    if not isinstance(result, dict):
        current_app.logger.warning('send_sms_notification: send_sms returned unexpected type %s', type(result))
        result = {'success': False, 'status': 'error', 'error': 'Invalid response'}
    
    # Determine SMS status - only 'sent' if actually sent, not disabled
    sms_status = 'failed'
    if result.get('success') == True:
        raw_status = result.get('status', '')
        if raw_status == 'disabled':
            sms_status = 'disabled'
        else:
            sms_status = 'sent'  # Actually sent
    else:
        # Handle both string and list error responses
        error_val = result.get('error', 'failed')
        if isinstance(error_val, list):
            sms_status = str(error_val[0]) if error_val else 'failed'
        else:
            sms_status = str(error_val)
    
    # Store in SMS logs
    try:
        sms_log = SMSLog(
            phone_number=phone_number,
            message=message,
            status=sms_status,
            related_appointment_id=appointment_id
        )
        db.session.add(sms_log)
        db.session.commit()
    except Exception as e:
        current_app.logger.exception('send_sms_notification: could not log SMS to database: %s', e)
        db.session.rollback()
    
    # Return the dict result, NOT the SMSLog object
    return {'success': result.get('success', False), 'status': sms_status}


@appointments_bp.route('', methods=['GET'])
@jwt_required()
def get_appointments():
    """Get all appointments."""
    try:
        identity = get_jwt_identity()
        # Parse identity if it's a JSON string
        if isinstance(identity, str):
            import json
            identity = json.loads(identity)
        
        role = identity.get('role') if identity else None
        user_id = identity.get('id') if identity else None
        current_app.logger.debug('get_appointments: role=%s, has_user_id=%s', role, bool(user_id))

        if not identity or not user_id:
            return jsonify({'error': 'Invalid token - no identity'}), 401

        # Get filter parameters
        status = request.args.get('status')
        date = request.args.get('date')
        
        # Customers only see their own appointments
        if role == 'customer':
            # Find customer by user_id (assuming customer has user account)
            user = User.query.get(user_id)
            if not user:
                return jsonify({'error': 'User not found'}), 404
            customer = Customer.query.filter_by(email=user.email).first()
            if customer:
                query = Appointment.query.filter_by(customer_id=customer.id)
            else:
                query = Appointment.query.filter_by(id=0)  # Empty
        else:
            # Staff, mechanic, admin see all
            query = Appointment.query
        
        # Apply filters
        if status:
            query = query.filter_by(status=status)
        if date:
            query = query.filter_by(preferred_date=date)
        
        appointments = query.all()

        # Newest first — this had no ordering at all before, so a brand new
        # booking could land anywhere in the list (wherever Firestore
        # happened to return it) instead of at the top, forcing admin to
        # scroll around hunting for it. Sorted in Python, not via Firestore's
        # own .order_by(), since that would need a composite index once a
        # status/date filter is also applied (see the identical reasoning in
        # routes/job_orders.py's get_job_orders()).
        def _created_at_key(apt):
            ca = apt.created_at
            if ca is None:
                return ''
            return ca.isoformat() if hasattr(ca, 'isoformat') else str(ca)

        appointments.sort(key=_created_at_key, reverse=True)
        Appointment.prefetch(appointments)

        # Serialize with error handling
        result = []
        for apt in appointments:
            try:
                result.append(apt.to_dict())
            except Exception as e:
                current_app.logger.exception('get_appointments: failed to serialize appointment %s: %s', apt.id, e)
                continue

        return jsonify(result), 200
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@appointments_bp.route('/<appointment_id>', methods=['GET'])
@jwt_required()
def get_appointment(appointment_id):
    """Get a specific appointment."""
    appointment = Appointment.query.get(appointment_id)
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    return jsonify(appointment.to_dict()), 200


@appointments_bp.route('/<appointment_id>', methods=['PUT'])
@jwt_required()
def update_appointment(appointment_id):
    """Update an appointment (status, estimate, etc.)."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    appointment = Appointment.query.get(appointment_id)
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    data = request.get_json()
    current_app.logger.debug('update_appointment %s: fields present=%s', appointment_id, list(data.keys()) if data else [])

    # Accumulated for a single audit-trail entry at the end -- status,
    # price and reschedule edits are the actions an admin most needs
    # accountability for, so they get logged here even though nothing did
    # before this fix.
    changes = []

    # Update service estimate first (needed for quotation SMS)
    if data.get('service_estimate') is not None:
        appointment.service_estimate = data['service_estimate']

    # Track price-range edits for an audit trail (who/when) and so a
    # customer never approves a price they were never actually shown — see
    # the re-notify block below.
    price_changed = False
    if data.get('min_estimate') is not None and data['min_estimate'] != appointment.min_estimate:
        appointment.min_estimate = data['min_estimate']
        price_changed = True

    if data.get('max_estimate') is not None and data['max_estimate'] != appointment.max_estimate:
        appointment.max_estimate = data['max_estimate']
        price_changed = True

    if price_changed:
        appointment.price_history = (appointment.price_history or []) + [{
            'by': identity.get('id'),
            'at': datetime.utcnow().isoformat(),
            'min_estimate': appointment.min_estimate,
            'max_estimate': appointment.max_estimate,
        }]
        if appointment.min_estimate and appointment.max_estimate:
            changes.append(f"Price set to PHP {appointment.min_estimate:,.0f}-{appointment.max_estimate:,.0f}")
        else:
            changes.append("Price estimate updated")

    # Only update notes if actually provided in request AND has content
    if 'notes' in data or 'reason' in data:
        notes_value = (data.get('notes') or data.get('reason') or '').strip()
        if notes_value:  # Only update if not empty
            appointment.notes = notes_value
    if data.get('status') == 'Rejected':
        appointment.rejection_reason = (data.get('rejection_reason') or 'Other').strip()
        appointment.rejection_message = (data.get('reason') or data.get('notes') or '').strip()
    
    # Track SMS success for response
    sms_success = None
    sms_error = None
    
    # Update status
    if data.get('status'):
        old_status = appointment.status
        appointment.status = data['status']
        if old_status != data['status']:
            changes.append(f"Status: {old_status} -> {data['status']}")

        # Commit BEFORE sending SMS to ensure data is saved
        db.session.commit()
        
        # Send SMS notification on status change - FIX: use appointment.customer directly
        customer = appointment.customer
        current_app.logger.debug(
            'update_appointment %s: customer_id=%s has_phone=%s',
            appointment_id, customer.id if customer else None, bool(customer and customer.phone)
        )

        if customer and customer.phone and old_status != data['status']:
            try:
                status_msg = data['status']
                sms_sent = False
                
                if status_msg == 'Quotation Sent':
                    # Use configurable base URL
                    base_url = current_app.config.get('BASE_URL', 'http://localhost:5000')
                    
                    # Include the view link if using HTTPS (iProg blocks HTTP URLs)
                    include_url = base_url.startswith('https://')
                    
                    # Include notes if provided
                    notes = data.get('notes', '')

                    # Build price message - range or single price
                    if appointment.min_estimate and appointment.max_estimate:
                        price_text = f"PHP {appointment.min_estimate:,.0f} - {appointment.max_estimate:,.0f}"
                    else:
                        price_text = f"PHP {appointment.service_estimate:,.2f}"

                    # Package name(s), if any — full itemized checklist is on
                    # the linked quotation page, not crammed into the SMS.
                    package_names = [p.name for p in appointment.packages] if appointment.package_ids else []
                    package_line = f" Package: {', '.join(package_names)}." if package_names else ""

                    # Build message - only include URL if HTTPS
                    if include_url:
                        from utils.quotation_token import generate_quotation_token
                        token = generate_quotation_token(appointment.id)
                        view_link = f"{base_url}/quotation/view/{appointment.id}?t={token}"
                        if notes:
                            message = f"Automan Car Care: Service quotation ready!{package_line} Est: {price_text}. Notes: {notes}. View and approve: {view_link}"
                        else:
                            message = f"Automan Car Care: Service quotation ready!{package_line} Est: {price_text}. View and approve: {view_link}"
                    else:
                        # iProg blocks HTTP URLs - use message without link
                        if notes:
                            message = f"Automan Car Care: Service quotation ready!{package_line} Est: {price_text}. Notes: {notes}. Please contact us to approve."
                        else:
                            message = f"Automan Car Care: Your service quotation{package_line} ({price_text}) is ready. Please call or visit us to approve. Thank you!"
                    
                    current_app.logger.debug(
                        'update_appointment %s: sending quotation SMS (base_url=%s include_url=%s)',
                        appointment.id, base_url, include_url
                    )

                    sms_result = send_sms_notification(customer.phone, message, appointment.id)
                    current_app.logger.debug('update_appointment %s: quotation SMS result=%s', appointment.id, sms_result)
                    sms_sent = sms_result.get('success', False) and sms_result.get('status') == 'sent'
                elif status_msg == 'Approved':
                    message = f"Automan Car Care: Your appointment has been SCHEDULED and APPROVED for {appointment.preferred_date} at {appointment.preferred_time}. See you!"
                    sms_result = send_sms_notification(customer.phone, message, appointment.id)
                    sms_sent = sms_result.get('status') == 'sent'
                elif status_msg == 'Rejected':
                    # FIX: Build SMS message using request data directly, not appointment object
                    reason_text = (data.get('notes') or data.get('reason') or '').strip()
                    current_app.logger.debug('update_appointment %s: rejection reason provided=%s', appointment.id, bool(reason_text))

                    if reason_text:
                        message = f"Automan Car Care: Your appointment has been REJECTED. Reason: {reason_text}"
                    else:
                        message = "Automan Car Care: Your appointment has been REJECTED. Please contact us for more information."

                    sms_result = send_sms_notification(customer.phone, message, appointment.id)
                    sms_sent = sms_result.get('status') == 'sent'
                elif status_msg == 'Completed':
                    message = "Automan Car Care: Your service is COMPLETED. Thank you for choosing us!"
                    sms_result = send_sms_notification(customer.phone, message, appointment.id)
                    sms_sent = sms_result.get('status') == 'sent'
                elif status_msg == 'Cancelled':
                    message = "Automan Car Care: Your appointment has been CANCELLED."
                    sms_result = send_sms_notification(customer.phone, message, appointment.id)
                    sms_sent = sms_result.get('status') == 'sent'
                else:
                    message = f"Automan Car Care: Your appointment status is now {status_msg}."
                    sms_result = send_sms_notification(customer.phone, message, appointment.id)
                    sms_sent = sms_result.get('status') == 'sent'
                
                sms_success = sms_sent
                if not sms_sent and sms_result.get('error'):
                    sms_error = sms_result.get('error')
                elif not sms_sent and sms_result.get('status') == 'disabled':
                    sms_error = 'SMS is disabled in system configuration'
            except Exception as e:
                sms_error = str(e)
                sms_success = False
                current_app.logger.exception('update_appointment %s: failed to send status-change SMS: %s', appointment.id, e)

    # Price edited without a status change (e.g. staff corrects the range
    # after the quotation already went out): send a fresh SMS so the
    # customer isn't left approving a number they were never shown. Only
    # applies once a quotation has actually been sent — editing the price
    # while still Pending doesn't need an SMS yet.
    price_resent = False
    if price_changed and not data.get('status') and appointment.status == 'Quotation Sent':
        customer = appointment.customer
        if customer and customer.phone:
            if appointment.min_estimate and appointment.max_estimate:
                price_text = f"PHP {appointment.min_estimate:,.0f} - {appointment.max_estimate:,.0f}"
            else:
                price_text = f"PHP {appointment.service_estimate:,.2f}"
            message = (f"Automan Car Care: Your service quotation has been UPDATED. "
                       f"New estimate: {price_text}. Please check your quotation link again before approving.")
            try:
                resend_result = send_sms_notification(customer.phone, message, appointment.id)
                price_resent = resend_result.get('status') == 'sent'
            except Exception as e:
                current_app.logger.exception('update_appointment %s: failed to send price-update SMS: %s', appointment.id, e)

    # Update preferred date/time
    if data.get('preferred_date'):
        try:
            new_date = datetime.strptime(data['preferred_date'], '%Y-%m-%d').date()
            if new_date.weekday() == CLOSED_WEEKDAY:
                return jsonify({'error': 'We are closed on Sundays. Please choose a date Monday–Saturday.'}), 400
            if new_date != appointment.preferred_date:
                changes.append(f"Rescheduled date to {new_date}")
            appointment.preferred_date = new_date
        except ValueError:
            pass

    if data.get('preferred_time'):
        try:
            new_time = datetime.strptime(data['preferred_time'], '%H:%M').time()
            if new_time != appointment.preferred_time:
                changes.append(f"Rescheduled time to {new_time}")
            appointment.preferred_time = new_time
        except ValueError:
            pass

    db.session.commit()

    if changes:
        log_audit(identity, 'update', 'appointment', appointment.id, '; '.join(changes))
    
    # Build response with SMS status
    response_data = {
        'message': 'Appointment updated successfully',
        'appointment': appointment.to_dict()
    }
    
    # Include SMS status in response if applicable
    if sms_success is not None:
        response_data['sms_sent'] = sms_success
        if sms_error:
            response_data['sms_error'] = sms_error

    if price_changed:
        response_data['price_resent'] = price_resent

    return jsonify(response_data), 200


@appointments_bp.route('', methods=['POST'])
@jwt_required()
def create_appointment():
    """Create a new appointment."""
    try:
        identity = get_jwt_identity()
        role = identity.get('role')
        user_id = identity.get('id')
        data = request.get_json()
        
        customer_id = data.get('customer_id')
        vehicle_id = data.get('vehicle_id')
        plate_number = data.get('plate_number')
        preferred_date = data.get('preferred_date')
        preferred_time = data.get('preferred_time')
        service_ids = data.get('service_ids', [])
        package_ids = data.get('package_ids', [])
        customer_notes = data.get('customer_notes')
        notes = data.get('notes')
        
        # Auto-resolve customer_id for customer users
        if role == 'customer' and not customer_id:
            user = User.query.get(user_id)
            if user:
                customer = Customer.query.filter_by(email=user.email).first()
                if customer:
                    customer_id = customer.id
                else:
                    return jsonify({'error': 'Customer profile not found. Please contact support.'}), 404
            else:
                return jsonify({'error': 'User not found'}), 404
        
        if not customer_id:
            return jsonify({'error': 'Customer is required'}), 400

        if not service_ids and not package_ids:
            return jsonify({'error': 'Please select at least one service or package'}), 400

        # Customers must pick one of their admin-assigned vehicles — no more
        # plate-only/walk-in booking for that role. Other roles (admin/staff
        # booking on a walk-in customer's behalf) keep the existing either/or.
        if role == 'customer':
            if not vehicle_id:
                return jsonify({'error': 'Please select one of your registered vehicles'}), 400
        elif not vehicle_id and not plate_number:
            return jsonify({'error': 'Vehicle or plate number is required'}), 400
        
        # Parse date and time
        try:
            preferred_date = datetime.strptime(preferred_date, '%Y-%m-%d').date()
            preferred_time = datetime.strptime(preferred_time, '%H:%M').time()
        except ValueError:
            return jsonify({'error': 'Invalid date or time format'}), 400

        if preferred_date.weekday() == CLOSED_WEEKDAY:
            return jsonify({'error': 'We are closed on Sundays. Please choose a date Monday–Saturday.'}), 400

        # Customers can't self-book a slot that's already started today — the
        # UI disables these, but that's client-side only, so re-check here in
        # case someone calls the API directly. Admin/staff keep booking
        # walk-ins at whatever time already happened, same as today.
        if role == 'customer':
            now = datetime.now(_MANILA_TZ)
            if preferred_date == now.date() and preferred_time <= now.time():
                return jsonify({'error': 'That time slot has already passed today. Please choose a later time.'}), 400

        max_per_slot = current_app.config.get('MAX_BOOKINGS_PER_SLOT', 3)
        if len(_active_bookings_for_slot(preferred_date, preferred_time)) >= max_per_slot:
            return _slot_capacity_reject_response()

        # Verify customer exists
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({'error': 'Customer not found'}), 404
        
        if vehicle_id:
            vehicle = Vehicle.query.get(vehicle_id)
            if not vehicle:
                return jsonify({'error': 'Vehicle not found'}), 404
            # Verify the vehicle belongs to the customer
            if vehicle.customer_id != customer_id:
                return jsonify({'error': 'Vehicle does not belong to this customer'}), 400
        else:
            vehicle = None
            vehicle_id = None

        # Fall back to the vehicle's stored plate/sticker if none was typed at booking time.
        if not plate_number and vehicle:
            plate_number = vehicle.plate_or_sticker_no or vehicle.license_plate

        # Every appointment must carry a validated plate/sticker — whether it
        # was just typed, or came from the vehicle's own record. This is what
        # actually enforces "must have a plate" for legacy vehicles that were
        # registered with none: the customer gets blocked here at booking
        # time (not at Add Vehicle, which deliberately has no plate field)
        # until they type one in.
        plate_type_raw = data.get('plate_type') or data.get('identifier_type') or 'plate_number'
        plate_kind = 'conduction' if plate_type_raw in ('conduction_sticker', 'conduction') else 'plate'
        is_valid, normalized_plate, plate_error = validate_plate(plate_number, plate_kind)
        if not is_valid:
            return jsonify({'error': plate_error}), 400
        plate_number = normalized_plate

        # The /services?vehicle_id= dropdown only filters what's *displayed* —
        # it was never re-checked here, so a customer could POST an
        # incompatible service/package id (e.g. an oil change for an EV)
        # directly. Re-run the same applicability check the dropdown uses.
        powertrain = normalize_vehicle_powertrain(vehicle) if vehicle else None

        # Add services (skip duplicates, validate they exist and are compatible)
        valid_service_ids = []
        selected_services = []
        seen = set()
        for service_id in service_ids:
            if service_id in seen:
                continue
            service = Service.query.get(service_id)
            if not service:
                continue
            if powertrain and not is_service_applicable_by_flag(service, powertrain):
                return jsonify({'error': f"Service '{service.name}' is not available for this vehicle's fuel type"}), 400
            valid_service_ids.append(service_id)
            selected_services.append(service)
            seen.add(service_id)

        combination_error = _validate_service_combinations(selected_services)
        if combination_error:
            return jsonify({'error': combination_error}), 400

        # Add packages (skip duplicates, validate they exist and are compatible)
        valid_package_ids = []
        seen_pkg = set()
        for package_id in package_ids:
            if package_id in seen_pkg:
                continue
            package = ServicePackage.query.get(package_id)
            if not package:
                continue
            if powertrain and not is_service_applicable_by_flag(package, powertrain):
                return jsonify({'error': f"Package '{package.name}' is not available for this vehicle's fuel type"}), 400
            valid_package_ids.append(package_id)
            seen_pkg.add(package_id)

        # Create appointment
        appointment = Appointment(
            appointment_number=generate_appointment_number(),
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            plate_number=plate_number,
            preferred_date=preferred_date,
            preferred_time=preferred_time,
            customer_notes=customer_notes,
            notes=notes,
            status='Pending',
            service_ids=valid_service_ids,
            package_ids=valid_package_ids
        )
        # Base prices are admin-managed Service/ServicePackage records.  The
        # quotation range starts at that estimate and uses a configurable
        # backend markup instead of a frontend hard-coded difference.
        from utils.pricing import compute_suggested_estimate
        appointment.service_estimate = compute_suggested_estimate(appointment)
        appointment.min_estimate = appointment.service_estimate
        appointment.max_estimate = appointment.service_estimate + float(SystemSetting.pricing().quotation_markup or 0)
        db.session.add(appointment)
        db.session.commit()

        # Two requests can both pass the pre-check above for the same
        # near-full slot (no cross-document transaction in this data layer)
        # — this compensating check re-counts after the write and rolls
        # back whichever booking(s) lost the race, including this one.
        lost_race = _enforce_slot_capacity_after_create(
            preferred_date, preferred_time, appointment.id, max_per_slot
        )
        if lost_race:
            return _slot_capacity_reject_response()

        return jsonify({
            'message': 'Appointment created successfully',
            'appointment': appointment.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('create_appointment failed: %s', e)
        return jsonify({'error': str(e)}), 500


@appointments_bp.route('/availability', methods=['GET'])
@jwt_required()
def get_slot_availability():
    """Booking-form + API source of truth for slot capacity on a given date.
    BOOKING_TIME_SLOTS is the single list of offered times — the booking
    form's JS renders from this same endpoint, so the two can never disagree
    about what times exist."""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'date query parameter is required (YYYY-MM-DD)'}), 400

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format, expected YYYY-MM-DD'}), 400

    identity = get_jwt_identity()
    role = identity.get('role') if isinstance(identity, dict) else None
    now = datetime.now(_MANILA_TZ)
    max_per_slot = current_app.config.get('MAX_BOOKINGS_PER_SLOT', 3)

    if target_date.weekday() == CLOSED_WEEKDAY:
        return jsonify({
            'date': date_str,
            'max_bookings_per_slot': max_per_slot,
            'closed': True,
            'slots': [
                {
                    'time': s,
                    'booked': 0,
                    'capacity': max_per_slot,
                    'remaining': 0,
                    'available': False,
                    'is_full': True,
                    'is_past': _is_slot_past(
                        target_date,
                        datetime.strptime(s, '%H:%M').time(),
                        now,
                    ),
                }
                for s in BOOKING_TIME_SLOTS
            ]
        }), 200

    same_date = Appointment.query.filter_by(preferred_date=target_date).all()
    active = [a for a in same_date if not a.is_deleted and a.status in ACTIVE_APPOINTMENT_STATUSES]

    slots = []
    for slot_str in BOOKING_TIME_SLOTS:
        slot_time = datetime.strptime(slot_str, '%H:%M').time()
        count = sum(1 for a in active if a.preferred_time == slot_time)
        is_past = _is_slot_past(target_date, slot_time, now)
        slots.append({
            'time': slot_str,
            'booked': count,
            'capacity': max_per_slot,
            'remaining': max(0, max_per_slot - count),
            'available': (role != 'customer' or not is_past) and count < max_per_slot,
            'is_full': count >= max_per_slot,
            'is_past': is_past
        })

    return jsonify({
        'date': date_str,
        'max_bookings_per_slot': max_per_slot,
        'slots': slots
    }), 200


@appointments_bp.route('/<appointment_id>', methods=['DELETE'])
@jwt_required()
def delete_appointment(appointment_id):
    """Delete/cancel an appointment."""
    identity = get_jwt_identity()
    role = identity.get('role')
    user_id = identity.get('id')
    
    appointment = Appointment.query.get(appointment_id)
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    # For customers, only allow deleting Pending or Quotation Sent appointments
    if role == 'customer':
        if appointment.status not in ['Pending', 'Quotation Sent']:
            return jsonify({'error': 'Cannot delete appointment that is already in progress or completed'}), 400
        
        # Customers can only delete their own appointments
        user = User.query.get(user_id)
        customer = Customer.query.filter_by(email=user.email).first()
        if not customer or appointment.customer_id != customer.id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Customer can delete their own appointment
        db.session.delete(appointment)
        db.session.commit()
        return jsonify({'message': 'Appointment cancelled successfully'}), 200
    
    # For admin/staff, allow deleting any appointment
    if role in ['admin', 'staff']:
        # Send SMS notification to customer before deleting
        customer = appointment.vehicle.customer if appointment.vehicle else None
        if customer and customer.phone:
            try:
                message = "Automan Car Care: Your appointment has been CANCELLED by our staff. Please contact us to reschedule."
                send_sms_notification(customer.phone, message, appointment.id)
            except Exception as e:
                current_app.logger.exception('cancel appointment %s: failed to send SMS: %s', appointment.id, e)
        
        db.session.delete(appointment)
        db.session.commit()
        return jsonify({'message': 'Appointment deleted successfully'}), 200
    
    # For customers, only allow Pending/Quotation Sent (fallback)
    if appointment.status not in ['Pending', 'Quotation Sent']:
        return jsonify({'error': 'Cannot delete appointment that is already in progress or completed'}), 400
    
    db.session.delete(appointment)
    db.session.commit()
    
    return jsonify({'message': 'Appointment cancelled successfully'}), 200


# Customer and Vehicle routes
@appointments_bp.route('/customers', methods=['GET'])
@jwt_required()
def get_customers():
    """Get all customers. Admin/staff only — this returns every customer's
    name, email, phone, address, and vehicles, so a customer role must never
    reach it (they have their own scoped access via /vehicles, /appointments)."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403
    customers = Customer.query.all()
    Customer.prefetch(customers)
    return jsonify([c.to_dict() for c in customers]), 200


@appointments_bp.route('/customers', methods=['POST'])
@jwt_required()
def create_customer():
    """Create a new customer. Admin/staff only — customers get their own
    profile auto-created server-side elsewhere (see create_vehicle/
    create_appointment's auto-resolve-by-email paths)."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403
    data = request.get_json()
    
    name = data.get('name')
    email = data.get('email')
    phone = data.get('phone')
    address = data.get('address')
    
    if not name or not phone:
        return jsonify({'error': 'Name and phone are required'}), 400
    
    customer = Customer(
        name=name,
        email=email,
        phone=phone,
        address=address
    )
    db.session.add(customer)
    db.session.commit()
    
    return jsonify({
        'message': 'Customer created successfully',
        'customer': customer.to_dict()
    }), 201


@appointments_bp.route('/vehicles', methods=['GET'])
@jwt_required()
def get_vehicles():
    """Get all vehicles."""
    try:
        identity = get_jwt_identity()
        # Parse identity if it's a JSON string
        if isinstance(identity, str):
            import json
            identity = json.loads(identity)
        
        role = identity.get('role')
        user_id = identity.get('id')
        customer_id = request.args.get('customer_id')
        
        current_app.logger.debug('get_vehicles: role=%s user_id=%s customer_id=%s', role, user_id, customer_id)

        # Customers see only their own vehicles
        if role == 'customer':
            try:
                user = User.query.get(user_id)

                if not user:
                    return jsonify({'error': 'User not found. Please login again.'}), 404

                # Try to find customer by email
                customer = Customer.query.filter_by(email=user.email).first()

                if not customer:
                    # If no customer found, create one
                    customer = Customer(
                        name=user.username,
                        email=user.email,
                        phone=user.contact_number or '',
                        address=user.address or ''
                    )
                    db.session.add(customer)
                    db.session.commit()
                    current_app.logger.debug('get_vehicles: created new customer %s for user %s', customer.id, user_id)

                vehicles = Vehicle.query.filter_by(customer_id=customer.id).all()
                current_app.logger.debug('get_vehicles: found %d vehicles for customer %s', len(vehicles), customer.id)

            except Exception as e:
                current_app.logger.exception('get_vehicles: error getting vehicles for user %s: %s', user_id, e)
                return jsonify({'error': str(e)}), 500
        elif customer_id:
            vehicles = Vehicle.query.filter_by(customer_id=customer_id).all()
            current_app.logger.debug('get_vehicles: found %d vehicles for customer_id %s', len(vehicles), customer_id)
        elif role in ('admin', 'staff'):
            vehicles = Vehicle.query.all()
        else:
            # mechanic / other non-customer roles must scope by customer_id
            return jsonify({'error': 'customer_id is required for this role'}), 400

        # Serialize with error handling
        result = []
        for v in vehicles:
            try:
                result.append(v.to_dict())
            except Exception as e:
                current_app.logger.exception('Failed to serialize vehicle %s: %s', v.id, e)
                continue
        
        return jsonify({'vehicles': result}), 200
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@appointments_bp.route('/vehicles', methods=['POST'])
@jwt_required()
def create_vehicle():
    """Create a new vehicle. Admin/staff can assign a vehicle to any customer;
    customers can add a vehicle to their own account only (their customer_id
    is resolved server-side from their login, never trusted from the body)."""
    data = request.get_json()

    identity = get_jwt_identity()
    if isinstance(identity, str):
        import json
        identity = json.loads(identity)

    role = identity.get('role')
    user_id = identity.get('id')

    if role == 'customer':
        user = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found. Please login again.'}), 404
        customer = Customer.query.filter_by(email=user.email).first()
        if not customer:
            customer = Customer(
                name=user.username,
                email=user.email,
                phone=user.contact_number or '',
                address=user.address or ''
            )
            db.session.add(customer)
            db.session.commit()
        data = dict(data or {})
        data['customer_id'] = customer.id
    elif role not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized'}), 403

    data, text_error = _validate_vehicle_text_fields(data)
    if text_error:
        return jsonify({'error': text_error}), 400

    make = data.get('make')
    model = data.get('model')
    year = data.get('year')
    color = data.get('color')
    mileage = data.get('mileage')
    vehicle_type = data.get('type') or data.get('vehicle_type')  # Gasoline, Diesel, HEV, PHEV, BEV, etc.
    vin = data.get('vin')
    notes = data.get('notes')

    is_valid_year, year, year_error = _validate_vehicle_year(year)
    if not is_valid_year:
        return jsonify({'error': year_error}), 400

    identifier_type = (data.get('identifier_type') or 'plate_number').strip()
    if identifier_type not in ('plate_number', 'conduction_sticker'):
        identifier_type = 'plate_number'
    plate_or_sticker_no = data.get('plate_or_sticker_no') or data.get('license_plate')

    # Plate/sticker is OPTIONAL at vehicle-creation time (a customer may add a
    # vehicle with no plate yet and supply it later at booking — see
    # create_appointment below). If one IS supplied here, validate it so bad
    # data never enters the system regardless of which form submitted it.
    if plate_or_sticker_no:
        plate_kind = 'conduction' if identifier_type == 'conduction_sticker' else 'plate'
        is_valid, normalized_plate, plate_error = validate_plate(plate_or_sticker_no, plate_kind)
        if not is_valid:
            return jsonify({'error': plate_error}), 400
        plate_or_sticker_no = normalized_plate

    size_class = normalize_size_class(data.get('size_class'))
    if not size_class:
        size_class = guess_size_class(make, model)

    customer_id = data.get('customer_id')
    if not customer_id or not make or not model:
        return jsonify({'error': 'Customer, make, and model are required'}), 400

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({'error': 'Customer not found'}), 404

    if plate_or_sticker_no:
        same_customer_conflict, other_customer_vehicle = _check_plate_duplicate(
            plate_or_sticker_no, customer_id
        )
        if same_customer_conflict:
            return jsonify({'error': 'This plate number or conduction sticker is already registered to one of your vehicles.'}), 400
        if other_customer_vehicle and not data.get('confirm_duplicate'):
            if role in ('admin', 'staff'):
                return jsonify({
                    'warning': ('This plate/sticker is already registered under a different '
                                'customer. Ownership may have transferred — resubmit with '
                                'confirm_duplicate: true to proceed.'),
                    'existing_vehicle_id': other_customer_vehicle.id,
                    'existing_customer_id': other_customer_vehicle.customer_id
                }), 409
            return jsonify({'error': 'This plate number or conduction sticker is already registered to a different customer.'}), 400

    normalized_vehicle_type = (vehicle_type or 'Gasoline').strip()

    vehicle = Vehicle(
        customer_id=customer_id,
        make=make,
        model=model,
        year=year,
        color=color,
        mileage=mileage,
        vehicle_type=normalized_vehicle_type,
        size_class=size_class,
        identifier_type=identifier_type,
        plate_or_sticker_no=plate_or_sticker_no,
        license_plate=plate_or_sticker_no,
        vin=vin,
        notes=notes
    )
    db.session.add(vehicle)
    db.session.commit()

    log_audit(identity, 'create', 'vehicle', vehicle.id, f"Added vehicle '{vehicle.make} {vehicle.model}'")

    return jsonify({
        'message': 'Vehicle created successfully',
        'vehicle': vehicle.to_dict()
    }), 201


@appointments_bp.route('/vehicles/<vehicle_id>', methods=['PUT'])
@jwt_required()
def update_vehicle(vehicle_id):
    """Update a vehicle.

    Admin/staff can update any vehicle and reassign it, edit its plate/
    sticker, VIN, mileage, and notes.

    Customers can update only their OWN vehicle, and only its basic info
    (make/model/year/color/type) — not customer_id, VIN, mileage, notes, or
    the plate/sticker (that's still entered at booking time, matching
    create_vehicle's behavior)."""
    identity = get_jwt_identity()
    if isinstance(identity, str):
        import json
        identity = json.loads(identity)

    role = identity.get('role')
    user_id = identity.get('id')

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404

    data = request.get_json()
    data, text_error = _validate_vehicle_text_fields(data)
    if text_error:
        return jsonify({'error': text_error}), 400

    if role == 'customer':
        user = User.query.get(user_id)
        customer = Customer.query.filter_by(email=user.email).first() if user else None
        if not customer or vehicle.customer_id != customer.id:
            return jsonify({'error': 'Unauthorized'}), 403

        is_valid_year, year, year_error = _validate_vehicle_year(data.get('year'))
        if not is_valid_year:
            return jsonify({'error': year_error}), 400

        for field in ('make', 'model', 'color'):
            if field in data:
                setattr(vehicle, field, data[field])
        if 'year' in data:
            vehicle.year = year
        if 'type' in data or 'vehicle_type' in data:
            vehicle.vehicle_type = data.get('vehicle_type') or data.get('type')

        vehicle.updated_at = datetime.utcnow()
        db.session.commit()

        log_audit(identity, 'update', 'vehicle', vehicle.id, f"Updated vehicle '{vehicle.make} {vehicle.model}'")

        return jsonify({
            'message': 'Vehicle updated successfully',
            'vehicle': vehicle.to_dict()
        }), 200

    if role not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    if 'customer_id' in data:
        new_customer = Customer.query.get(data['customer_id'])
        if not new_customer:
            return jsonify({'error': 'Customer not found'}), 400
        vehicle.customer_id = data['customer_id']

    is_valid_year, year, year_error = _validate_vehicle_year(data.get('year'))
    if not is_valid_year:
        return jsonify({'error': year_error}), 400
    if 'year' in data:
        data = dict(data)
        data['year'] = year

    for field in ('make', 'model', 'year', 'color', 'mileage', 'vin', 'notes'):
        if field in data:
            setattr(vehicle, field, data[field])

    if 'type' in data or 'vehicle_type' in data:
        vehicle.vehicle_type = data.get('vehicle_type') or data.get('type')

    if 'size_class' in data:
        new_size_class = normalize_size_class(data['size_class'])
        if new_size_class:
            vehicle.size_class = new_size_class

    if 'identifier_type' in data and data['identifier_type'] in ('plate_number', 'conduction_sticker'):
        vehicle.identifier_type = data['identifier_type']

    if 'plate_or_sticker_no' in data:
        new_plate = data['plate_or_sticker_no']
        if new_plate:
            plate_kind = 'conduction' if vehicle.identifier_type == 'conduction_sticker' else 'plate'
            is_valid, normalized_plate, plate_error = validate_plate(new_plate, plate_kind)
            if not is_valid:
                return jsonify({'error': plate_error}), 400

            target_customer_id = data.get('customer_id', vehicle.customer_id)
            same_customer_conflict, other_customer_vehicle = _check_plate_duplicate(
                normalized_plate, target_customer_id, exclude_vehicle_id=vehicle_id
            )
            if same_customer_conflict:
                return jsonify({'error': 'This plate number or conduction sticker is already registered to one of your other vehicles.'}), 400
            if other_customer_vehicle and not data.get('confirm_duplicate'):
                # This route is admin/staff-only (checked above), so the
                # "staff/admin confirmation" override is always available here.
                return jsonify({
                    'warning': ('This plate/sticker is already registered under a different '
                                'customer. Ownership may have transferred — resubmit with '
                                'confirm_duplicate: true to proceed.'),
                    'existing_vehicle_id': other_customer_vehicle.id,
                    'existing_customer_id': other_customer_vehicle.customer_id
                }), 409

            vehicle.plate_or_sticker_no = normalized_plate
            vehicle.license_plate = normalized_plate
        else:
            vehicle.plate_or_sticker_no = new_plate
            vehicle.license_plate = new_plate

    vehicle.updated_at = datetime.utcnow()
    db.session.commit()

    log_audit(identity, 'update', 'vehicle', vehicle.id, f"Updated vehicle '{vehicle.make} {vehicle.model}'")

    return jsonify({
        'message': 'Vehicle updated successfully',
        'vehicle': vehicle.to_dict()
    }), 200


@appointments_bp.route('/vehicles/<vehicle_id>', methods=['DELETE'])
@jwt_required()
def delete_vehicle(vehicle_id):
    """Delete a vehicle.

    Admin/staff can delete any vehicle, unconditionally (matches the
    existing admin/staff appointment-delete behavior — no history check).

    Customers can delete only their OWN vehicle, and only if it has no
    appointment history — deleting a vehicle that appointments/job orders
    reference would orphan those records (same guard rail as customers only
    being able to delete Pending/Quotation Sent appointments, just applied
    at the vehicle level since vehicles have no status field of their own).
    """
    identity = get_jwt_identity()
    if isinstance(identity, str):
        import json
        identity = json.loads(identity)

    role = identity.get('role')
    user_id = identity.get('id')

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404

    if role == 'customer':
        user = User.query.get(user_id)
        customer = Customer.query.filter_by(email=user.email).first() if user else None
        if not customer or vehicle.customer_id != customer.id:
            return jsonify({'error': 'Unauthorized'}), 403

        has_history = len(Appointment.query.filter_by(vehicle_id=vehicle_id).all()) > 0
        if has_history:
            return jsonify({
                'error': ('This vehicle has appointment history and can\'t be removed. '
                          'Contact staff if you need it deleted.')
            }), 400
    elif role not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized — admin/staff only'}), 403

    vehicle_label = f'{vehicle.make} {vehicle.model}'
    db.session.delete(vehicle)
    db.session.commit()

    log_audit(identity, 'delete', 'vehicle', vehicle_id, f"Deleted vehicle '{vehicle_label}'")

    return jsonify({'message': 'Vehicle deleted successfully'}), 200


# (service name keyword, interval_km, interval_months) -- matched
# case-insensitively as a substring against completed services' names.
# No per-service interval field exists in the Service model and there's no
# admin screen to manage one, so this is a small fixed table rather than a
# configurable schedule; good enough for a "suggested" recommendation.
_SERVICE_INTERVALS = [
    ('oil change', 5000, 6),
    ('tire rotation', 10000, 6),
    ('brake', 20000, 12),
    ('air filter', 15000, 12),
    ('coolant', 40000, 24),
    ('battery', 24000, 24),
    ('transmission', 40000, 24),
    ('wiper', 12000, 12),
]


def _months_between(earlier, later):
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


@appointments_bp.route('/vehicles/<vehicle_id>/next-service', methods=['GET'])
@jwt_required()
def suggest_next_service(vehicle_id):
    """Suggest the next service due for a vehicle, based on its completed
    job order history matched against a fixed interval table."""
    identity = get_jwt_identity()
    if isinstance(identity, str):
        import json
        identity = json.loads(identity)

    role = identity.get('role')
    user_id = identity.get('id')

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        return jsonify({'error': 'Vehicle not found'}), 404

    if role == 'customer':
        user = User.query.get(user_id)
        customer = Customer.query.filter_by(email=user.email).first() if user else None
        if not customer or vehicle.customer_id != customer.id:
            return jsonify({'error': 'Unauthorized'}), 403
    elif role not in ('admin', 'staff', 'mechanic'):
        return jsonify({'error': 'Unauthorized'}), 403

    from models.job_order import JobOrder
    job_orders = JobOrder.query.filter_by(vehicle_id=vehicle_id, status='Completed', is_deleted=False).all()
    JobOrder.prefetch(job_orders)

    # Latest completed occurrence (by completed_at) per matched interval entry.
    latest_by_interval = {}
    for jo in job_orders:
        if not jo.completed_at:
            continue
        completed_at = jo.completed_at
        if isinstance(completed_at, str):
            try:
                completed_at = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
            except ValueError:
                continue
        appt = jo.appointment
        performed_names = [s.name for s in (appt.services if appt else []) if s and s.name]
        for name in performed_names:
            name_lower = name.lower()
            for keyword, interval_km, interval_months in _SERVICE_INTERVALS:
                if keyword in name_lower:
                    existing = latest_by_interval.get(keyword)
                    if not existing or completed_at > existing['completed_at']:
                        latest_by_interval[keyword] = {
                            'service_name': name,
                            'completed_at': completed_at,
                            'mileage': jo.mileage,
                            'interval_km': interval_km,
                            'interval_months': interval_months,
                        }

    if not latest_by_interval:
        return jsonify({
            'suggestion': None,
            'message': 'No completed service history yet for this vehicle.'
        }), 200

    now = datetime.utcnow()
    suggestions = []
    for entry in latest_by_interval.values():
        months_elapsed = _months_between(entry['completed_at'], now)
        months_remaining = entry['interval_months'] - months_elapsed

        km_remaining = None
        if vehicle.mileage is not None and entry['mileage'] is not None:
            km_elapsed = vehicle.mileage - entry['mileage']
            km_remaining = entry['interval_km'] - km_elapsed

        is_overdue = months_remaining <= 0 or (km_remaining is not None and km_remaining <= 0)
        # Rank by whichever runs out sooner: months_remaining alone if
        # mileage isn't tracked, otherwise the more urgent of the two.
        urgency_months = months_remaining
        if km_remaining is not None:
            # Rough km-per-month rate isn't tracked, so just let an overdue
            # mileage push this to the front regardless of date.
            urgency_months = min(months_remaining, months_remaining if km_remaining > 0 else -1)

        suggestions.append({
            'service_name': entry['service_name'],
            'last_completed_at': entry['completed_at'].isoformat(),
            'last_mileage': entry['mileage'],
            'due_by_months': entry['interval_months'],
            'months_remaining': months_remaining,
            'km_remaining': km_remaining,
            'is_overdue': is_overdue,
            '_urgency': urgency_months,
        })

    suggestions.sort(key=lambda s: s.pop('_urgency'))
    top = suggestions[0]

    return jsonify({'suggestion': top, 'all_due_soon': suggestions[:3]}), 200


@appointments_bp.route('/services', methods=['GET'])
@jwt_required()
def get_services():
    """Get available services, optionally filtered by selected vehicle compatibility.

    Query parameters:
    - vehicle_id: ID of selected vehicle
    """
    try:
        vehicle_id = request.args.get('vehicle_id')
        identity = get_jwt_identity()
        # The admin/staff Services management table needs to see deactivated
        # services too (to display them as Inactive and let them be
        # reactivated) — everyone else (customer booking, etc.) only ever
        # sees active ones. Without this, deactivating a service made it
        # disappear from the admin's own management page as well, with no
        # way to find and reactivate it.
        include_inactive = (
            request.args.get('include_inactive') == 'true'
            and identity.get('role') in ('admin', 'staff')
        )
        services = Service.query.all() if include_inactive else Service.query.filter_by(is_active=True).all()

        # No selected vehicle: return all (active, or active+inactive for
        # admin/staff) services
        if not vehicle_id:
            return jsonify([s.to_dict() for s in services]), 200

        vehicle = Vehicle.query.get(vehicle_id)
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404

        powertrain = normalize_vehicle_powertrain(vehicle)

        current_app.logger.debug('get_compatible_services: vehicle=%s powertrain=%s', vehicle_id, powertrain)

        # Try using compatibility matrix first (if configured)
        vtype = None
        compat_map = {}
        try:
            from models.enhanced_models import VehicleType, ServiceVehicleTypeCompatibility

            vtype = VehicleType.query.filter_by(fuel_type=powertrain).first()
            if vtype:
                rows = ServiceVehicleTypeCompatibility.query.filter_by(vehicle_type_id=vtype.id).all()
                compat_map = {row.service_id: bool(row.is_applicable) for row in rows}
                current_app.logger.debug('get_compatible_services: %d compatibility rules for %s', len(compat_map), powertrain)
            else:
                current_app.logger.debug('get_compatible_services: no vehicle_type record for powertrain %s', powertrain)
        except Exception as e:
            current_app.logger.debug('get_compatible_services: no compatibility table: %s', e)
            vtype = None
            compat_map = {}

        # First pass: determine which services are applicable
        applicable_map = {}  # service_id -> is_applicable
        for service in services:
            # 1) Explicit compatibility table has highest priority
            if service.id in compat_map:
                applicable_map[service.id] = bool(compat_map[service.id])
            else:
                # 2) Use service vehicle-type applicability flags
                is_applicable = is_service_applicable_by_flag(service, powertrain)
                applicable_map[service.id] = is_applicable
        
        # Second pass: handle parent-child relationships
        # Build lookup: parent_id -> [child1, child2, ...]
        children_by_parent = {}
        for service in services:
            if service.parent_id:
                if service.parent_id not in children_by_parent:
                    children_by_parent[service.parent_id] = []
                children_by_parent[service.parent_id].append(service)

        services_to_include = set()

        for service in services:
            if service.parent_id:
                # Child service: always include if parent exists (for dropdown display)
                # Mark whether it's applicable for this vehicle type
                services_to_include.add(service.id)
                # Also include parent as dropdown container
                services_to_include.add(service.parent_id)
            else:
                # Parent service: include if parent is applicable OR if any of its children are applicable
                if applicable_map[service.id]:
                    services_to_include.add(service.id)
                elif service.id in children_by_parent:
                    # Parent has children - always include as dropdown container
                    services_to_include.add(service.id)
        
        # Build response with included services
        compatible_services = [s.to_dict() for s in services if s.id in services_to_include]
        
        current_app.logger.debug('get_compatible_services: returning %d of %d services', len(compatible_services), len(services))
        
        # Include debug info in response (including applicability for each service)
        applicability_info = {}
        for s in services:
            if s.id in services_to_include:
                applicability_info[s.id] = applicable_map.get(s.id, True)
        
        return jsonify({
            'services': compatible_services,
            '_debug': {
                'vehicle_id': vehicle_id,
                'vehicle_type': vehicle.vehicle_type,
                'powertrain': powertrain,
                'size_class': getattr(vehicle, 'size_class', None),
                'total_services': len(services),
                'filtered_services': len(compatible_services)
            },
            '_applicability': applicability_info
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _apply_service_fields(service, data):
    """Shared field assignment for create/update — keeps the two endpoints
    (and their validation) in sync as fields get added."""
    for field in ('name', 'description', 'category', 'is_active'):
        if field in data:
            setattr(service, field, data[field])
    for field in ('applicable_gasoline', 'applicable_diesel', 'applicable_hev', 'applicable_phev', 'applicable_bev'):
        if field in data:
            setattr(service, field, bool(data[field]))
    if 'duration_minutes' in data:
        try:
            service.duration_minutes = int(data['duration_minutes']) if data['duration_minutes'] not in (None, '') else None
        except (TypeError, ValueError):
            return 'duration_minutes must be a whole number of minutes'
    for field in ('price', 'price_small', 'price_large'):
        if field not in data:
            continue
        if data[field] in (None, ''):
            if field == 'price':
                continue  # price is required elsewhere; skip rather than null it
            setattr(service, field, None)
            continue
        try:
            value = float(data[field])
            if value < 0:
                raise ValueError
        except (TypeError, ValueError):
            return f'{field} must be a non-negative number'
        setattr(service, field, value)
    return None


@appointments_bp.route('/services', methods=['POST'])
@jwt_required()
def create_service():
    """Create a new service, optionally with sub-services (admin/staff only)."""
    identity = get_jwt_identity()
    role = identity.get('role')

    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()

    name = data.get('name')
    price = data.get('price')

    if not name or price is None:
        return jsonify({'error': 'Name and price are required'}), 400

    parent_id = data.get('parent_id')
    if parent_id and not Service.get(parent_id):
        return jsonify({'error': 'Parent service not found'}), 404

    service = Service(
        name=name,
        price=price,
        description=data.get('description'),
        category=data.get('category'),
        duration_minutes=data.get('duration_minutes'),
        parent_id=parent_id,
        price_small=data.get('price_small'),
        price_large=data.get('price_large'),
        applicable_gasoline=data.get('applicable_gasoline', True),
        applicable_diesel=data.get('applicable_diesel', True),
        applicable_hev=data.get('applicable_hev', True),
        applicable_phev=data.get('applicable_phev', True),
        applicable_bev=data.get('applicable_bev', True),
    )
    db.session.add(service)
    db.session.commit()

    sub_services = []
    for sub_data in data.get('sub_services', []):
        if not sub_data.get('name') or sub_data.get('price') is None:
            continue
        sub = Service(
            name=sub_data['name'],
            price=sub_data['price'],
            description=sub_data.get('description'),
            category=sub_data.get('category') or service.category,
            duration_minutes=sub_data.get('duration_minutes'),
            parent_id=service.id,
            price_small=sub_data.get('price_small'),
            price_large=sub_data.get('price_large'),
            applicable_gasoline=sub_data.get('applicable_gasoline', True),
            applicable_diesel=sub_data.get('applicable_diesel', True),
            applicable_hev=sub_data.get('applicable_hev', True),
            applicable_phev=sub_data.get('applicable_phev', True),
            applicable_bev=sub_data.get('applicable_bev', True),
        )
        db.session.add(sub)
        sub_services.append(sub)
    if sub_services:
        db.session.commit()

    result = service.to_dict()
    result['sub_services'] = [s.to_dict() for s in sub_services]
    return jsonify({
        'message': 'Service created successfully',
        'service': result
    }), 201


@appointments_bp.route('/services/<service_id>', methods=['PUT'])
@jwt_required()
def update_service(service_id):
    """Update a service's details, pricing, applicability, or parent link
    (admin/staff only — this used to be admin-only pricing, but description/
    category/sub-service management needs staff access same as create)."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized'}), 403
    service = Service.get(service_id)
    if not service:
        return jsonify({'error': 'Service not found'}), 404
    data = request.get_json(silent=True) or {}

    if 'parent_id' in data:
        parent_id = data['parent_id'] or None
        if parent_id == service.id:
            return jsonify({'error': 'A service cannot be its own parent'}), 400
        if parent_id and not Service.get(parent_id):
            return jsonify({'error': 'Parent service not found'}), 404
        service.parent_id = parent_id

    error = _apply_service_fields(service, data)
    if error:
        return jsonify({'error': error}), 400

    service.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Service updated', 'service': service.to_dict()}), 200


@appointments_bp.route('/services/<service_id>', methods=['DELETE'])
@jwt_required()
def delete_service(service_id):
    """Deactivate a service (soft delete — services are transactional data
    referenced by historical appointments' service_ids, matching the
    no-hard-delete precedent used for customers/inventory/packages)."""
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized'}), 403
    service = Service.get(service_id)
    if not service:
        return jsonify({'error': 'Service not found'}), 404
    service.is_active = False
    service.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Service deactivated'}), 200


@appointments_bp.route('/pricing-settings', methods=['GET', 'PUT'])
@jwt_required()
def pricing_settings():
    identity = get_jwt_identity()
    if identity.get('role') not in ('admin', 'staff'):
        return jsonify({'error': 'Unauthorized'}), 403
    setting = SystemSetting.pricing()
    if request.method == 'PUT':
        if identity.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        try:
            markup = float((request.get_json(silent=True) or {}).get('quotation_markup'))
            if markup < 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({'error': 'quotation_markup must be a non-negative number'}), 400
        setting.quotation_markup = markup
        setting.updated_at = datetime.utcnow()
        setting.save()
    return jsonify({'quotation_markup': setting.quotation_markup}), 200


@appointments_bp.route('/approved', methods=['GET'])
@jwt_required()
def get_approved_appointments():
    """Get all approved appointments (for job order creation)."""
    identity = get_jwt_identity()
    role = identity.get('role')
    
    if role not in ['admin', 'staff']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Get approved appointments that don't have a job order yet
    from models.job_order import JobOrder
    approved = Appointment.query.filter_by(status='Approved').all()

    # One query per 30 appointments for their job orders, instead of a
    # filter_by().first() per appointment (was N+1 Firestore round trips
    # just to find which ones still need a job order). Chunked at 30 —
    # Firestore's 'in' filter cap (see JobOrder.prefetch for the same limit).
    appt_ids = [a.id for a in approved]
    appt_ids_with_jo = set()
    for i in range(0, len(appt_ids), 30):
        chunk = appt_ids[i:i + 30]
        appt_ids_with_jo.update(
            jo.appointment_id
            for jo in JobOrder.query.filter(JobOrder.appointment_id.in_(chunk)).all()
        )
    result_appts = [a for a in approved if a.id not in appt_ids_with_jo]

    # Batch-load customer/vehicle/services/packages for the survivors instead
    # of one round trip per relation per appointment inside to_dict().
    Appointment.prefetch(result_appts)

    return jsonify([apt.to_dict() for apt in result_appts]), 200
