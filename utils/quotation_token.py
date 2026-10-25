"""Signed, expiring tokens for the customer-facing quotation approve/reject
links sent by SMS.

Previously those links only relied on the appointment's raw Firestore
document id being hard to guess — anyone who saw/forwarded/leaked the link
(or a messaging app's link-preview crawler prefetching it) could approve or
reject someone else's appointment with no proof they were the customer it
was sent to. This ties each link to a signed token minted only when the
quotation SMS is actually sent, so knowing the id alone is no longer enough.
"""
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app

SALT = 'quotation-approval'
MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # matches the "valid for 7 days" text on the quotation page


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def generate_quotation_token(appointment_id):
    return _serializer().dumps(str(appointment_id), salt=SALT)


def verify_quotation_token(appointment_id, token):
    """Return True only if token was minted for this exact appointment id
    and hasn't expired."""
    if not token:
        return False
    try:
        value = _serializer().loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return value == str(appointment_id)
