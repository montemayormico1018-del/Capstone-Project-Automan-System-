"""Signed, expiring tokens for the "forgot password" email link.

Mirrors utils/quotation_token.py's approach (itsdangerous, signed with the
app's SECRET_KEY, salted, time-limited) but for a different, higher-stakes
purpose, so it gets its own salt and its own module rather than reusing that
one directly.

A valid signature + unexpired age alone is not enough to make a link
single-use or revocable early (e.g. after it's already been used once, or
after a newer reset was requested). That's handled on top of this by
routes/auth.py, which stores a hash of the minted token plus its own expiry
on the user document and checks both.
"""
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app

SALT = 'password-reset'
MAX_AGE_SECONDS = 30 * 60  # 30 minutes


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'])


def generate_reset_token(uid):
    return _serializer().dumps(str(uid), salt=SALT)


def verify_reset_token(token):
    """Return the uid the token was minted for, or None if invalid/expired."""
    if not token:
        return None
    try:
        return _serializer().loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
