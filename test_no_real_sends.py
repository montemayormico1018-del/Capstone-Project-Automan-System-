"""Round 3 R8: register / password-reset / quotation flows must go through
the mocked send functions (conftest.py's autouse `no_real_email_or_sms`
fixture), never a real SMTP/HTTP call.

FIRESTORE_MOCK=1 only.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)

# Captured at collection time, before conftest.py's autouse fixture patches
# utils.email_utils.send_otp_email/utils.sms_utils.send_sms for each test --
# these names keep pointing at the *real*, undecorated function objects
# regardless of later monkeypatching (monkeypatch.setattr rebinds the
# attribute on the module, not this already-bound reference). Used below to
# verify the second safety net (each function's own _test_mode_active()
# guard) independently of the fixture that's the first one.
from utils.email_utils import send_otp_email as real_send_otp_email
from utils.sms_utils import send_sms as real_send_sms


@pytest.fixture
def api():
    import firebase_auth_utils
    from models.user import User

    uids = {}
    for role in ('admin', 'staff'):
        uid = f'sendtest-{role}-{uuid.uuid4().hex[:8]}'
        User(
            id=uid, username=f'{role}_{uid}', full_name=f'{role.title()} Tester',
            email=f'{uid}@example.test', role=role, contact_number='09123456789',
            is_active=True, is_verified=True,
        ).save()
        uids[role] = uid

    current = {'uid': uids['admin']}
    firebase_auth_utils.verify_id_token = lambda token: {'uid': current['uid']}

    from app import create_app
    app = create_app()
    client = app.test_client()
    H = {'Authorization': 'Bearer fake'}

    def as_role(role):
        current['uid'] = uids[role]

    yield {'client': client, 'H': H, 'as_role': as_role, 'uids': uids}


@pytest.fixture
def app_context():
    from app import create_app
    app = create_app()
    with app.app_context():
        yield


def test_register_calls_mocked_send_otp_email_not_a_real_smtp_call(api, no_real_email_or_sms):
    client = api['client']
    email = f'{uuid.uuid4().hex[:8]}@example.test'

    r = client.post('/api/auth/register', json={
        'username': f'newuser{uuid.uuid4().hex[:6]}',
        'first_name': 'New',
        'last_name': 'Customer',
        'email': email,
        'address': '123 Test St',
        'password': 'Test123!',
        'confirm_password': 'Test123!',
        'contact_number': '09123456789',
    })
    assert r.status_code == 201, r.get_json()

    otp_calls = [c for c in no_real_email_or_sms['email'] if c['kind'] == 'otp']
    assert len(otp_calls) == 1
    assert otp_calls[0]['to'] == email


def test_forgot_password_calls_mocked_send_password_reset_email(api, no_real_email_or_sms):
    client = api['client']
    from models.user import User

    email = f'{uuid.uuid4().hex[:8]}@example.test'
    User(
        id=f'fpwtest-{uuid.uuid4().hex[:8]}', username='fpwtest', full_name='Forgot Pw Tester',
        email=email, role='customer', contact_number='09123456789',
        is_active=True, is_verified=True,
    ).save()

    r = client.post('/api/auth/forgot-password', json={'email': email})
    assert r.status_code == 200

    reset_calls = [c for c in no_real_email_or_sms['email'] if c['kind'] == 'password_reset']
    assert len(reset_calls) == 1
    assert reset_calls[0]['to'] == email
    assert '/reset-password?token=' in reset_calls[0]['reset_link']


def test_quotation_sent_status_change_calls_mocked_send_sms(api, no_real_email_or_sms):
    client, H, as_role = api['client'], api['H'], api['as_role']
    from models.customer import Customer
    from models.appointment import Appointment

    customer = Customer(name='Quote Customer', phone='09123456789', email='quote@example.test')
    customer.save()
    appointment = Appointment(
        status='Pending', customer_id=customer.id,
        preferred_date='2026-12-10', preferred_time='10:00:00',
        service_estimate=1500.0,
    )
    appointment.save()

    as_role('staff')
    r = client.put(f'/api/appointments/{appointment.id}', headers=H, json={'status': 'Quotation Sent'})
    assert r.status_code == 200, r.get_json()

    assert len(no_real_email_or_sms['sms']) == 1
    assert no_real_email_or_sms['sms'][0]['to'] == '09123456789'


def test_real_send_otp_email_refuses_to_send_under_test_mode(app_context):
    """Second safety net, tested independently of the autouse fixture: the
    *real*, undecorated send_otp_email (captured at collection time, above)
    must itself refuse to touch smtplib under FIRESTORE_MOCK=1/TESTING=1."""
    result = real_send_otp_email('should-not-be-emailed@example.test', '123456', 'nobody')
    assert result['status'] == 'success'
    assert 'test mode' in result['message']


def test_real_send_sms_refuses_to_send_under_test_mode(app_context):
    result = real_send_sms('09123456789', 'should not actually be sent')
    assert result['success'] is True
    assert result['message_id'] == 'test-mode-stub'


def test_no_test_file_imports_smtplib_or_requests_directly():
    """grep-verify: no test in this suite talks to smtplib or the real
    iProg HTTP API itself -- everything must go through the mocked
    send_otp_email/send_password_reset_email/send_welcome_email/send_sms."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    self_name = os.path.basename(__file__)
    test_files = [
        f for f in os.listdir(repo_root)
        if f.startswith('test_') and f.endswith('.py') and f != self_name
    ]
    assert test_files, 'no test_*.py files found -- sanity check on the glob itself'

    offenders = []
    for fname in test_files:
        with open(os.path.join(repo_root, fname), encoding='utf-8') as f:
            src = f.read()
        if 'import smtplib' in src or 'smtplib.SMTP' in src:
            offenders.append((fname, 'smtplib'))
        if 'requests.post' in src or 'import requests' in src:
            offenders.append((fname, 'requests'))

    assert not offenders, f'test file(s) touch a real network client directly: {offenders}'
