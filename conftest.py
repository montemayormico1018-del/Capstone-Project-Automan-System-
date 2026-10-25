"""Shared, autouse test safety nets.

IMPORTANT: the two os.environ.setdefault() calls immediately below must stay
the very first thing this file does, before any other import. pytest always
imports the root conftest.py before collecting/importing any test_*.py file,
so this is the earliest point at which these can be set for the whole
session -- firebase_config.py reads FIRESTORE_MOCK into a *module-level*
constant (`USE_MOCK = os.environ.get("FIRESTORE_MOCK") == "1"`) the instant
it's first imported, not lazily, so if that import happens before this line
ever runs, the real-vs-mock decision is made and baked in for the rest of
the process -- setting the env var later would be a no-op. A real
firebase-credentials.json sits in this repo, so a bare `pytest` run with
neither var set would otherwise silently authenticate against the real
Firebase project (Round 4 R11 -- this used to be a manual
`$env:FIRESTORE_MOCK=1` habit before every run, not something a bare pytest
or CI invocation enforced on its own).

- FIRESTORE_MOCK=1: routes every Model.query/.save() at Firestore/Firebase
  Auth through the offline in-memory mock (mock_firestore.py) instead of the
  real project.
- TESTING=1: what utils/email_utils.py's and utils/sms_utils.py's
  _test_mode_active() checks key off (Round 3 R8) so those modules refuse to
  send a real email/SMS for the whole session even if a test forgets to
  request the autouse fixture below, or a call happens at import time. Also
  what firestore_db.py's own guard (see that file) checks against
  FIRESTORE_MOCK, to fail loudly instead of silently proceeding if the two
  ever disagree.
"""
import os

os.environ.setdefault('FIRESTORE_MOCK', '1')
os.environ.setdefault('TESTING', '1')

import pytest

# Round 3 R8, continued: no test in this suite may send a real email or SMS,
# regardless of what .env holds -- .env carries live EMAIL_ADDRESS/
# EMAIL_PASSWORD credentials (unlike IPROG_API_HASH, which stays unset
# here), and an earlier test genuinely sent one outbound email through them
# before either guard here existed. Two independent layers: (1) TESTING=1
# above, which the util modules' own _test_mode_active() checks key off, and
# (2) the autouse fixture below, which monkeypatches every send_* function
# to a recording stub so tests can assert on exactly what would have been
# sent (recipient, body, link/OTP) without it ever reaching smtplib/requests.


@pytest.fixture(autouse=True)
def no_real_email_or_sms(monkeypatch):
    calls = {'email': [], 'sms': [], 'push': []}

    def fake_send_otp_email(recipient_email, otp_code, username=None):
        calls['email'].append({'kind': 'otp', 'to': recipient_email, 'otp_code': otp_code, 'username': username})
        return {'status': 'success', 'message': 'OTP sent successfully (test stub -- no real email sent)'}

    def fake_send_password_reset_email(recipient_email, reset_link, username=None):
        calls['email'].append({'kind': 'password_reset', 'to': recipient_email, 'reset_link': reset_link, 'username': username})
        return {'status': 'success', 'message': 'Password reset email sent (test stub -- no real email sent)'}

    def fake_send_welcome_email(recipient_email, username):
        calls['email'].append({'kind': 'welcome', 'to': recipient_email, 'username': username})
        return {'status': 'success', 'message': 'Welcome email sent (test stub -- no real email sent)'}

    def fake_send_sms(number, message):
        calls['sms'].append({'to': number, 'message': message})
        return {'success': True, 'status': 'sent', 'message_id': 'test-stub', 'to': number, 'body': message}

    def fake_send_push(tokens, title, body, data=None):
        if isinstance(tokens, str):
            tokens = [tokens]
        tokens = [t for t in (tokens or []) if t]
        calls['push'].append({'tokens': tokens, 'title': title, 'body': body, 'data': data or {}})
        if not tokens:
            return {'success': False, 'error': 'No device tokens'}
        return {'success': True, 'status': 'sent', 'success_count': len(tokens), 'failure_count': 0, 'invalid_tokens': []}

    import utils.email_utils as email_utils
    import utils.sms_utils as sms_utils
    import utils.push_utils as push_utils

    monkeypatch.setattr(email_utils, 'send_otp_email', fake_send_otp_email)
    monkeypatch.setattr(email_utils, 'send_password_reset_email', fake_send_password_reset_email)
    monkeypatch.setattr(email_utils, 'send_welcome_email', fake_send_welcome_email)
    monkeypatch.setattr(sms_utils, 'send_sms', fake_send_sms)
    monkeypatch.setattr(push_utils, 'send_push', fake_send_push)

    # routes/auth.py imports these three by name at module load time
    # (`from utils.email_utils import (...)`), so it holds its own
    # references that patching utils.email_utils above doesn't reach --
    # every other call site in the codebase (this includes every send_push
    # call site -- see models/enhanced_models.py's notify_low_stock() and
    # routes/job_orders.py's 'In Progress' handler) does
    # `from utils.sms_utils/email_utils/push_utils import ...` *inside* the
    # function body, which re-resolves against the (now-patched) module
    # attribute on every call, so only routes.auth needs this second patch.
    import routes.auth as auth_routes
    monkeypatch.setattr(auth_routes, 'send_otp_email', fake_send_otp_email)
    monkeypatch.setattr(auth_routes, 'send_password_reset_email', fake_send_password_reset_email)
    monkeypatch.setattr(auth_routes, 'send_welcome_email', fake_send_welcome_email)

    yield calls
