from flask import Blueprint, request, jsonify, current_app
from models import db
from models.user import User
from datetime import datetime, timedelta, timezone
from utils.email_utils import (
    generate_otp, send_otp_email, send_welcome_email, send_password_reset_email,
)
from utils.password_reset_token import generate_reset_token, verify_reset_token
import hashlib
import os
import re
import uuid

from firebase_config import (
    verify_id_token, get_auth, create_firebase_user, set_user_password,
    exchange_password_for_token, exchange_password_for_token_full,
    refresh_id_token,
)
from firebase_auth_utils import get_current_user
from utils.audit import log_audit

auth_bp = Blueprint('auth', __name__)


def _is_expired(expires_at):
    """Compare an OTP expiry against now, safely.

    verification_expires is set with a naive datetime.utcnow(), but Firestore
    always returns stored datetimes as timezone-aware (UTC) once round-tripped,
    so comparing it directly against a fresh datetime.utcnow() raises
    TypeError: can't compare offset-naive and offset-aware datetimes.
    """
    if expires_at is None:
        return False
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


def is_ngrok_access():
    """Check if request is coming from ngrok (public access)."""
    origin = request.headers.get('Origin', '')
    referer = request.headers.get('Referer', '')
    host = request.headers.get('Host', '')

    ngrok_indicators = ['ngrok', 'ngrok-free.dev']

    for indicator in ngrok_indicators:
        if indicator in origin or indicator in referer or indicator in host:
            return True
    return False


def validate_password(password):
    if len(password) <= 6:
        return False, "Password must be more than 6 characters"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least 1 uppercase letter"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least 1 special character"
    return True, None


def _firebase_uid_from_token():
    auth_header = request.headers.get('Authorization', '')
    token = None
    if auth_header.lower().startswith('bearer '):
        token = auth_header.split(None, 1)[1].strip()
    elif auth_header:
        token = auth_header.strip()
    if not token:
        return None
    try:
        claims = verify_id_token(token)
        return claims.get('uid')
    except Exception:
        return None


@auth_bp.route('/login', methods=['POST'])
def login():
    """Login: accept either a Firebase ID token OR email+password.

    - If ``id_token`` (or ``Authorization: Bearer <token>``) is provided, it is
      verified directly.
    - Otherwise we exchange ``username``/``email`` + ``password`` for a Firebase
      ID token via the Identity Toolkit REST API, then verify it.

    Returns the verified ID token so the client can reuse it for later calls.
    """
    data = request.get_json(silent=True) or {}
    id_token = data.get('id_token') or _extract_bearer()
    refresh_token = None

    if not id_token:
        # Password login: exchange email/password for a Firebase ID token.
        login_id = (data.get('username') or data.get('email') or '').strip()
        password = (data.get('password') or '').strip()
        if not login_id or not password:
            return jsonify({'error': 'Username/email and password are required'}), 400

        # Firebase Auth only knows email addresses. If the caller passed a
        # plain username (e.g. from the login form's "username" field), look
        # up the matching Firestore profile to resolve its real email first.
        if '@' in login_id:
            email = login_id
        else:
            existing_user = User.find_by(username=login_id)
            if not existing_user or not existing_user.email:
                log_audit(None, 'login_failed', 'user', login_id, 'Failed login attempt (unknown username)')
                return jsonify({'error': 'Invalid username or password'}), 401
            email = existing_user.email

        try:
            tokens = exchange_password_for_token_full(email, password)
        except RuntimeError as e:
            log_audit(None, 'login_failed', 'user', login_id, 'Failed login attempt (bad request)')
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            log_audit(None, 'login_failed', 'user', login_id, 'Failed login attempt (invalid credentials)')
            return jsonify({'error': f'Authentication failed: {e}'}), 401
        id_token = tokens['idToken']
        # The ID token expires in 1 hour with no way to renew it after the
        # fact — hand the refresh token back so the client can silently mint
        # a new one via /auth/refresh instead of getting hard-logged-out.
        refresh_token = tokens.get('refreshToken')

    try:
        claims = verify_id_token(id_token)
    except Exception as e:
        return jsonify({'error': f'Invalid Firebase token: {e}'}), 401

    uid = claims.get('uid')
    if not uid:
        return jsonify({'error': 'Invalid token claims'}), 401

    user = User.get(uid)
    if not user:
        # Auto-provision a profile from the Firebase Auth record.
        fb_user = get_auth().get_user(uid)
        user = User(
            id=uid,
            username=fb_user.display_name or fb_user.email,
            email=fb_user.email,
            role='customer',
            contact_number=(fb_user.phone_number or '').lstrip('+'),
            is_active=True,
            is_verified=bool(fb_user.email_verified),
        )
        user.save()

    if not user.is_active:
        return jsonify({'error': 'Account is inactive. Please verify your email first.'}), 403

    if is_ngrok_access() and user.role != 'customer':
        return jsonify({
            'error': 'Admin, staff, and mechanic roles are not available for public access. Please use the internal network.'
        }), 403

    # Mint a fresh session id for this login and make it the ONLY valid one
    # for this account — any other browser/device already logged in as this
    # user gets rejected on its next request (see firebase_auth_utils.py),
    # enforcing a single concurrent login per account.
    session_id = uuid.uuid4().hex
    user.active_session_id = session_id
    user.save()

    log_audit({'id': user.id, 'username': user.username, 'role': user.role}, 'login', 'user', user.id, 'Login successful')

    response = {
        'message': 'Login successful',
        'access_token': id_token,
        'session_id': session_id,
        'user': user.to_dict()
    }
    if refresh_token:
        response['refresh_token'] = refresh_token
    return jsonify(response), 200


