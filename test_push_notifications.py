"""Push notifications (FCM): device registration, low-stock alerts to
admin/staff, and "service started" alerts to the customer on a job order's
Pending -> In Progress transition.

FIRESTORE_MOCK=1 only -- uses conftest.py's autouse push/email/SMS mock
fixture, never touches real Firebase Cloud Messaging.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)

# Captured at collection time, before conftest.py's autouse fixture patches
# utils.push_utils.send_push for each test -- this name keeps pointing at
# the real, undecorated function regardless of later monkeypatching
# (monkeypatch.setattr rebinds the module attribute, not this reference).
# Same pattern as test_no_real_sends.py uses for the email/SMS equivalents.
from utils.push_utils import send_push as real_send_push


@pytest.fixture
def api():
    import firebase_auth_utils
    from models.user import User

    uids = {}
    for role in ('admin', 'staff', 'mechanic', 'customer'):
        uid = f'pushtest-{role}-{uuid.uuid4().hex[:8]}'
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

    def as_uid(uid):
        """Log in as an arbitrary uid, not just one of the pre-seeded
        role accounts above -- e.g. a Customer-linked account created
        inside a specific test. Mutates the SAME closure `as_role` uses,
        unlike creating a second verify_id_token override (which would
        silently shadow this one and make later as_role() calls no-ops)."""
        current['uid'] = uid

    yield {'client': client, 'H': H, 'as_role': as_role, 'as_uid': as_uid, 'uids': uids}


# ---------------------------------------------------------------------------
# Real send_push() itself: test-mode guard + FCM call shape.
# ---------------------------------------------------------------------------

def test_real_send_push_refuses_to_send_under_test_mode():
    from app import create_app
    app = create_app()
    with app.app_context():
        result = real_send_push(['fake-device-token'], 'Title', 'Body')
    assert result['success'] is True
    assert result.get('test_mode') is True


def test_send_push_with_no_tokens_is_a_clean_no_op():
    from utils.push_utils import send_push
    result = send_push([], 'Title', 'Body')
    assert result['success'] is False
    assert 'No device tokens' in result['error']

    result = send_push(None, 'Title', 'Body')
    assert result['success'] is False


def test_send_push_accepts_a_single_token_string_not_just_a_list():
    from utils.push_utils import send_push
    result = send_push('single-token', 'Title', 'Body')
    assert result['success'] is True


# ---------------------------------------------------------------------------
# Device registration endpoint.
# ---------------------------------------------------------------------------

def test_register_device_stores_token_on_the_logged_in_user(api):
    client, H, as_role, uids = api['client'], api['H'], api['as_role'], api['uids']
    as_role('customer')

    r = client.post('/api/notifications/register-device', headers=H, json={'token': 'device-abc-123'})
    assert r.status_code == 200, r.get_json()

    from models.user import User
    user = User.get(uids['customer'])
    assert 'device-abc-123' in user.fcm_tokens


def test_register_device_dedupes_the_same_token(api):
    client, H, as_role, uids = api['client'], api['H'], api['as_role'], api['uids']
    as_role('customer')

    client.post('/api/notifications/register-device', headers=H, json={'token': 'device-xyz'})
    client.post('/api/notifications/register-device', headers=H, json={'token': 'device-xyz'})

    from models.user import User
    user = User.get(uids['customer'])
    assert user.fcm_tokens.count('device-xyz') == 1


def test_register_device_requires_a_token(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('customer')
    r = client.post('/api/notifications/register-device', headers=H, json={})
    assert r.status_code == 400


def test_unregister_device_removes_the_token(api):
    client, H, as_role, uids = api['client'], api['H'], api['as_role'], api['uids']
    as_role('customer')

    client.post('/api/notifications/register-device', headers=H, json={'token': 'device-to-remove'})
    r = client.post('/api/notifications/unregister-device', headers=H, json={'token': 'device-to-remove'})
    assert r.status_code == 200

    from models.user import User
    user = User.get(uids['customer'])
    assert 'device-to-remove' not in user.fcm_tokens


# ---------------------------------------------------------------------------
# Low-stock alert -> pushes to admin/staff with a registered device.
# ---------------------------------------------------------------------------

def test_low_stock_pushes_to_admin_and_staff_with_tokens(api, no_real_email_or_sms):
    client, H, as_role, uids = api['client'], api['H'], api['as_role'], api['uids']

    as_role('admin')
    client.post('/api/notifications/register-device', headers=H, json={'token': 'admin-device'})
    as_role('staff')
    client.post('/api/notifications/register-device', headers=H, json={'token': 'staff-device'})
    as_role('mechanic')
    client.post('/api/notifications/register-device', headers=H, json={'token': 'mechanic-device'})

    from models.enhanced_models import InventoryItem, InventoryMovement

    item = InventoryItem(name='Brake Pad Set', sku=f'BP-{uuid.uuid4().hex[:6]}', price=1200, stock=5, reorder_level=3)
    item.save()

    # Crosses from above reorder_level (5) to at/below it (2) -- the exact
    # edge notify_low_stock() triggers on.
    InventoryMovement.record_usage(item.id, quantity=3, reference_type='job_order', reason='test')

    push_tokens_notified = {t for call in no_real_email_or_sms['push'] for t in call['tokens']}
    assert 'admin-device' in push_tokens_notified
    assert 'staff-device' in push_tokens_notified
    assert 'mechanic-device' not in push_tokens_notified, 'mechanics should not get stock alerts'

    low_stock_call = next(c for c in no_real_email_or_sms['push'] if c['data'].get('type') == 'low_stock')
    assert 'Brake Pad Set' in low_stock_call['body']


def test_low_stock_does_not_push_again_while_already_below_reorder_level(api, no_real_email_or_sms):
    """Matches the existing SMS behavior: only notify on the crossing, not
    on every subsequent deduction while already low."""
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('admin')
    client.post('/api/notifications/register-device', headers=H, json={'token': 'admin-device-2'})

    from models.enhanced_models import InventoryItem, InventoryMovement

    item = InventoryItem(name='Air Filter', sku=f'AF-{uuid.uuid4().hex[:6]}', price=400, stock=2, reorder_level=3)
    item.save()  # starts already at/below reorder_level

    InventoryMovement.record_usage(item.id, quantity=1, reference_type='job_order', reason='test')

    assert not any(c['data'].get('type') == 'low_stock' and c['data'].get('item_id') == item.id
                   for c in no_real_email_or_sms['push'])


# ---------------------------------------------------------------------------
# Job order "In Progress" -> pushes the customer.
# ---------------------------------------------------------------------------

def test_job_order_started_pushes_the_customer(api, no_real_email_or_sms):
    client, H, as_role, uids = api['client'], api['H'], api['as_role'], api['uids']

    from models.customer import Customer
    from models.user import User
    from models.appointment import Appointment
    from models.job_order import JobOrder

    email = f'{uuid.uuid4().hex[:8]}@example.test'
    customer = Customer(name='Push Customer', phone='09171234567', email=email)
    customer.save()

    # The account a real customer would be logged into the app with --
    # matched to the Customer record by email, same as the real code path.
    account_uid = f'pushtest-cust-{uuid.uuid4().hex[:8]}'
    User(
        id=account_uid, username=f'cust_{account_uid}', full_name='Push Customer',
        email=email, role='customer', contact_number='09171234567',
        is_active=True, is_verified=True,
    ).save()

    appointment = Appointment(
        status='Approved', customer_id=customer.id,
        preferred_date='2026-12-20', preferred_time='10:00:00',
    )
    appointment.save()

    job_order = JobOrder(status='Pending', appointment_id=appointment.id, user_id=uids['mechanic'])
    job_order.save()

    api['as_uid'](account_uid)
    client.post('/api/notifications/register-device', headers=H, json={'token': 'customer-device'})

    as_role('mechanic')
    r = client.put(f'/api/job-orders/{job_order.id}', headers=H, json={'status': 'In Progress'})
    assert r.status_code == 200, r.get_json()

    started_calls = [c for c in no_real_email_or_sms['push'] if c['data'].get('type') == 'job_order_started']
    assert len(started_calls) == 1
    assert 'customer-device' in started_calls[0]['tokens']
    assert started_calls[0]['data']['job_order_id'] == job_order.id


def test_job_order_completed_pushes_the_customer(api, no_real_email_or_sms):
    client, H, as_role = api['client'], api['H'], api['as_role']

    from models.customer import Customer
    from models.user import User
    from models.vehicle import Vehicle
    from models.job_order import JobOrder

    # The Completed branch resolves the customer via job_order.vehicle.customer
    # (not the appointment), so this test sets up that same path.
    email = f'{uuid.uuid4().hex[:8]}@example.test'
    customer = Customer(name='Completed Push Customer', phone='09171234567', email=email)
    customer.save()
    vehicle = Vehicle(customer_id=customer.id, make='Toyota', model='Vios')
    vehicle.save()

    account_uid = f'pushtest-cust-{uuid.uuid4().hex[:8]}'
    User(
        id=account_uid, username=f'cust_{account_uid}', full_name='Completed Push Customer',
        email=email, role='customer', contact_number='09171234567',
        is_active=True, is_verified=True,
    ).save()

    # No appointment services/packages -- the completion gate only blocks
    # when there ARE some still unchecked, so an empty set passes trivially.
    job_order = JobOrder(status='In Progress', vehicle_id=vehicle.id)
    job_order.save()

    api['as_uid'](account_uid)
    client.post('/api/notifications/register-device', headers=H, json={'token': 'customer-device-completed'})

    as_role('staff')
    r = client.put(f'/api/job-orders/{job_order.id}', headers=H, json={'status': 'Completed'})
    assert r.status_code == 200, r.get_json()

    completed_calls = [c for c in no_real_email_or_sms['push'] if c['data'].get('type') == 'job_order_completed']
    assert len(completed_calls) == 1
    assert 'customer-device-completed' in completed_calls[0]['tokens']
    assert completed_calls[0]['data']['job_order_id'] == job_order.id


def test_other_job_order_transitions_do_not_push_started(api, no_real_email_or_sms):
    client, H, as_role, uids = api['client'], api['H'], api['as_role'], api['uids']

    from models.customer import Customer
    from models.appointment import Appointment
    from models.job_order import JobOrder

    customer = Customer(name='No Push Customer', phone='09179999999', email=f'{uuid.uuid4().hex[:8]}@example.test')
    customer.save()
    appointment = Appointment(status='Approved', customer_id=customer.id,
                               preferred_date='2026-12-21', preferred_time='11:00:00')
    appointment.save()
    job_order = JobOrder(status='In Progress', appointment_id=appointment.id, user_id=uids['mechanic'])
    job_order.save()

    as_role('mechanic')
    r = client.put(f'/api/job-orders/{job_order.id}', headers=H, json={'status': 'Parts Requested'})
    assert r.status_code == 200, r.get_json()

    assert not any(c['data'].get('type') == 'job_order_started' for c in no_real_email_or_sms['push'])
