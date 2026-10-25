"""Push notification utilities - Firebase Cloud Messaging (FCM).

Reuses this project's existing Firebase Admin SDK credentials (see
firebase_config.py) -- Cloud Messaging lives under the same Firebase
project as Firestore/Auth, so no separate setup is needed on the backend
beyond the google-services.json the Android app itself needs (see
automan-customer-app's README/setup notes).
"""
import os
from flask import current_app


def _test_mode_active():
    """Same guard as utils/email_utils.py / utils/sms_utils.py -- never send
    a real push during a test run. Checked at call time (not import time)
    so conftest.py's os.environ.setdefault() -- which runs before this
    module is ever imported -- is what actually decides this in tests."""
    return os.environ.get('FIRESTORE_MOCK') == '1' or os.environ.get('TESTING') == '1'


def send_push(tokens, title, body, data=None):
    """Send one push notification to one or more FCM device tokens.

    Args:
        tokens: a device token string, or a list of them. Falsy/empty
            entries are dropped silently (a user with no registered device
            yet is a normal, expected case, not an error).
        title, body: the notification's visible text.
        data: optional dict of extra string data delivered alongside the
            notification (e.g. {'type': 'job_order_started', 'job_order_id': ...})
            for the app to act on if it wants to (e.g. deep-link on tap).

    Returns a result dict, e.g.:
        {'success': True, 'status': 'sent', 'success_count': 2, 'failure_count': 0, 'invalid_tokens': []}
        {'success': False, 'error': 'No device tokens'}

    Never raises -- same "log a warning, keep going" contract as
    models/enhanced_models.py's notify_low_stock() and the SMS/email
    senders: a push failure must never block whatever real action
    triggered it (a stock deduction, a job order status change).
    """
    if isinstance(tokens, str):
        tokens = [tokens]
    tokens = [t for t in (tokens or []) if t]
    if not tokens:
        return {'success': False, 'error': 'No device tokens'}

    if _test_mode_active():
        current_app.logger.info(
            'send_push: test mode active -- not sending a real push (title=%r, %d token(s))',
            title, len(tokens),
        )
        return {
            'success': True, 'status': 'sent', 'test_mode': True,
            'tokens': tokens, 'title': title, 'body': body, 'data': data or {},
        }

    try:
        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            tokens=tokens,
        )
        response = messaging.send_each_for_multicast(message)
        current_app.logger.info(
            'send_push: %d succeeded, %d failed (title=%r)',
            response.success_count, response.failure_count, title,
        )

        # FCM reports a per-token result; a token can go stale (app
        # uninstalled, user logged out elsewhere) without our code ever
        # being told directly -- prune those here so User.fcm_tokens
        # doesn't quietly accumulate dead entries forever.
        invalid_tokens = [
            tokens[i] for i, r in enumerate(response.responses)
            if not r.success and _is_unregistered(r.exception)
        ]

        return {
            'success': response.success_count > 0,
            'status': 'sent',
            'success_count': response.success_count,
            'failure_count': response.failure_count,
            'invalid_tokens': invalid_tokens,
        }
    except Exception as e:
        current_app.logger.exception('send_push: failed to send (title=%r): %s', title, e)
        return {'success': False, 'error': str(e)}


def _is_unregistered(exception):
    """True if an FCM send error means the token is permanently dead
    (app uninstalled, token rotated) rather than a transient failure."""
    if exception is None:
        return False
    code = getattr(exception, 'code', '') or ''
    return 'UNREGISTERED' in str(code).upper() or 'not-registered' in str(exception).lower()


def prune_invalid_tokens(user, invalid_tokens):
    """Remove dead tokens from a user's device list and save, if any.
    Small helper so every call site doesn't repeat the same three lines."""
    if not invalid_tokens:
        return
    for token in invalid_tokens:
        user.remove_fcm_token(token)
    user.save()