def _extract_bearer():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.lower().startswith('bearer '):
        return auth_header.split(None, 1)[1].strip()
    return None


@auth_bp.route('/refresh', methods=['POST'])
def refresh():
    """Exchange a Firebase refresh token (from /login) for a new ID token.

    The client should call this proactively before its ID token expires
    (it's always good for exactly 1 hour), and/or retry once with a fresh
    token whenever a request comes back 401. No Authorization header is
    required here — the refresh token itself is the credential.
    """
    data = request.get_json(silent=True) or {}
    refresh_token = (data.get('refresh_token') or '').strip()
    if not refresh_token:
        return jsonify({'error': 'refresh_token is required'}), 400

    try:
        tokens = refresh_id_token(refresh_token)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 401

    return jsonify({
        'access_token': tokens['idToken'],
        'refresh_token': tokens['refreshToken'],
    }), 200


@auth_bp.route('/register', methods=['POST'])
def register():
    """Register a new user. Creates a Firebase Auth account + Firestore profile (inactive until verified)."""
    data = request.get_json(silent=True) or {}

    username = (data.get('username') or '').strip()
    first_name = (data.get('first_name') or '').strip()
    middle_initial = (data.get('middle_initial') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    email = (data.get('email') or '').strip()
    address = (data.get('address') or '').strip()
    password = (data.get('password') or '').strip()
    confirm_password = (data.get('confirm_password') or '').strip()
    contact_number = (data.get('contact_number') or '').strip()

    if not username:
        return jsonify({'error': 'Username is required'}), 400
    if not first_name:
        return jsonify({'error': 'First name is required'}), 400
    if not last_name:
        return jsonify({'error': 'Last name is required'}), 400
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    if not address:
        return jsonify({'error': 'Address is required'}), 400
    if not contact_number:
        return jsonify({'error': 'Contact number is required for SMS notifications'}), 400

    name_pattern = r"^[A-Za-zÀ-ɏ' \-]+$"
    if len(first_name) > 20 or not re.match(name_pattern, first_name):
        return jsonify({'error': 'First name must be 20 characters or fewer and contain only letters'}), 400
    if len(last_name) > 20 or not re.match(name_pattern, last_name):
        return jsonify({'error': 'Last name must be 20 characters or fewer and contain only letters'}), 400
    if middle_initial and not re.match(r"^[A-Za-zÀ-ɏ]\.?$", middle_initial):
        return jsonify({'error': 'Middle initial must be a single letter (e.g. "C" or "C.")'}), 400

    if len(username) > 30 or not re.match(r'^[A-Za-z0-9_.]+$', username):
        return jsonify({'error': 'Username must be 30 characters or fewer and contain only letters, numbers, underscore, or period'}), 400

    if len(address) > 200:
        return jsonify({'error': 'Address must be 200 characters or fewer'}), 400

    if len(contact_number) > 13 or not re.match(r'^(09|\+63)\d{9}$', contact_number):
        return jsonify({'error': 'Invalid phone number format. Use 09XXXXXXXXX or +63XXXXXXXXX'}), 400

    if password != confirm_password:
        return jsonify({'error': 'Password and confirm password do not match'}), 400

    is_valid, error_msg = validate_password(password)
    if not is_valid:
        return jsonify({'error': error_msg}), 400

    if User.find_by(email=email):
        return jsonify({'error': 'Email already exists'}), 400

    # Create Firebase Auth user (UID becomes the Firestore doc id).
    try:
        fb_user = create_firebase_user(email=email, password=password, display_name=username)
        uid = fb_user.uid
    except Exception as e:
        return jsonify({'error': f'Could not create Firebase account: {e}'}), 400

    otp_code = generate_otp()
    otp_expires = datetime.utcnow() + timedelta(minutes=10)

    user = User(
        id=uid,
        username=username,
        first_name=first_name,
        middle_initial=middle_initial,
        last_name=last_name,
        email=email,
        address=address,
        contact_number=contact_number,
        role='customer',
        verification_code=otp_code,
        verification_expires=otp_expires,
        is_active=False,
        is_verified=False
    )
    user.save()

    log_audit({'id': uid, 'username': username, 'role': 'customer'}, 'create', 'user', uid, f"Customer self-registration '{username}' (pending email verification)")

    email_result = send_otp_email(email, otp_code, username)

    return jsonify({
        'message': 'Registration initiated. OTP sent to your email.',
        'user_id': uid,
        'verification_required': True,
        'email_sent': email_result.get('status') == 'success'
    }), 201


@auth_bp.route('/verify-email', methods=['POST'])
def verify_email():
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    code = data.get('verification_code')

    if not email or not code:
        return jsonify({'error': 'Email and verification code are required'}), 400

    user = User.find_by(email=email, verification_code=code)
    if not user:
        return jsonify({'error': 'Invalid OTP code'}), 400
    if user.is_verified:
        return jsonify({'error': 'Email already verified'}), 400
    if _is_expired(user.verification_expires):
        return jsonify({'error': 'OTP has expired. Please request a new one.'}), 400

    user.is_verified = True
    user.is_active = True
    user.verification_code = None
    user.verification_expires = None
    user.save()

    send_welcome_email(email, user.username)

    return jsonify({
        'message': 'Email verified successfully! You can now login.',
        'user': user.to_dict()
    }), 200


@auth_bp.route('/register-complete', methods=['POST'])
def register_complete():
    data = request.get_json(silent=True) or {}
    email = data.get('email')
    code = data.get('verification_code')

    if not email or not code:
        return jsonify({'error': 'Email and verification code are required'}), 400

    user = User.find_by(email=email, verification_code=code)
    if not user:
        return jsonify({'error': 'Invalid verification code'}), 400
    if user.is_verified and user.is_active:
        return jsonify({'error': 'Account already active'}), 400
    if _is_expired(user.verification_expires):
        return jsonify({'error': 'OTP has expired. Please request a new one.'}), 400

    user.is_verified = True
    user.is_active = True
    user.verification_code = None
    user.verification_expires = None
    user.save()

    send_welcome_email(email, user.username)

    return jsonify({
        'message': 'Registration completed successfully!',
        'user': user.to_dict()
    }), 200


@auth_bp.route('/me', methods=['GET'])
def get_current_user_route():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found / not authenticated'}), 401
    return jsonify(user.to_dict()), 200


@auth_bp.route('/change-password', methods=['POST'])
def change_password():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'User not found'}), 401

    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({'error': 'Current and new password are required'}), 400

    # Admin SDK can't verify a raw password directly, but the same
    # email+password -> ID token exchange /login uses can: it only succeeds
    # if current_password is actually correct. Without this check, anyone
    # holding a valid session token (stolen, XSS'd, a shared/unlocked device)
    # could change the password with no proof they know the current one.
    try:
        exchange_password_for_token(user.email, current_password)
    except Exception:
        return jsonify({'error': 'Current password is incorrect'}), 401

    is_valid, error_msg = validate_password(new_password)
    if not is_valid:
        return jsonify({'error': error_msg}), 400

    try:
        set_user_password(user.id, new_password)
    except Exception as e:
        return jsonify({'error': f'Could not update password: {e}'}), 400

    # Best-effort: sign the user out of other sessions. Note this revokes
    # refresh tokens only -- an ID token already issued elsewhere stays valid
    # until its own ~1h expiry, since get_current_user() doesn't check
    # revocation on every request (that has a real per-request perf cost).
    try:
        get_auth().revoke_refresh_tokens(user.id)
    except Exception:
        pass

    log_audit({'id': user.id, 'username': user.username, 'role': user.role}, 'update', 'user', user.id, 'Changed own password')

    return jsonify({'message': 'Password changed successfully'}), 200


