"""Firebase initialization for the Automan backend.

Uses the Firebase Admin SDK to:
- Initialize the app from a service account (path via env or default file)
- Expose the Auth and Firestore clients
- Provide an ID-token verifier used by the API layer

Environment variables (set in .env or your shell):
- FIREBASE_CREDENTIALS: path to the serviceAccountKey.json (default: ./firebase-credentials.json)
- FIREBASE_PROJECT_ID: optional override
"""
import os
import json

import firebase_admin
from firebase_admin import credentials, auth as firebase_auth, firestore

# Allow an offline in-memory Firestore mock for testing the data layer without
# a real Firebase project (set FIRESTORE_MOCK=1).
USE_MOCK = os.environ.get("FIRESTORE_MOCK") == "1"

_app = None
_db = None
_auth = None


def _resolve_credentials():
    if USE_MOCK:
        return None
    path = os.environ.get("FIREBASE_CREDENTIALS") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "firebase-credentials.json"
    )
    if os.path.exists(path):
        return credentials.Certificate(path)
    return credentials.ApplicationDefault()


def init_firebase():
    """Initialize the Firebase Admin SDK exactly once."""
    global _app, _db, _auth
    if _app is not None:
        return _app

    if USE_MOCK:
        import mock_firestore
        _app = "mock"
        _db = mock_firestore.get_client()
        _auth = MockAuth()
        return _app

    cred = _resolve_credentials()
    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    options = {"projectId": project_id} if project_id else {}
    _app = firebase_admin.initialize_app(cred, options)
    _db = firestore.client()
    _auth = firebase_auth
    return _app


class MockAuth:
    """Minimal Firebase Auth stand-in for offline testing."""
    def __init__(self):
        self._users = {}

    def create_user(self, **kwargs):
        import uuid
        uid = kwargs.get("uid") or uuid.uuid4().hex
        self._users[uid] = {
            "uid": uid,
            "email": kwargs.get("email"),
            "display_name": kwargs.get("display_name"),
            "phone_number": kwargs.get("phone_number"),
            "email_verified": False,
            "disabled": kwargs.get("disabled", False),
        }
        return type("FBUser", (), self._users[uid])()

    def update_user(self, uid, **kwargs):
        if uid in self._users:
            self._users[uid].update(kwargs)

    def get_user(self, uid):
        u = self._users.get(uid)
        if not u:
            return None
        return type("FBUser", (), u)()

    def get_user_by_email(self, email):
        for u in self._users.values():
            if u.get("email") == email:
                return type("FBUser", (), u)()
        raise ValueError(f"No user found for email: {email}")

    def delete_user(self, uid):
        self._users.pop(uid, None)


def get_db():
    """Return the Firestore client, initializing Firebase if needed."""
    global _db
    if _db is None:
        init_firebase()
    return _db


def get_auth():
    """Return the Firebase Auth client."""
    global _auth
    if _auth is None:
        init_firebase()
    return _auth


def verify_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token (from client sign-in) and return its claims."""
    # Tolerate small clock drift between this machine and Firebase's servers
    # ("Token used too early" errors when the local clock is a few seconds off).
    return get_auth().verify_id_token(id_token, clock_skew_seconds=30)


def decode_token_unverified(id_token: str) -> dict:
    """Decode a token without signature verification (testing only)."""
    import firebase_admin.auth as _a
    return _a.verify_id_token(id_token, check_revoked=False)


def create_firebase_user(email, password, display_name=None, phone_number=None, disabled=False):
    """Create a Firebase Authentication user. Returns the Firebase UID."""
    user_kwargs = {"email": email, "password": password, "disabled": disabled}
    if display_name:
        user_kwargs["display_name"] = display_name
    if phone_number:
        user_kwargs["phone_number"] = phone_number
    return get_auth().create_user(**user_kwargs)


def set_user_password(uid, password):
    get_auth().update_user(uid, password=password)


def get_firebase_user(uid):
    try:
        return get_auth().get_user(uid)
    except Exception:
        return None


def get_firebase_user_by_email(email):
    try:
        return get_auth().get_user_by_email(email)
    except Exception:
        return None


def delete_firebase_user(uid):
    """Delete a Firebase Auth account. No-op if it doesn't exist."""
    try:
        get_auth().delete_user(uid)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Email/password -> ID token exchange (Firebase REST API)
# ---------------------------------------------------------------------------
# Firebase Admin cannot verify a raw password, so password login is done by
# exchanging email+password for an ID token via the public Identity Toolkit
# REST endpoint. This requires the project's WEB API KEY.
_API_KEY = None


def get_api_key():
    """Return the Firebase web API key (from FIREBASE_API_KEY or cached)."""
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    _API_KEY = os.environ.get("FIREBASE_API_KEY")
    return _API_KEY


def set_api_key(key):
    global _API_KEY
    _API_KEY = key


def exchange_password_for_token(email, password):
    """Exchange email+password for a Firebase ID token using the REST API.

    Returns the ID token string, or raises an exception with a readable error.
    Requires FIREBASE_API_KEY to be set (the project's Web API Key from
    Firebase console -> Project settings -> General).
    """
    return exchange_password_for_token_full(email, password)["idToken"]


def exchange_password_for_token_full(email, password):
    """Same as ``exchange_password_for_token`` but returns the full REST
    response (``idToken``, ``refreshToken``, ``expiresIn``), so callers that
    need to let the client refresh its session later (see
    ``refresh_id_token``) have a refresh token to hand back.
    """
    import requests

    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "FIREBASE_API_KEY is not set. Add your Firebase Web API Key to .env "
            "to enable password login. (Project settings -> General -> Web API Key)"
        )

    project_id = os.environ.get("FIREBASE_PROJECT_ID")
    if not project_id:
        try:
            project_id = json.load(open(
                os.environ.get("FIREBASE_CREDENTIALS") or "firebase-credentials.json"
            )).get("project_id")
        except Exception:
            project_id = None

    url = (
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
        f"?key={api_key}"
    )
    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    data = resp.json()
    if resp.status_code != 200 or "idToken" not in data:
        # Firebase returns error messages like "INVALID_PASSWORD", "EMAIL_NOT_FOUND"
        message = data.get("error", {}).get("message", "Authentication failed")
        raise RuntimeError(message)
    return data


def refresh_id_token(refresh_token):
    """Exchange a Firebase refresh token for a new ID token.

    Firebase ID tokens always expire after 1 hour and this app has no other
    way to renew one (the Admin SDK can't issue one for a live client
    session) — the client must call this before/when its ID token expires.
    Uses the public securetoken.googleapis.com REST endpoint, which (unlike
    signInWithPassword) returns snake_case fields.

    Returns a dict with ``idToken`` / ``refreshToken`` / ``expiresIn`` keys
    (normalized to match ``exchange_password_for_token_full``), or raises
    RuntimeError with a readable message.
    """
    import requests

    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("FIREBASE_API_KEY is not set.")

    url = f"https://securetoken.googleapis.com/v1/token?key={api_key}"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = requests.post(url, data=payload, timeout=15)
    data = resp.json()
    if resp.status_code != 200 or "id_token" not in data:
        message = data.get("error", {}).get("message", "Token refresh failed")
        raise RuntimeError(message)
    return {
        "idToken": data["id_token"],
        "refreshToken": data["refresh_token"],
        "expiresIn": data.get("expires_in"),
    }
