"""Panel item #9 (audit trail) follow-up: routes/appointments.py's
update_appointment() handles every status transition (Quotation Sent,
Approved, Rejected, Completed, Cancelled), price edits and reschedules --
but previously called log_audit() zero times, leaving the audit log blind
to the core appointment workflow (it only ever saw vehicle create/update/
delete). This verifies the new log_audit('appointment', ...) calls actually
fire, with the right action/entity_type/description, for each kind of edit.

FIRESTORE_MOCK=1 only.
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

    uid = f'audittest-staff-{uuid.uuid4().hex[:8]}'
    User(
        id=uid, username=f'staff_{uid}', full_name='Staff Tester',
        email=f'{uid}@example.test', role='staff', contact_number='09123456789',
        is_active=True, is_verified=True,
    ).save()
    firebase_auth_utils.verify_id_token = lambda token: {'uid': uid}

    from app import create_app
    app = create_app()
    client = app.test_client()
    return {'client': client, 'H': {'Authorization': 'Bearer fake'}, 'uid': uid}


@pytest.fixture
def appointment():
    from models.customer import Customer
    from models.appointment import Appointment

    customer = Customer(name='Audit Customer', phone='09171234567', email='audit@example.test')
    customer.save()
    appt = Appointment(
        status='Pending', customer_id=customer.id,
        preferred_date='2026-12-15', preferred_time='09:00:00',
        service_estimate=2500.0,
    )
    appt.save()
    return appt


def _entries_for(appointment_id):
    from models.audit_log import AuditLog
    return [e for e in AuditLog.query.all()
            if e.entity_type == 'appointment' and e.entity_id == str(appointment_id)]


def test_status_change_writes_one_audit_entry(api, appointment, no_real_email_or_sms):
    r = api['client'].put(f'/api/appointments/{appointment.id}', headers=api['H'],
                           json={'status': 'Approved'})
    assert r.status_code == 200, r.get_json()

    entries = _entries_for(appointment.id)
    assert len(entries) == 1
    assert entries[0].action == 'update'
    assert entries[0].username == f"staff_{api['uid']}"
    assert 'Pending -> Approved' in entries[0].description


def test_no_op_update_writes_nothing(api, appointment):
    """Sending the same status back (or an empty body) shouldn't create a
    noise entry -- only an actual change is audit-worthy."""
    r = api['client'].put(f'/api/appointments/{appointment.id}', headers=api['H'], json={})
    assert r.status_code == 200, r.get_json()
    assert _entries_for(appointment.id) == []


def test_price_edit_writes_an_audit_entry(api, appointment):
    r = api['client'].put(f'/api/appointments/{appointment.id}', headers=api['H'],
                           json={'min_estimate': 2000, 'max_estimate': 3000})
    assert r.status_code == 200, r.get_json()

    entries = _entries_for(appointment.id)
    assert len(entries) == 1
    assert 'PHP 2,000-3,000' in entries[0].description


def test_reschedule_writes_an_audit_entry(api, appointment):
    r = api['client'].put(f'/api/appointments/{appointment.id}', headers=api['H'],
                           json={'preferred_date': '2026-12-21', 'preferred_time': '10:00'})
    assert r.status_code == 200, r.get_json()

    entries = _entries_for(appointment.id)
    assert len(entries) == 1
    assert 'Rescheduled date to 2026-12-21' in entries[0].description
    assert 'Rescheduled time to 10:00:00' in entries[0].description


def test_status_and_price_in_one_request_write_a_single_combined_entry(api, appointment):
    r = api['client'].put(f'/api/appointments/{appointment.id}', headers=api['H'],
                           json={'status': 'Approved', 'min_estimate': 2000, 'max_estimate': 3000})
    assert r.status_code == 200, r.get_json()

    entries = _entries_for(appointment.id)
    assert len(entries) == 1
    assert 'Pending -> Approved' in entries[0].description
    assert 'PHP 2,000-3,000' in entries[0].description


def test_admin_reset_demo_endpoint_is_audited(api, monkeypatch):
    """The security fix locking /reset-demo behind admin+flag should also
    leave a trail -- it's exactly the kind of sensitive action #9 exists for."""
    from models.user import User
    from models.audit_log import AuditLog

    admin_uid = f'audittest-admin-{uuid.uuid4().hex[:8]}'
    User(
        id=admin_uid, username=f'admin_{admin_uid}', full_name='Admin Tester',
        email=f'{admin_uid}@example.test', role='admin', contact_number='09123456789',
        is_active=True, is_verified=True,
    ).save()

    import firebase_auth_utils
    firebase_auth_utils.verify_id_token = lambda token: {'uid': admin_uid}
    monkeypatch.setenv('ALLOW_DEMO_ACCOUNTS', '1')

    import routes.auth as auth_module
    monkeypatch.setattr(auth_module, 'create_firebase_user', lambda **kw: type('F', (), {'uid': f'demo-{uuid.uuid4().hex[:8]}'})())
    monkeypatch.setattr(auth_module, 'set_user_password', lambda uid, pw: None)

    r = api['client'].post('/api/auth/reset-demo', headers=api['H'])
    assert r.status_code == 200, r.get_json()

    entries = [e for e in AuditLog.query.all() if e.entity_type == 'user' and e.entity_id == 'demo_accounts']
    assert len(entries) == 1
    assert entries[0].username == f'admin_{admin_uid}'
