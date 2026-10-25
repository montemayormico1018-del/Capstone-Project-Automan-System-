"""Panel item #5 (validate data before accepting): sales.py and
job_orders.py had the same class of gap already fixed in inventory.py --
money fields (tax, discount, amount_paid, labor_cost) went straight from
request JSON into a float()/assignment with no bound. A negative value
silently corrupted a sale/job-order's total; a non-numeric one crashed
with an unhandled 500 instead of a clean 400.

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

    uid = f'moneyval-staff-{uuid.uuid4().hex[:8]}'
    User(
        id=uid, username=f'staff_{uid}', full_name='Staff Tester',
        email=f'{uid}@example.test', role='staff', contact_number='09123456789',
        is_active=True, is_verified=True,
    ).save()
    firebase_auth_utils.verify_id_token = lambda token: {'uid': uid}

    from app import create_app
    app = create_app()
    client = app.test_client()
    return {'client': client, 'H': {'Authorization': 'Bearer fake'}}


@pytest.fixture
def completed_job_order():
    from models.customer import Customer
    from models.appointment import Appointment
    from models.job_order import JobOrder

    customer = Customer(name='Money Customer', phone='09171234567', email='money@example.test')
    customer.save()
    appt = Appointment(status='Approved', customer_id=customer.id,
                        preferred_date='2026-12-15', preferred_time='09:00:00')
    appt.save()
    jo = JobOrder(appointment_id=appt.id, status='Completed', labor_cost=1000.0, parts_cost=500.0, total_cost=1500.0)
    jo.save()
    return jo


@pytest.fixture
def pending_job_order():
    from models.customer import Customer
    from models.appointment import Appointment
    from models.job_order import JobOrder

    customer = Customer(name='Money Customer 2', phone='09171234568', email='money2@example.test')
    customer.save()
    appt = Appointment(status='Approved', customer_id=customer.id,
                        preferred_date='2026-12-15', preferred_time='09:00:00')
    appt.save()
    jo = JobOrder(appointment_id=appt.id, status='Pending', labor_cost=0.0)
    jo.save()
    return jo


# ---- create_sale ----

def test_create_sale_rejects_negative_tax(api, completed_job_order):
    r = api['client'].post('/api/sales', headers=api['H'],
                            json={'job_order_id': completed_job_order.id, 'tax': -10})
    assert r.status_code == 400
    assert 'negative' in r.get_json()['error'].lower()


def test_create_sale_accepts_valid_tax_and_discount(api, completed_job_order):
    r = api['client'].post('/api/sales', headers=api['H'],
                            json={'job_order_id': completed_job_order.id, 'tax': 50, 'discount': 20})
    assert r.status_code == 201, r.get_json()


# ---- update_sale ----

@pytest.fixture
def sale(completed_job_order):
    from models.sale import Sale
    s = Sale(job_order_id=completed_job_order.id, invoice_number='INV-TEST-0001',
              subtotal=1500.0, tax=0.0, discount=0.0, total=1500.0, payment_status='Pending')
    s.save()
    return s


def test_update_sale_rejects_negative_amount_paid(api, sale):
    r = api['client'].put(f'/api/sales/{sale.id}', headers=api['H'], json={'amount_paid': -100})
    assert r.status_code == 400
    assert 'negative' in r.get_json()['error'].lower()


def test_update_sale_rejects_non_numeric_amount_paid(api, sale):
    r = api['client'].put(f'/api/sales/{sale.id}', headers=api['H'], json={'amount_paid': 'lots'})
    assert r.status_code == 400


def test_update_sale_rejects_invalid_payment_status(api, sale):
    # 'Refunded' used to be the canonical invalid example here. It is a
    # valid status now (it reverses loyalty points -- see
    # routes/sales.py's VALID_PAYMENT_STATUSES), so the invalid case needs a
    # status that is still not allowed.
    r = api['client'].put(f'/api/sales/{sale.id}', headers=api['H'], json={'payment_status': 'NotAPaymentStatus'})
    assert r.status_code == 400


def test_update_sale_accepts_refunded_status(api, sale):
    r = api['client'].put(f'/api/sales/{sale.id}', headers=api['H'], json={'payment_status': 'Refunded'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['sale']['payment_status'] == 'Refunded'


def test_update_sale_accepts_valid_amount_paid(api, sale):
    r = api['client'].put(f'/api/sales/{sale.id}', headers=api['H'], json={'amount_paid': 1500})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['sale']['payment_status'] == 'Paid'


# ---- record_payment ----

def test_record_payment_rejects_non_numeric_amount(api, sale):
    r = api['client'].post(f'/api/sales/{sale.id}/payment', headers=api['H'], json={'amount': 'free'})
    assert r.status_code == 400


def test_record_payment_rejects_negative_amount(api, sale):
    r = api['client'].post(f'/api/sales/{sale.id}/payment', headers=api['H'], json={'amount': -50})
    assert r.status_code == 400


def test_record_payment_accepts_valid_amount(api, sale):
    r = api['client'].post(f'/api/sales/{sale.id}/payment', headers=api['H'], json={'amount': 500})
    assert r.status_code == 200, r.get_json()


# ---- job order labor_cost ----

def test_create_job_order_rejects_negative_labor_cost(api):
    from models.customer import Customer
    from models.appointment import Appointment
    from models.user import User

    customer = Customer(name='JO Customer', phone='09171234569', email='jo@example.test')
    customer.save()
    appt = Appointment(status='Approved', customer_id=customer.id,
                        preferred_date='2026-12-15', preferred_time='09:00:00')
    appt.save()
    mech_id = f'mech-{uuid.uuid4().hex[:8]}'
    User(id=mech_id, username=mech_id, full_name='Mech', email=f'{mech_id}@example.test',
         role='mechanic', contact_number='09123456780', is_active=True, is_verified=True,
         availability='available').save()

    r = api['client'].post('/api/job-orders', headers=api['H'],
                            json={'appointment_id': appt.id, 'mechanic_id': mech_id, 'labor_cost': -200})
    assert r.status_code == 400
    assert 'negative' in r.get_json()['error'].lower()


def test_update_job_order_rejects_negative_labor_cost(api, pending_job_order):
    r = api['client'].put(f'/api/job-orders/{pending_job_order.id}', headers=api['H'],
                           json={'labor_cost': -50})
    assert r.status_code == 400
    assert 'negative' in r.get_json()['error'].lower()


def test_update_job_order_accepts_valid_labor_cost(api, pending_job_order):
    r = api['client'].put(f'/api/job-orders/{pending_job_order.id}', headers=api['H'],
                           json={'labor_cost': 750})
    assert r.status_code == 200, r.get_json()
