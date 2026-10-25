"""Round 4 R12: send_quotation_sms() consolidation.

utils/sms_utils.py's send_quotation_sms() used to be dead code with a body
that diverged from the real, working message-building logic inline in
routes/appointments.py's update_appointment(). That inline block has been
replaced with a single call to send_quotation_sms() (now carrying the
authoritative logic, ported verbatim). This verifies: (1) the 'Quotation
Sent' transition calls send_quotation_sms() exactly once with the appointment
and notes, and the message it builds reaches send_sms with the expected
phone number; (2) no other status transition calls send_quotation_sms() at
all -- it's SMS, but not through this function.

FIRESTORE_MOCK=1 only -- uses conftest.py's autouse email/SMS mock fixture.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)


@pytest.fixture
def api():
    import firebase_auth_utils
    from models.user import User

    uid = f'r12test-staff-{uuid.uuid4().hex[:8]}'
    User(
        id=uid, username=f'staff_{uid}', full_name='Staff Tester',
        email=f'{uid}@example.test', role='staff', contact_number='09123456789',
        is_active=True, is_verified=True,
    ).save()

    import firebase_auth_utils as fau
    fau.verify_id_token = lambda token: {'uid': uid}

    from app import create_app
    app = create_app()
    client = app.test_client()
    H = {'Authorization': 'Bearer fake'}

    yield {'client': client, 'H': H}


@pytest.fixture
def appointment_with_phone():
    from models.customer import Customer
    from models.appointment import Appointment

    customer = Customer(name='R12 Customer', phone='09171234567', email='r12@example.test')
    customer.save()
    appointment = Appointment(
        status='Pending', customer_id=customer.id,
        preferred_date='2026-12-15', preferred_time='09:00:00',
        service_estimate=2500.0,
    )
    appointment.save()
    return appointment


def test_quotation_sent_calls_send_quotation_sms_exactly_once(api, appointment_with_phone, monkeypatch):
    calls = []

    def fake_send_quotation_sms(appointment, notes=''):
        calls.append({'appointment_id': appointment.id, 'notes': notes})
        return {'success': True, 'status': 'sent', 'to': '09171234567', 'body': 'stub'}

    import utils.sms_utils as sms_utils
    monkeypatch.setattr(sms_utils, 'send_quotation_sms', fake_send_quotation_sms)

    client, H = api['client'], api['H']
    r = client.put(f'/api/appointments/{appointment_with_phone.id}', headers=H,
                    json={'status': 'Quotation Sent', 'notes': 'Front brake pads worn'})
    assert r.status_code == 200, r.get_json()

    assert len(calls) == 1
    assert calls[0]['appointment_id'] == appointment_with_phone.id
    assert calls[0]['notes'] == 'Front brake pads worn'


def test_quotation_sent_reaches_send_sms_with_expected_number_and_message(api, appointment_with_phone, no_real_email_or_sms):
    """End-to-end through the real (consolidated) send_quotation_sms(),
    down to the conftest-mocked send_sms -- confirms the actual message
    content/number, not just that some function was called."""
    client, H = api['client'], api['H']
    r = client.put(f'/api/appointments/{appointment_with_phone.id}', headers=H,
                    json={'status': 'Quotation Sent'})
    assert r.status_code == 200, r.get_json()

    assert len(no_real_email_or_sms['sms']) == 1
    sent = no_real_email_or_sms['sms'][0]
    assert sent['to'] == '09171234567'
    assert 'quotation' in sent['message'].lower()
    assert 'PHP 2,500.00' in sent['message']


@pytest.mark.parametrize('status', ['Approved', 'Rejected', 'Completed', 'Cancelled'])
def test_other_status_transitions_do_not_call_send_quotation_sms(api, appointment_with_phone, monkeypatch, status):
    calls = []

    def fake_send_quotation_sms(appointment, notes=''):
        calls.append(appointment.id)
        return {'success': True, 'status': 'sent'}

    import utils.sms_utils as sms_utils
    monkeypatch.setattr(sms_utils, 'send_quotation_sms', fake_send_quotation_sms)

    client, H = api['client'], api['H']
    r = client.put(f'/api/appointments/{appointment_with_phone.id}', headers=H, json={'status': status})
    assert r.status_code == 200, r.get_json()

    assert calls == [], f'{status} transition must not call send_quotation_sms()'
