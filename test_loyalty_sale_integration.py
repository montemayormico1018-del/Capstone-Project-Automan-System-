"""Loyalty must never be able to cost us a sale.

The earning/reversal hooks run inside the payment, refund and delete paths
of routes/sales.py. A points rule that is still pending adviser approval
should be able to fail in any way -- a bug, a Firestore error, a bad settings
document -- without a customer losing their payment record or staff losing
their invoice. These tests make the sale fail loudly and the loyalty fail
silently, which is the intended split.

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
def env():
    import firebase_auth_utils
    from models.user import User
    from app import create_app

    uids = {}
    for role in ('staff', 'admin'):
        uid = f'loytest-{role}-{uuid.uuid4().hex[:8]}'
        User(id=uid, username=f'{role}_{uid}', full_name=f'{role.title()} Tester',
             email=f'{uid}@example.test', role=role, contact_number='09123456789',
             is_active=True, is_verified=True).save()
        uids[role] = uid

    firebase_auth_utils.verify_id_token = lambda token: {'uid': uids.get(token, 'unknown')}

    app = create_app()
    with app.app_context():
        yield {
            'app': app,
            'client': app.test_client(),
            'H': {'Authorization': 'Bearer staff'},
            'admin_H': {'Authorization': 'Bearer admin'},
        }


@pytest.fixture(autouse=True)
def default_settings(env):
    from utils import loyalty_service as svc
    svc.save_settings(dict(svc.DEFAULT_SETTINGS))


def _make_sale_chain(total=5000.0):
    from models.customer import Customer
    from models.vehicle import Vehicle
    from models.job_order import JobOrder
    from models.sale import Sale

    customer = Customer(name='Integration Customer', phone='09171230011',
                        email=f'loyalty-{uuid.uuid4().hex[:8]}@example.test')
    customer.save()
    vehicle = Vehicle(customer_id=customer.id, make='Mitsubishi',
                      model='Mirage', license_plate=f'UVW{uuid.uuid4().hex[:6].upper()[:3]}')
    vehicle.save()
    job_order = JobOrder(vehicle_id=vehicle.id, status='Completed',
                         labor_cost=total, parts_cost=0.0, total_cost=total)
    job_order.save()
    sale = Sale(job_order_id=job_order.id, invoice_number=f'INV-INT-{uuid.uuid4().hex[:8]}',
                subtotal=total, tax=0.0, discount=0.0, total=total,
                payment_status='Pending')
    sale.save()
    return customer, job_order, sale


def _balance(customer):
    from utils import loyalty_service as svc
    return svc.get_account(customer.id)['points_balance']


def test_payment_succeeds_when_awarding_points_explodes(env, monkeypatch):
    from models.sale import Sale
    import routes.sales as sales_module

    customer, job_order, sale = _make_sale_chain(total=5000.0)

    def explode(*args, **kwargs):
        raise RuntimeError('loyalty is on fire')

    monkeypatch.setattr(sales_module, 'award_points_for_sale', explode)

    r = env['client'].post(f'/api/sales/{sale.id}/payment', headers=env['H'],
                           json={'amount': 5000, 'payment_method': 'Cash'})
    assert r.status_code == 200, r.get_json()

    # The sale saved and is recorded as paid...
    stored = Sale.get(sale.id)
    assert stored.payment_status == 'Paid'
    assert stored.amount_paid == 5000.0
    # ...and no points were awarded, with no crash and no partial state.
    assert _balance(customer) == 0


def test_sale_update_succeeds_when_awarding_points_explodes(env, monkeypatch):
    from models.sale import Sale
    import routes.sales as sales_module

    customer, job_order, sale = _make_sale_chain(total=5000.0)
    monkeypatch.setattr(sales_module, 'award_points_for_sale',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))

    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'payment_status': 'Paid'})
    assert r.status_code == 200, r.get_json()
    assert Sale.get(sale.id).payment_status == 'Paid'
    assert _balance(customer) == 0


def test_refund_succeeds_when_reversing_points_explodes(env, monkeypatch):
    from models.sale import Sale
    import routes.sales as sales_module

    customer, job_order, sale = _make_sale_chain(total=5000.0)
    env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                      json={'payment_status': 'Paid'})
    assert _balance(customer) == 50

    monkeypatch.setattr(sales_module, 'reverse_points_for_sale',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))

    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'payment_status': 'Refunded'})
    assert r.status_code == 200, r.get_json()
    assert Sale.get(sale.id).payment_status == 'Refunded'
    # Points are still there because the reversal failed -- which is why the
    # admin adjustment endpoint exists.
    assert _balance(customer) == 50


def test_delete_succeeds_when_reversing_points_explodes(env, monkeypatch):
    from models.sale import Sale
    import routes.sales as sales_module

    customer, job_order, sale = _make_sale_chain(total=5000.0)
    env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                      json={'payment_status': 'Paid'})

    monkeypatch.setattr(sales_module, 'reverse_points_for_sale',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))

    r = env['client'].delete(f'/api/sales/{sale.id}', headers=env['admin_H'])
    assert r.status_code == 200, r.get_json()
    assert Sale.get(sale.id).is_deleted is True


def test_payment_succeeds_with_a_corrupt_settings_document(env, monkeypatch):
    """A settings document that can't be read must degrade to the defaults,
    not take the payment path down."""
    from models.sale import Sale
    from utils import loyalty_service as svc

    customer, job_order, sale = _make_sale_chain(total=5000.0)

    def broken_settings():
        raise RuntimeError('settings store unavailable')

    monkeypatch.setattr(svc, 'get_settings', broken_settings)
    monkeypatch.setattr('routes.sales.award_points_for_sale',
                        lambda *a, **k: svc.award_points_for_sale(*a, **k))

    r = env['client'].post(f'/api/sales/{sale.id}/payment', headers=env['H'],
                           json={'amount': 5000, 'payment_method': 'Cash'})
    assert r.status_code == 200, r.get_json()
    assert Sale.get(sale.id).payment_status == 'Paid'


def test_a_broken_voucher_does_not_corrupt_the_sale(env, monkeypatch):
    """A voucher failure is different from a points failure: the staff member
    typed a code that didn't work, so the request must fail cleanly and the
    sale must be left exactly as it was."""
    from models.sale import Sale
    import routes.sales as sales_module

    _, _, sale = _make_sale_chain(total=2000.0)
    monkeypatch.setattr(sales_module, 'apply_voucher_to_sale',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('voucher service down')))

    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'voucher_code': 'AMR-WHATEVER', 'notes': 'test'})
    # The 400 comes from the route's own handler, not a 500.
    assert r.status_code == 400

    stored = Sale.get(sale.id)
    assert stored.total == 2000.0
    assert stored.discount == 0.0
    assert stored.voucher_code is None


def test_a_voucher_failure_does_not_stop_the_payment_itself(env, monkeypatch):
    """The two paths are independent: a broken voucher must not prevent a
    later payment on the same sale from being recorded."""
    from models.sale import Sale
    import routes.sales as sales_module

    customer, job_order, sale = _make_sale_chain(total=2000.0)
    monkeypatch.setattr(sales_module, 'apply_voucher_to_sale',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))

    env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                      json={'voucher_code': 'AMR-WHATEVER'})
    r = env['client'].post(f'/api/sales/{sale.id}/payment', headers=env['H'],
                           json={'amount': 2000, 'payment_method': 'Cash'})
    assert r.status_code == 200, r.get_json()
    assert Sale.get(sale.id).payment_status == 'Paid'
    assert _balance(customer) == 20
