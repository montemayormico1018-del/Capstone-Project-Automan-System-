from models import db, DocumentModel
from datetime import datetime
from flask_bcrypt import generate_password_hash, check_password_hash


class User(DocumentModel):
    """User model for authentication and role-based access."""
    __collection__ = 'users'

    def __init__(self, **kwargs):
        # Firebase UID is stored as the document id; keep email/password fields
        # for portability but auth is handled by Firebase Auth.
        self.username = kwargs.pop('username', None)
        # Structured name fields (Round 3 P8) -- first_name/last_name are the
        # source of truth going forward; full_name is still a real stored
        # field (not computed on read) because a lot of existing code reads
        # user.full_name directly as a plain attribute (SMS messages, print
        # forms, reports, audit log descriptions) and shouldn't all need
        # rewriting. Kept in sync via _sync_full_name() below, called from
        # both __init__ and save().
        self.first_name = kwargs.pop('first_name', None)
        self.middle_initial = kwargs.pop('middle_initial', None)
        self.last_name = kwargs.pop('last_name', None)
        self.full_name = kwargs.pop('full_name', None)
        self._sync_full_name()
        self.email = kwargs.pop('email', None)
        self.address = kwargs.pop('address', None)
        self.password_hash = kwargs.pop('password_hash', None)
        self.role = kwargs.pop('role', None)  # admin, staff, mechanic, customer
        self.contact_number = kwargs.pop('contact_number', None)
        # Mechanics have one deliberately simple assignment state.  Keeping it
        # on the user document makes the API/database the source of truth.
        self.availability = kwargs.pop('availability', 'not_available')
        # Set on every successful /login to a fresh random id and checked on
        # every authenticated request (see firebase_auth_utils.py) so a new
        # login anywhere invalidates every other browser's session for this
        # account — only one active login per account at a time.
        self.active_session_id = kwargs.pop('active_session_id', None)
        self.is_active = kwargs.pop('is_active', True)
        self.is_verified = kwargs.pop('is_verified', False)
        self.verification_code = kwargs.pop('verification_code', None)
        self.verification_expires = kwargs.pop('verification_expires', None)
        # Forgot-password flow: a hash of the signed reset token (never the
        # raw token) plus its expiry, kept separate from the registration-OTP
        # fields above so the two security-sensitive flows can't be confused
        # or reused against each other. Never exposed via to_dict().
        self.password_reset_token = kwargs.pop('password_reset_token', None)
        self.password_reset_expires = kwargs.pop('password_reset_expires', None)
        # FCM device tokens for push notifications (Capacitor mobile app) --
        # a list, not a single value, since the same account can be logged
        # into more than one device. Never exposed via to_dict(), same as
        # the password-reset fields above.
        self.fcm_tokens = kwargs.pop('fcm_tokens', None) or []
        self.created_at = kwargs.pop('created_at', datetime.utcnow())
        self.updated_at = kwargs.pop('updated_at', datetime.utcnow())
        super().__init__(**kwargs)

    def _sync_full_name(self):
        """Recompute full_name from first/middle/last when both first and
        last are present -- otherwise leaves full_name exactly as given.
        This covers legacy/system accounts (e.g. the demo 'System
        Administrator' display name) and any existing Firestore document
        loaded from before these fields existed, neither of which have
        first_name/last_name to compute from."""
        if self.first_name and self.last_name:
            parts = [self.first_name.strip()]
            if self.middle_initial and self.middle_initial.strip():
                mi = self.middle_initial.strip().rstrip('.')
                parts.append(f'{mi}.')
            parts.append(self.last_name.strip())
            self.full_name = ' '.join(parts)

    def set_password(self, password):
        """Hash and set password using Bcrypt."""
        self.password_hash = generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        from flask_bcrypt import check_password_hash
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def add_fcm_token(self, token):
        """Register a device token, deduped. Caller still needs to .save()."""
        if token and token not in self.fcm_tokens:
            self.fcm_tokens.append(token)

    def remove_fcm_token(self, token):
        """Drop a device token (logout, or FCM reporting it as no longer
        registered). Caller still needs to .save()."""
        if token in self.fcm_tokens:
            self.fcm_tokens.remove(token)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'full_name': self.full_name,
            'first_name': self.first_name,
            'middle_initial': self.middle_initial,
            'last_name': self.last_name,
            'email': self.email,
            'address': self.address,
            'role': self.role,
            'contact_number': self.contact_number,
            'availability': self.availability if self.role == 'mechanic' else None,
            'is_active': self.is_active,
            'is_verified': self.is_verified,
            'created_at': self.created_at.isoformat() if hasattr(self.created_at, 'isoformat') else self.created_at
        }

    @staticmethod
    def get_role_permissions():
        return {
            'admin': ['manage_users', 'manage_inventory', 'manage_appointments',
                      'manage_job_orders', 'view_sales', 'view_reports', 'view_forecast'],
            'staff': ['manage_appointments', 'manage_job_orders', 'manage_inventory',
                      'view_sales', 'view_reports'],
            'mechanic': ['manage_job_orders', 'update_inventory'],
            'customer': ['view_appointments', 'create_appointment', 'view_job_orders']
        }
