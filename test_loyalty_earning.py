"""Customer loyalty: earning points.

The rules under test:
- points are earned when a sale is fully PAID, not when the job order is
  merely completed;
- the rate is configurable (system settings), floored;
- the basis is total - voucher_discount - points_redeemed_amount;
- awarding is idempotent per sale (earn_<sale_id>), so re-marking a sale
  Paid -- or re-saving it, or racing two requests -- awards once;
- a cancelled/refunded/deleted sale reverses through a negative
  'reversal' transaction, itself idempotent, clamped at a zero balance.

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
    """App + one header set per role.

    The bearer token names the identity, so a single patched
    verify_id_token can serve staff, admin and customer requests from the
    same client -- which is what lets the authorization tests flip roles
    without rebuilding the app.
    """
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
            'uids': uids,
        }


def _make_sale_chain(total=5000.0, customer_name='Loyalty Earner'):
    """Customer -> vehicle -> job order -> sale, wired the way the real
    payment flow wires them (the customer is reached through the job
    order's vehicle)."""
    from models.customer import Customer
    from models.vehicle import Vehicle
    from models.job_order import JobOrder
    from models.sale import Sale

    customer = Customer(
        name=customer_name,
        phone='09171230001',
        email=f'loyalty-{uuid.uuid4().hex[:8]}@example.test',
    )
    customer.save()
    vehicle = Vehicle(customer_id=customer.id, make='Toyota', model='Vios',
                      license_plate=f'ABC{uuid.uuid4().hex[:6].upper()[:3]}')
    vehicle.save()
    job_order = JobOrder(vehicle_id=vehicle.id, status='Completed',
                         labor_cost=total, parts_cost=0.0, total_cost=total)
    job_order.save()
    sale = Sale(job_order_id=job_order.id,
                invoice_number=f'INV-LOY-{uuid.uuid4().hex[:8]}',
                subtotal=total, tax=0.0, discount=0.0, total=total,
                payment_status='Pending')
    sale.save()
    return customer, job_order, sale


def _account(customer):
    from utils import loyalty_service as svc
    return svc.get_account(customer.id)


@pytest.fixture(autouse=True)
def default_settings(env):
    """Restore the default earning rules before every test.

    The Firestore mock store is process-global (one client, one _store), so
    a test that changes a setting leaks it into every test that runs after it.
    Saving the defaults up front makes each test independent of ordering.
    """
    from utils import loyalty_service as svc
    svc.save_settings(dict(svc.DEFAULT_SETTINGS))


def _txns(customer_id):
    from models.loyalty import LoyaltyTransaction
    return LoyaltyTransaction.query.filter_by(customer_id=str(customer_id)).all()


# ---- calculation and rounding ----

def test_points_floor_at_default_rate(env):
    from utils import loyalty_service as svc
    settings = svc.get_settings()
    # 1 point per PHP 100 at the defaults.
    assert settings['amount_per_point'] == 100.0
    assert svc.points_for_amount(100.0, settings) == 1
    assert svc.points_for_amount(5500.0, settings) == 55


def test_points_floor_rounds_down_not_nearest(env):
    from utils import loyalty_service as svc
    settings = svc.get_settings()
    # 199 -> 1 point, never 2. Flooring is what keeps the shop from
    # systematically over-paying out points.
    assert svc.points_for_amount(199.0, settings) == 1
    assert svc.points_for_amount(99.0, settings) == 0
    assert svc.points_for_amount(9999.0, settings) == 99


def test_rate_is_configurable(env):
    from utils import loyalty_service as svc
    svc.save_settings({'amount_per_point': 50.0, 'points_per_amount': 1.0})
    try:
        assert svc.points_for_amount(5000.0, svc.get_settings()) == 100
    finally:
        svc.save_settings({'amount_per_point': 100.0, 'points_per_amount': 1.0})
    assert svc.points_for_amount(5000.0, svc.get_settings()) == 50


def test_points_per_amount_multiplier_is_configurable(env):
    from utils import loyalty_service as svc
    svc.save_settings({'points_per_amount': 2.0})
    try:
        assert svc.points_for_amount(1000.0, svc.get_settings()) == 20
    finally:
        svc.save_settings({'points_per_amount': 1.0})


def test_earning_basis_excludes_voucher_and_points(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    # Sale total is post-voucher (calculate_total folds the voucher into
    # `discount`), so the basis rule un-folds it first and then subtracts
    # both exclusions: 4500 + 500 (un-fold) - 500 (voucher) - 200 (points).
    sale.total = 4500.0
    sale.discount = 500.0
    sale.voucher_discount = 500.0
    sale.points_redeemed_amount = 200.0
    sale.save()
    assert svc.earning_base_amount(sale) == 4300.0
    assert svc.points_for_amount(svc.earning_base_amount(sale), svc.get_settings()) == 43


def test_earning_basis_ignores_voucher_when_none_applied(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.total = 5000.0
    sale.save()
    assert svc.earning_base_amount(sale) == 5000.0


def test_earning_basis_never_goes_negative(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=1000.0)
    sale.total = 1000.0
    sale.points_redeemed_amount = 1500.0
    sale.save()
    assert svc.earning_base_amount(sale) == 0.0


def test_points_never_earned_below_the_threshold(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=50.0)
    sale.total = 50.0
    sale.save()
    assert svc.award_points_for_sale(sale) is None
    assert _account(customer)['points_balance'] == 0


# ---- the Paid trigger ----

def test_points_awarded_when_sale_marked_paid(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()
    assert svc.award_points_for_sale(sale) == 50
    account = _account(customer)
    assert account['points_balance'] == 50
    assert account['lifetime_points'] == 50
    assert account['tier'] == 'Bronze'


def test_completed_job_order_alone_earns_nothing(env):
    """Earning is tied to payment, not completion. A job order sitting
    Completed with an unpaid sale is work in progress."""
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    assert job_order.status == 'Completed'
    assert sale.payment_status == 'Pending'
    assert svc.award_points_for_sale(sale) is None
    assert _account(customer)['points_balance'] == 0


def test_sale_without_a_resolvable_customer_earns_nothing(env):
    from models.sale import Sale
    from utils import loyalty_service as svc
    sale = Sale(invoice_number='INV-ORPHAN', subtotal=5000.0, total=5000.0,
                payment_status='Paid')
    sale.save()
    assert svc.resolve_customer_id_for_sale(sale) is None
    assert svc.award_points_for_sale(sale) is None


# ---- idempotency ----

def test_awarding_twice_awards_once(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()

    assert svc.award_points_for_sale(sale) == 50
    assert svc.award_points_for_sale(sale) is None
    assert svc.award_points_for_sale(sale) is None

    assert _account(customer)['points_balance'] == 50
    earns = [t for t in _txns(customer.id) if t.type == 'earn']
    assert len(earns) == 1
    assert earns[0].id == f'earn_{sale.id}'
    assert earns[0].points == 50


def test_earn_transaction_id_is_deterministic(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()
    svc.award_points_for_sale(sale)
    earn_ids = [t.id for t in _txns(customer.id) if t.type == 'earn']
    assert earn_ids == [f'earn_{sale.id}']


def test_payment_endpoint_twice_awards_once(env):
    """The real path: two payment calls that together settle the invoice."""
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    r1 = env['client'].post(f'/api/sales/{sale.id}/payment', headers=env['H'],
                            json={'amount': 3000, 'payment_method': 'Cash'})
    assert r1.status_code == 200, r1.get_json()
    r2 = env['client'].post(f'/api/sales/{sale.id}/payment', headers=env['H'],
                            json={'amount': 2000, 'payment_method': 'Cash'})
    assert r2.status_code == 200, r2.get_json()

    account = _account(customer)
    assert account['points_balance'] == 50
    assert len([t for t in _txns(customer.id) if t.type == 'earn']) == 1


def test_re_saving_a_paid_sale_does_not_double_award(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'payment_status': 'Paid'})
    assert r.status_code == 200, r.get_json()
    # A second, unrelated edit to the same paid sale.
    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'notes': 'Customer called about the invoice'})
    assert r.status_code == 200, r.get_json()
    assert _account(customer)['points_balance'] == 50
    assert len([t for t in _txns(customer.id) if t.type == 'earn']) == 1


# ---- reversal ----

def test_refund_reverses_points(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()
    svc.award_points_for_sale(sale)

    sale.payment_status = 'Refunded'
    assert svc.reverse_points_for_sale(sale, 'Customer cancelled the service') == 50

    account = _account(customer)
    assert account['points_balance'] == 0
    assert account['lifetime_points'] == 0
    reversals = [t for t in _txns(customer.id) if t.type == 'reversal']
    assert len(reversals) == 1
    assert reversals[0].points == -50
    assert reversals[0].id == f'reversal_{sale.id}'


def test_reversing_twice_reverses_once(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()
    svc.award_points_for_sale(sale)

    assert svc.reverse_points_for_sale(sale, 'First reversal') == 50
    assert svc.reverse_points_for_sale(sale, 'Second reversal') is None
    assert _account(customer)['points_balance'] == 0
    assert len([t for t in _txns(customer.id) if t.type == 'reversal']) == 1


def test_reversal_clamps_at_zero_balance(env):
    """A customer who already spent the points can't be driven negative."""
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()
    svc.award_points_for_sale(sale)          # 50 points
    svc.adjust_points(customer.id, -50, 'Customer spent points at the counter')

    reversed_points = svc.reverse_points_for_sale(sale, 'Refunded')
    assert reversed_points == 0
    assert _account(customer)['points_balance'] == 0


def test_reversal_of_an_unpaid_sale_does_nothing(env):
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    assert svc.reverse_points_for_sale(sale, 'Refund') is None
    assert _txns(customer.id) == []


def test_refund_endpoint_reverses_points(env):
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                      json={'payment_status': 'Paid'})
    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'payment_status': 'Refunded'})
    assert r.status_code == 200, r.get_json()
    assert _account(customer)['points_balance'] == 0
    assert len([t for t in _txns(customer.id) if t.type == 'reversal']) == 1


def test_admin_delete_reverses_points(env):
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                      json={'payment_status': 'Paid'})
    r = env['client'].delete(f'/api/sales/{sale.id}', headers=env['admin_H'])
    assert r.status_code == 200, r.get_json()
    assert _account(customer)['points_balance'] == 0


def test_ledger_balance_after_is_consistent(env):
    """Every row's balance_after has to chain to the account balance --
    that's what makes the append-only ledger auditable."""
    from utils import loyalty_service as svc
    customer, job_order, sale = _make_sale_chain(total=5000.0)
    sale.payment_status = 'Paid'
    sale.save()
    svc.award_points_for_sale(sale)
    svc.adjust_points(customer.id, 25, 'Goodwill for the long wait')
    sale.payment_status = 'Refunded'
    svc.reverse_points_for_sale(sale, 'Refunded')

    rows = sorted(_txns(customer.id), key=lambda t: t.created_at)
    running = 0
    for row in rows:
        running += row.points
        assert row.balance_after == running, f'ledger broke at {row.id}'
    assert running == _account(customer)['points_balance']