def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """Request a password-reset email.

    Always returns the same generic response whether or not the email
    belongs to an account, so this endpoint can't be used to enumerate
    registered emails.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip()

    generic_response = jsonify({
        'message': 'If an account exists for that email, a password reset link has been sent.'
    }), 200

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    user = User.find_by(email=email)
    if not user:
        return generic_response

    # If a still-valid reset is already pending, don't mint/send another --
    # a cheap guard against someone repeatedly hammering this endpoint to
    # spam a victim's inbox, without needing a real rate-limiter dependency.
    if user.password_reset_token and not _is_expired(user.password_reset_expires):
        return generic_response

    token = generate_reset_token(user.id)
    user.password_reset_token = _hash_token(token)
    user.password_reset_expires = datetime.utcnow() + timedelta(minutes=30)
    user.save()

    base_url = current_app.config.get('BASE_URL', '').rstrip('/')
    reset_link = f"{base_url}/reset-password?token={token}"
    send_password_reset_email(email, reset_link, user.username)

    return generic_response


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """Complete a password reset started via /forgot-password."""
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    new_password = data.get('new_password')

    generic_error = jsonify({
        'error': 'Invalid or expired reset link. Please request a new one.'
    }), 400

    if not token or not new_password:
        return jsonify({'error': 'Token and new password are required'}), 400

    uid = verify_reset_token(token)
    if not uid:
        return generic_error

    user = User.get(uid)
    if not user or not user.password_reset_token:
        return generic_error

    if _is_expired(user.password_reset_expires):
        return generic_error

    if user.password_reset_token != _hash_token(token):
        return generic_error

    is_valid, error_msg = validate_password(new_password)
    if not is_valid:
        return jsonify({'error': error_msg}), 400

    try:
        set_user_password(user.id, new_password)
    except Exception as e:
        return jsonify({'error': f'Could not update password: {e}'}), 400

    user.password_reset_token = None
    user.password_reset_expires = None
    user.save()

    # Email ownership was just proven by following the link, so unlike the
    # change-password flow this is unambiguously warranted, not just best-effort.
    try:
        get_auth().revoke_refresh_tokens(user.id)
    except Exception:
        pass

    log_audit({'id': user.id, 'username': user.username, 'role': user.role}, 'update', 'user', user.id, 'Reset password via emailed link')

    return jsonify({'message': 'Password reset successful. You can now log in with your new password.'}), 200


@auth_bp.route('/resend-code', methods=['POST'])
def resend_verification_code():
    data = request.get_json(silent=True) or {}
    email = data.get('email')

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    user = User.find_by(email=email)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.is_verified:
        return jsonify({'error': 'Email already verified'}), 400

    user.verification_code = generate_otp()
    user.verification_expires = datetime.utcnow() + timedelta(minutes=10)
    user.save()

    email_result = send_otp_email(email, user.verification_code, user.username)

    return jsonify({
        'message': 'New OTP sent to your email',
        'email_sent': email_result.get('status') == 'success'
    }), 200


@auth_bp.route('/reset-demo', methods=['POST'])
def reset_demo_accounts():
    """Reset demo accounts to default credentials in Firebase Auth + Firestore.

    Admin-only, and additionally gated behind ALLOW_DEMO_ACCOUNTS (the same
    flag app.py's init_sample_data() already uses for this exact concern --
    one flag, not two, for "these well-known hardcoded credentials are
    allowed to exist/reset on this deployment"). This endpoint had no
    authentication at all, meaning anyone who found the URL could reset
    admin/staff/mechanic/customer accounts to well-known hardcoded passwords
    (and the response used to echo those passwords back) -- a full
    unauthenticated account-takeover path on the live site.
    """
    if os.environ.get('ALLOW_DEMO_ACCOUNTS') != '1':
        return jsonify({'error': 'Demo reset is disabled'}), 403

    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        demo = [
            ('admin', 'admin@automan.com', 'Admin123!', 'admin', '09123456789'),
            ('staff', 'staff@automan.com', 'Staff123!', 'staff', '09123456788'),
            ('mechanic', 'mechanic@automan.com', 'Mechanic123!', 'mechanic', '09123456787'),
            ('customer', 'customer@automan.com', 'Customer123!', 'customer', '09123456786'),
        ]
        for username, email, pw, role, phone in demo:
            existing = User.find_by(email=email)
            if existing:
                set_user_password(existing.id, pw)
                existing.role = role
                existing.is_active = True
                existing.is_verified = True
                existing.contact_number = phone
                existing.full_name = existing.full_name or {'admin': 'System Administrator', 'staff': 'Service Staff', 'mechanic': 'Service Mechanic', 'customer': 'Demo Customer'}.get(role)
                existing.save()
            else:
                fb_user = create_firebase_user(email=email, password=pw, display_name=username)
                User(
                    id=fb_user.uid, username=username, email=email, role=role,
                    full_name={'admin': 'System Administrator', 'staff': 'Service Staff', 'mechanic': 'Service Mechanic', 'customer': 'Demo Customer'}.get(role),
                    contact_number=phone, is_active=True, is_verified=True
                ).save()

        log_audit({'id': user.id, 'username': user.username, 'role': user.role}, 'update', 'user',
                   'demo_accounts', 'Reset all demo accounts to default credentials')

        return jsonify({
            'message': 'Demo accounts reset successfully',
            # No passwords in the response -- the caller already had to be an
            # authenticated admin with ALLOW_DEMO_RESET enabled to get here,
            # and the fixed demo passwords belong in internal docs, not an
            # API response body.
            'demo_accounts': [
                {'username': u, 'role': r} for u, e, p, r, ph in demo
            ]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
