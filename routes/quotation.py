from flask import Blueprint, request, jsonify, redirect, url_for, render_template, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from models import db
from models.appointment import Appointment
from utils.sms_utils import send_sms as iprog_send_sms

quotation_bp = Blueprint('quotation', __name__)


@quotation_bp.route('/approve/<appointment_id>', methods=['GET', 'POST'])
def approve_quotation(appointment_id):
    """
    Public URL for customers to approve quotation.
    Customers can access this without login via the SMS link.
    """
    appointment = Appointment.query.get(appointment_id)
    
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    if appointment.status not in ['Pending', 'Quotation Sent']:
        return jsonify({'error': 'This appointment is no longer pending quotation'}), 400
    
    # Approve the quotation
    appointment.status = 'Approved'
    db.session.commit()
    
    # Send SMS notification to customer confirming approval
    current_app.logger.debug('approve_quotation %s: customer_id=%s', appointment.id, getattr(appointment.customer, 'id', None))
    try:
        from utils.sms_utils import send_sms
        customer = appointment.vehicle.customer if appointment.vehicle else appointment.customer
        if customer and hasattr(customer, 'phone') and customer.phone:
            message = f"Automan Car Care: Your appointment has been SCHEDULED and APPROVED for {appointment.preferred_date} at {appointment.preferred_time}. See you!"
            result = send_sms(customer.phone, message)
            current_app.logger.debug(
                'approve_quotation %s: SMS success=%s status=%s',
                appointment.id, result.get('success'), result.get('status')
            )
    except Exception as e:
        current_app.logger.exception('approve_quotation %s: error sending approval SMS: %s', appointment.id, e)
    
    # Return success page (for web) or JSON (for API)
    accept_header = request.headers.get('Accept', '')
    if 'text/html' in accept_header:
        return render_template('quotation_approved.html', job_order_number=f"JO-{appointment.id:05d}")
    
    return jsonify({
        'message': 'Quotation approved successfully!',
        'appointment': appointment.to_dict()
    }), 200


@quotation_bp.route('/reject/<appointment_id>', methods=['GET', 'POST'])
def reject_quotation(appointment_id):
    """
    Public URL for customers to reject quotation.
    Customers can access this without login via the SMS link.
    """
    appointment = Appointment.query.get(appointment_id)
    
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    if appointment.status not in ['Pending', 'Quotation Sent']:
        return jsonify({'error': 'This appointment is no longer pending quotation'}), 400
    
    # Reject the quotation
    appointment.status = 'Rejected'
    db.session.commit()
    
    # Return success page (for web) or JSON (for API)
    accept_header = request.headers.get('Accept', '')
    if 'text/html' in accept_header:
        return render_template('quotation_rejected.html')
    
    return jsonify({
        'message': 'Quotation rejected',
        'appointment': appointment.to_dict()
    }), 200


@quotation_bp.route('/view/<appointment_id>')
def view_quotation(appointment_id):
    """
    Public URL for customers to view and respond to quotation.
    Customers can view the quotation details and approve/reject without SMS.
    """
    appointment = Appointment.query.get(appointment_id)
    
    if not appointment:
        return render_template('appointment_not_found.html'), 404
    
    if appointment.status not in ['Pending', 'Quotation Sent']:
        return render_template('quotation_processed.html', appointment=appointment)
    
    # Prepare data for template
    customer = appointment.vehicle.customer if appointment.vehicle else appointment.customer
    vehicle = appointment.vehicle
    services = appointment.services
    packages = appointment.packages

    # Check Accept header for API vs HTML response
    accept_header = request.headers.get('Accept', '')
    if 'text/html' in accept_header:
        return render_template('quotation_view.html',
                               appointment=appointment,
                               vehicle=vehicle,
                               services=services,
                               packages=packages)


def api_approve_quotation(appointment_id):
    """API endpoint for approving quotation."""
    appointment = Appointment.query.get(appointment_id)
    
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    if appointment.status not in ['Pending', 'Quotation Sent']:
        return jsonify({'error': 'This appointment is no longer pending quotation'}), 400
    
    # Approve the quotation
    appointment.status = 'Approved'
    db.session.commit()
    
    # Send SMS notification to customer
    try:
        from utils.sms_utils import send_sms
        customer = appointment.vehicle.customer if appointment.vehicle else appointment.customer
        if customer and hasattr(customer, 'phone') and customer.phone:
            message = f"Automan Car Care: Your appointment has been SCHEDULED and APPROVED for {appointment.preferred_date} at {appointment.preferred_time}. See you!"
            send_sms(customer.phone, message)
    except Exception as e:
        current_app.logger.exception('api_approve_quotation %s: error sending approval SMS: %s', appointment_id, e)

    return jsonify({
        'message': 'Quotation approved successfully!',
        'appointment': appointment.to_dict()
    }), 200


@quotation_bp.route('/api/reject/<appointment_id>', methods=['POST'])
def api_reject_quotation(appointment_id):
    """API endpoint for rejecting quotation."""
    appointment = Appointment.query.get(appointment_id)
    
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    if appointment.status not in ['Pending', 'Quotation Sent']:
        return jsonify({'error': 'This appointment is no longer pending quotation'}), 400
    
    # Get rejection reason if provided
    data = request.get_json() or {}
    reason = data.get('notes') or data.get('reason') or ''
    current_app.logger.debug('api_reject_quotation %s: reason provided=%s', appointment_id, bool(reason))
    if reason:
        appointment.notes = reason
    
    # Reject the quotation
    appointment.status = 'Rejected'
    db.session.commit()
    
    # Send SMS notification to customer
    customer = appointment.vehicle.customer if appointment.vehicle else None
    if customer and customer.phone:
        current_app.logger.debug('api_reject_quotation %s: sending rejection SMS', appointment_id)
        if reason:
            message = f"Automan Car Care: Your appointment has been REJECTED. Reason: {reason}"
        else:
            message = "Automan Car Care: Your appointment has been REJECTED. Please contact us for more information."
        try:
            iprog_send_sms(customer.phone, message)
        except Exception as e:
            current_app.logger.exception('api_reject_quotation %s: failed to send rejection SMS: %s', appointment_id, e)
    
    return jsonify({
        'message': 'Quotation rejected',
        'appointment': appointment.to_dict()
    }), 200
