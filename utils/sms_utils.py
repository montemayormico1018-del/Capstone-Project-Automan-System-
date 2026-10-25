"""
SMS Utility Functions - iProg SMS API Integration
==================================================

API Provider: iProg SMS
API Hash: read from the IPROG_API_HASH environment variable (see .env.example).

The token used to live here as a literal -- anyone with a copy of this
codebase (or a production container image built from it) could read it and
send SMS on the shop's paid account. IMPORTANT: if this code has ever been
shared/committed with a real token in it, that old value must be rotated in
the iProg dashboard -- moving it to an env var only stops it from appearing
in the code going forward, it does not revoke an already-exposed value.
"""

import os
import re
import requests
from datetime import datetime
from flask import current_app


# =============================================================================
# CONFIGURATION
# =============================================================================

# iProg SMS API credentials
IPROG_API_HASH = os.environ.get('IPROG_API_HASH')


# Correct API endpoints based on official docs
IPROG_URL = 'https://www.iprogsms.com/api/v1/sms_messages'
IPROG_URL_ALT = 'https://sms.iprogtech.com/api/v1/sms_messages'

# Enable real SMS (set to False to disable)
SMS_ENABLED = True


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def normalize_phone(phone):
    """
    Normalize Philippine phone number to 11-digit format.
    """
    if not phone:
        return None
    
    digits = re.sub(r'\D', '', phone)
    
    if len(digits) == 11 and digits.startswith('09'):
        return digits
    elif len(digits) == 10 and digits.startswith('9'):
        return '0' + digits
    elif digits.startswith('63') and len(digits) >= 11:
        return '0' + digits[2:12]
    elif len(digits) == 9:
        return '09' + digits
    else:
        return phone


def check_sender_status():
    """Check the status of sender names on the iProg account."""
    return []


# =============================================================================
# MAIN SMS FUNCTION
# =============================================================================

def send_sms(number, message):
    """
    Send SMS via iProg SMS API.
    
    Args:
        number: Philippine phone number (e.g., '09123456789')
        message: SMS message content
    
    Returns:
        dict with success status and details
    """
    
    # Step 1: Normalize the phone number
    number = normalize_phone(number)
    
    if not number:
        current_app.logger.error('send_sms: invalid phone number')
        return {'success': False, 'error': 'Invalid phone number'}

    # Step 2: Check if SMS is enabled
    if not SMS_ENABLED:
        current_app.logger.info('send_sms: SMS disabled, message not actually sent')
        return {
            'success': True,
            'status': 'disabled',
            'to': number,
            'body': message,
            'message': 'SMS disabled'
        }
    
    if not IPROG_API_HASH:
        message_txt = 'IPROG_API_HASH is not set in .env'
        current_app.logger.error('send_sms: %s', message_txt)
        return {'success': False, 'error': message_txt}

    # Step 3: Build the payload for iProg (API v1 format)
    # API expects JSON body with Content-Type: application/json
    payload = {
        'api_token': IPROG_API_HASH,
        'phone_number': number,
        'message': message,
    }

    headers = {
        'Content-Type': 'application/json'
    }

    # No phone number or message text logged anywhere below -- both are
    # customer PII/content. Endpoint URLs, HTTP status codes, and the
    # provider's own status/error fields aren't, and are what's actually
    # useful for diagnosing a delivery failure.
    current_app.logger.debug('send_sms: sending via iProg API')

    # Step 5: Send the SMS - try both endpoints
    response = None
    urls_to_try = [IPROG_URL, IPROG_URL_ALT]

    for url in urls_to_try:
        try:
            current_app.logger.debug('send_sms: trying %s', url)
            # Use POST with JSON body
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            current_app.logger.debug('send_sms: %s responded with status %s', url, response.status_code)
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if 'status' in result or 'message' in result:
                        current_app.logger.debug('send_sms: got a valid response from %s', url)
                        break
                except Exception:
                    pass

            current_app.logger.debug('send_sms: %s did not return a usable response', url)
        except Exception as e:
            current_app.logger.warning('send_sms: request to %s failed: %s', url, e)
            continue

    if response is None or response.status_code != 200:
        return {'success': False, 'error': 'All endpoints failed', 'tried_urls': urls_to_try}

    current_app.logger.debug('send_sms: final response status=%s', response.status_code)

    # Step 6: Check if successful - API returns JSON with status field
    if response.status_code == 200:
        try:
            result = response.json()
        except Exception:
            return {'success': False, 'error': 'Invalid JSON response', 'raw_response': response.text}

        # Check for success - look at JSON status field, not HTTP status
        api_status = result.get('status', '')
        if api_status == 'success':
            current_app.logger.info('send_sms: sent successfully')
            return {
                'success': True,
                'status': 'sent',
                'message_id': result.get('message_id'),
                'to': number,
                'body': message
            }

        # Check if message indicates success even without status: success
        message_val = result.get('message', '')
        if 'success' in str(message_val).lower():
            current_app.logger.info('send_sms: sent successfully')
            return {
                'success': True,
                'status': 'sent',
                'message_id': result.get('message_id'),
                'to': number,
                'body': message
            }

        error_msg = result.get('message', result.get('error', 'Unknown error'))
        current_app.logger.warning('send_sms: API error: %s', error_msg)
        return {'success': False, 'error': error_msg, 'response': result}

    current_app.logger.warning('send_sms: HTTP error %s', response.status_code)
    return {'success': False, 'error': f'HTTP {response.status_code}', 'response': response.text}


# =============================================================================
# LEGACY FUNCTION
# =============================================================================

def send_sms_notification(phone_number, message, appointment_id=None, job_order_id=None):
    """Legacy function - wraps send_sms with database logging."""
    from models import db
    from models.sms_log import SMSLog
    
    result = send_sms(phone_number, message)
    
    # Ensure result is a dict before trying to access it
    if not isinstance(result, dict):
        current_app.logger.warning('send_sms_notification: send_sms returned unexpected type %s', type(result))
        result = {'success': False, 'status': 'error', 'error': 'Invalid response from send_sms'}

    try:
        sms_status = result.get('status', 'unknown')
        sms_log = SMSLog(
            phone_number=phone_number,
            message=message,
            status=sms_status,
            related_appointment_id=appointment_id,
            related_job_order_id=job_order_id
        )
        db.session.add(sms_log)
        db.session.commit()
    except Exception as e:
        current_app.logger.exception('send_sms_notification: could not log SMS to database: %s', e)
        # Continue anyway - don't let logging failure affect SMS result
    
    # Always return a dict, not the SMSLog object
    return result


def send_quotation_sms(appointment, service_estimate):
    """Send quotation SMS with approval link to customer."""
    phone = None
    if appointment.vehicle and appointment.vehicle.customer:
        phone = appointment.vehicle.customer.phone
    
    if not phone:
        current_app.logger.warning('send_quotation_sms: no phone number found for appointment %s', appointment.id)
        return {'error': 'No phone number found'}
    
    # Include BOTH approve and view links
    approve_link = f"http://localhost:5000/quotation/approve/{appointment.id}"
    view_link = f"http://localhost:5000/quotation/view/{appointment.id}"
    
    message = f"Quotation for your vehicle service: PHP {service_estimate:.2f}. View details: {view_link}"
    
    return send_sms(phone, message)


# =============================================================================
# TEST FUNCTION
# =============================================================================

if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("SMS TEST")
    print("=" * 50 + "\n")
    
    result = send_sms('09762409060', 'Test message from Automan Car Care')
    
    print("\nResult:")
    print(result)
