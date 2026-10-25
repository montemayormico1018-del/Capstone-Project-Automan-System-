"""Compatibility shim: provides the flask_jwt_extended API used by the routes,
backed by Firebase Authentication (ID-token verification).

This lets the existing route files continue to call:
    from flask_jwt_extended import jwt_required, get_jwt_identity
    @jwt_required()
    identity = get_jwt_identity()   # -> {'id': <firestore uid>, 'role': .., 'username': ..}

The real auth (token issuance / user creation) lives in routes/auth.py which
talks to Firebase Auth directly. This module only *verifies* inbound tokens.
"""
from firebase_auth_utils import (
    firebase_required as _firebase_required,
    get_jwt_identity,
    get_jwt,
)


def jwt_required(*args, **kwargs):
    """Mirror flask_jwt_extended.jwt_required() which returns a decorator."""
    def decorator(f):
        return _firebase_required(f)
    return decorator

# The original extension exposes a JWTManager that the app used to instantiate.
class JWTManager:
    def __init__(self, app=None):
        self.app = app

    def init_app(self, app):
        self.app = app

    # Loader decorators are no-ops under Firebase Auth.
    def expired_token_loader(self, fn): return fn
    def invalid_token_loader(self, fn): return fn
    def unauthorized_loader(self, fn): return fn
    def revoked_token_loader(self, fn): return fn
    def needs_fresh_token_loader(self, fn): return fn
    def user_lookup_loader(self, fn): return fn
    def token_in_blocklist_loader(self, fn): return fn


# Token creation helper (mirrors create_access_token) — not used for issuing
# (the client gets tokens from Firebase), but kept for API parity.
def create_access_token(identity, *args, **kwargs):
    # Firebase clients obtain tokens client-side; we return the identity as a
    # marker. Real issuance happens in the client SDK.
    return identity


def decode_token(token, *args, **kwargs):
    from firebase_auth_utils import get_jwt
    return get_jwt()


__all__ = [
    "JWTManager",
    "jwt_required",
    "create_access_token",
    "get_jwt_identity",
    "get_jwt",
    "decode_token",
]
