"""Firebase Auth helpers - replaces flask_jwt_extended usage.

Client signs in with Firebase (web/Flutter/JS SDK) and sends the resulting
ID token in the ``Authorization: Bearer <id_token>`` header. These helpers
verify that token, resolve the Firebase UID, and load the matching Firestore
user profile (``users/{uid}``).

Drop-in replacements for the JWT decorators used in routes:
- ``@jwt_required()``  ->  ``@firebase_required``
- ``get_jwt_identity()``  ->  ``current_user`` (the Firestore User object)
"""
from functools import wraps
from flask import request, jsonify

from firebase_config import verify_id_token, get_auth
from models.user import User


def _extract_token():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header:
        return None
    parts = auth_header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == 'bearer':
        return parts[1].strip()
    return auth_header.strip()


def get_current_user():
    """Resolve the authenticated Firestore User from the request, or None.

    A valid Firebase ID token alone is not enough — the /login endpoint's
    own is_active check can be bypassed entirely by minting a token directly
    against Firebase (e.g. the public Identity Toolkit REST API) instead of
    going through this app's /login, since the account and its password
    already exist in Firebase Auth before OTP verification completes. This
    check must live here, not just in /login, so every endpoint enforces it.
    """
    token = _extract_token()
    if not token:
        return None
    try:
        claims = verify_id_token(token)
    except Exception:
        return None
    uid = claims.get('uid')
    if not uid:
        return None
    user = User.get(uid)
    if user is None:
        return None
    if not user.is_active or not user.is_verified:
        return None
    return user


def firebase_required(f):
    """Decorator requiring a valid Firebase ID token.

    Injects ``current_user`` into the route via flask's ``g`` and returns the
    user when the wrapped function requests a ``current_user`` kwarg.
    """
    from flask import g

    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return jsonify({'msg': 'Missing or invalid token'}), 401

        # Single-login enforcement: /login stamps a fresh active_session_id
        # on the user doc and hands it to that browser only. Any other
        # browser's requests carry a now-stale X-Session-Id header and are
        # rejected here, so an account can never stay logged in from two
        # places at once. Requests with no header (older cached clients)
        # are let through rather than breaking them outright.
        session_id = request.headers.get('X-Session-Id')
        if user.active_session_id and session_id and session_id != user.active_session_id:
            return jsonify({
                'msg': 'This account was signed in on another device or browser.',
                'session_conflict': True
            }), 401

        g.current_user = user
        return f(*args, **kwargs)

    return wrapper


# Mimic flask_jwt_extended API names used across routes
def jwt_required():
    return firebase_required


def get_jwt_identity():
    """Return the current user's identity dict (id/role/username).

    ``id`` is the Firestore document id (string). Routes that previously
    compared against integer URL params should treat ids as strings now.
    """
    from flask import g
    user = getattr(g, 'current_user', None)
    if user is None:
        user = get_current_user()
    if user is None:
        return None
    return {'id': str(user.id), 'role': user.role, 'username': user.username}


def get_jwt():
    """Return the decoded claims for the current request."""
    token = _extract_token()
    if not token:
        return {}
    try:
        return verify_id_token(token)
    except Exception:
        return {}
