"""Customer loyalty: redemption and vouchers.

Covers the rule set that decides whether a customer can spend points:
balance, reward active, stock, per-customer limit, per-transaction max,
daily cap; the voucher lifecycle (valid / expired / used / unknown code);
and the property that actually protects the balance -- concurrent
redemptions cannot overdraw it.

FIRESTORE_MOCK=1 only.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

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
    """Restore the default rules before every test.

    The Firestore mock store is process-global, so a test that changes a
    setting would otherwise leak it into every test that runs after it.
    """
    from utils import loyalty_service as svc
    svc.save_settings(dict(svc.DEFAULT_SETTINGS))


def _make_customer(name='Redeemer', balance=1000):
    from models.customer import Customer
    from utils import loyalty_service as svc

    customer = Customer(name=name, phone='09171230002',
                        email=f'loyalty-{uuid.uuid4().hex[:8]}@example.test')
    customer.save()
    if balance:
        svc.adjust_points(customer.id, balance, 'Test seed balance')
    return customer


def _make_reward(points_cost=200, reward_type='discount', value=100.0, stock=None,
                 per_customer_limit=0, is_active=True, name='Test Reward'):
    from models.loyalty import Reward

    reward = Reward(name=name, description='For tests', points_cost=points_cost,
                    reward_type=reward_type, value=value, stock=stock,
                    per_customer_limit=per_customer_limit, is_active=is_active)
    reward.save()
    return reward


def _reset_settings():
    from utils import loyalty_service as svc
    svc.save_settings(dict(svc.DEFAULT_SETTINGS))


# ---- balance and eligibility ----

def test_redeem_succeeds_and_issues_a_voucher(env):
    from utils import loyalty_service as svc
    _reset_settings()
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=150.0)

    redemption = svc.redeem_reward(customer.id, reward.id)

    assert redemption.points_spent == 200
    assert redemption.status == 'active'
    assert redemption.voucher_code.startswith('AMR-')
    assert redemption.expires_at is not None
    assert svc.get_account(customer.id)['points_balance'] == 800
    assert [t.type for t in _transactions(customer.id) if t.type == 'redeem'] == ['redeem']


def test_redeem_rejects_insufficient_balance(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=100)
    reward = _make_reward(points_cost=200)

    with pytest.raises(svc.LoyaltyError) as exc:
        svc.redeem_reward(customer.id, reward.id)
    assert 'Insufficient points' in exc.value.message
    assert svc.get_account(customer.id)['points_balance'] == 100


def test_redeem_rejects_inactive_reward(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(is_active=False)

    with pytest.raises(svc.LoyaltyError) as exc:
        svc.redeem_reward(customer.id, reward.id)
    assert 'no longer available' in exc.value.message


def test_redeem_rejects_out_of_stock(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(stock=0)

    with pytest.raises(svc.LoyaltyError) as exc:
        svc.redeem_reward(customer.id, reward.id)
    assert 'out of stock' in exc.value.message.lower()


def test_stock_decrements_and_unlimited_stock_never_runs_out(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)

    limited = _make_reward(points_cost=100, stock=2, name='Limited')
    svc.redeem_reward(customer.id, limited.id)
    svc.redeem_reward(customer.id, limited.id)
    # Re-read: the redemption transaction updated the document, not this
    # already-materialized model object.
    assert svc.Reward.get(limited.id).stock == 0
    with pytest.raises(svc.LoyaltyError):
        svc.redeem_reward(customer.id, limited.id)

    unlimited = _make_reward(points_cost=100, stock=None, name='Unlimited')
    for _ in range(5):
        svc.redeem_reward(customer.id, unlimited.id)
    assert svc.Reward.get(unlimited.id).stock is None


def test_per_customer_limit_is_enforced(env):
    from utils import loyalty_service as svc
    _reset_settings()
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=100, per_customer_limit=2)

    svc.redeem_reward(customer.id, reward.id)
    svc.redeem_reward(customer.id, reward.id)
    with pytest.raises(svc.LoyaltyError) as exc:
        svc.redeem_reward(customer.id, reward.id)
    assert 'already redeemed this reward 2 time(s)' in exc.value.message
    assert svc.get_account(customer.id)['points_balance'] == 800


def test_per_customer_limit_is_per_customer_not_global(env):
    from utils import loyalty_service as svc
    reward = _make_reward(points_cost=100, per_customer_limit=1)
    a = _make_customer(name='A', balance=500)
    b = _make_customer(name='B', balance=500)

    svc.redeem_reward(a.id, reward.id)
    # B has their own allowance -- the limit is not consumed by A.
    svc.redeem_reward(b.id, reward.id)


def test_reward_above_per_transaction_cap_is_rejected(env):
    from utils import loyalty_service as svc
    _reset_settings()
    customer = _make_customer(balance=100000)
    reward = _make_reward(points_cost=5000)

    with pytest.raises(svc.LoyaltyError) as exc:
        svc.redeem_reward(customer.id, reward.id)
    assert 'per-transaction limit' in exc.value.message


def test_daily_cap_is_enforced(env):
    from utils import loyalty_service as svc
    _reset_settings()
    customer = _make_customer(balance=10000)
    reward = _make_reward(points_cost=1000)

    svc.redeem_reward(customer.id, reward.id)   # 1000 today
    svc.redeem_reward(customer.id, reward.id)   # 2000 today
    with pytest.raises(svc.LoyaltyError) as exc:
        svc.redeem_reward(customer.id, reward.id)
    assert 'Daily redemption limit reached' in exc.value.message
    assert svc.get_account(customer.id)['points_balance'] == 8000


def test_daily_cap_is_configurable(env):
    from utils import loyalty_service as svc
    _reset_settings()
    svc.save_settings({'daily_redemption_cap': 2500})
    try:
        customer = _make_customer(balance=10000)
        reward = _make_reward(points_cost=1000)
        svc.redeem_reward(customer.id, reward.id)
        svc.redeem_reward(customer.id, reward.id)
        with pytest.raises(svc.LoyaltyError):
            svc.redeem_reward(customer.id, reward.id)
    finally:
        _reset_settings()


def test_daily_cap_resets_for_a_previous_day(env):
    """The cap is per calendar day, so yesterday's redemptions must not
    count against today."""
    from utils import loyalty_service as svc
    from models.loyalty import RewardRedemption
    _reset_settings()
    customer = _make_customer(balance=10000)
    reward = _make_reward(points_cost=1000)

    stale = RewardRedemption(
        customer_id=str(customer.id), reward_id=reward.id, reward_name=reward.name,
        voucher_code='AMR-STALE1', points_spent=2000, status='active',
        expires_at=datetime.utcnow() + timedelta(days=30),
        created_at=datetime.utcnow() - timedelta(days=1))
    stale.save()

    assert svc._points_spent_today(customer.id, svc.get_settings()) == 0
    svc.redeem_reward(customer.id, reward.id)
    svc.redeem_reward(customer.id, reward.id)   # would breach if stale counted


# ---- concurrency ----

def test_concurrent_redemptions_cannot_overdraw(env):
    """Two threads spending the same balance must not both succeed.

    Without a transaction, both read balance=200, both decide 200 is enough
    for a 200-point reward, and both write -- leaving the customer at -200.
    """
    import threading
    from utils import loyalty_service as svc

    _reset_settings()
    customer = _make_customer(balance=200)
    reward = _make_reward(points_cost=200, stock=None)

    results = []
    barrier = threading.Barrier(2)

    def attempt():
        # See the note in test_concurrent_redeem_of_last_stock_sells_it_once:
        # current_app is thread-local, so each worker needs its own context.
        with env['app'].app_context():
            barrier.wait()
            try:
                svc.redeem_reward(customer.id, reward.id)
                results.append('ok')
            except svc.LoyaltyError as e:
                results.append(f'rejected: {e.message}')

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count('ok') == 1, f'expected exactly one success, got {results}'
    assert any(r.startswith('rejected') for r in results)
    assert svc.get_account(customer.id)['points_balance'] == 0


def test_concurrent_redeem_of_last_stock_sells_it_once(env):
    from utils import loyalty_service as svc
    import threading

    customer = _make_customer(balance=10000)
    reward = _make_reward(points_cost=100, stock=1)
    results = []
    barrier = threading.Barrier(2)

    def attempt():
        # Worker threads get their own app context: Flask's `current_app` is
        # thread-local, and the service logs through it. In production this
        # comes free with the request.
        with env['app'].app_context():
            barrier.wait()
            try:
                svc.redeem_reward(customer.id, reward.id)
                results.append('ok')
            except svc.LoyaltyError as e:
                results.append(f'rejected: {e.message}')

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count('ok') == 1
    assert svc.Reward.get(reward.id).stock == 0


# ---- vouchers ----

def test_valid_voucher_reports_its_value(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code

    result = svc.validate_voucher(code)
    assert result['valid'] is True
    assert result['discount_amount'] == 250.0
    assert result['status'] == 'active'
    assert result['customer_id'] == str(customer.id)


def test_unknown_code_is_rejected(env):
    from utils import loyalty_service as svc
    result = svc.validate_voucher('AMR-NOPE123')
    assert result['valid'] is False
    assert 'not found' in result['reason']


def test_empty_code_is_rejected(env):
    from utils import loyalty_service as svc
    assert svc.validate_voucher('')['valid'] is False
    assert svc.validate_voucher(None)['valid'] is False


def test_expired_voucher_is_rejected(env):
    from utils import loyalty_service as svc
    from models.loyalty import RewardRedemption
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code

    redemption = RewardRedemption.get(code)
    redemption.expires_at = datetime.utcnow() - timedelta(days=1)
    redemption.save()

    result = svc.validate_voucher(code)
    assert result['valid'] is False
    assert result['status'] == 'expired'
    assert 'expired' in result['reason']


def test_used_voucher_is_rejected(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code

    redemption = svc.RewardRedemption.get(code)
    redemption.status = 'used'
    redemption.used_at = datetime.utcnow()
    redemption.used_on_sale_id = 'sale-abc'
    redemption.save()

    result = svc.validate_voucher(code)
    assert result['valid'] is False
    assert result['status'] == 'used'
    assert 'already been used' in result['reason']


def test_voucher_code_is_case_insensitive(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code
    assert svc.validate_voucher(code.lower())['valid'] is True


def test_voucher_codes_are_unique(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=100000)
    reward = _make_reward(points_cost=100)
    codes = {svc.redeem_reward(customer.id, reward.id).voucher_code for _ in range(10)}
    assert len(codes) == 10


def test_voucher_higher_than_sale_total_is_flagged(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=500.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code

    result = svc.validate_voucher(code, sale_total=100.0)
    assert result['valid'] is False
    assert 'greater than the sale total' in result['reason']


# ---- applying a voucher to a sale ----

def _make_sale(total=2000.0):
    from models.customer import Customer
    from models.vehicle import Vehicle
    from models.job_order import JobOrder
    from models.sale import Sale

    customer = Customer(name='Voucher Buyer', phone='09171230003',
                        email=f'loyalty-{uuid.uuid4().hex[:8]}@example.test')
    customer.save()
    vehicle = Vehicle(customer_id=customer.id, make='Honda', model='Civic',
                      license_plate=f'XYZ{uuid.uuid4().hex[:6].upper()[:3]}')
    vehicle.save()
    job_order = JobOrder(vehicle_id=vehicle.id, status='Completed',
                         labor_cost=total, parts_cost=0.0, total_cost=total)
    job_order.save()
    sale = Sale(job_order_id=job_order.id, invoice_number=f'INV-VCH-{uuid.uuid4().hex[:8]}',
                subtotal=total, tax=0.0, discount=0.0, total=total,
                payment_status='Pending')
    sale.save()
    return customer, sale


def test_applying_a_voucher_discounts_the_sale_and_marks_it_used(env):
    from utils import loyalty_service as svc
    from models.sale import Sale
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code
    _, sale = _make_sale(total=2000.0)

    svc.apply_voucher_to_sale(sale, code, {'id': 'staff-1'})

    stored = Sale.get(sale.id)
    assert stored.voucher_code == code
    assert stored.voucher_discount == 250.0
    assert stored.discount == 250.0
    assert stored.total == 1750.0

    redemption = svc.RewardRedemption.get(code)
    assert redemption.status == 'used'
    assert redemption.used_on_sale_id == sale.id


def test_a_voucher_cannot_be_applied_twice(env):
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code
    _, sale = _make_sale(total=2000.0)
    svc.apply_voucher_to_sale(sale, code, {'id': 'staff-1'})

    _, other_sale = _make_sale(total=2000.0)
    with pytest.raises(svc.LoyaltyError) as exc:
        svc.apply_voucher_to_sale(other_sale, code, {'id': 'staff-1'})
    assert 'already been used' in exc.value.message


def test_applying_an_invalid_code_leaves_the_sale_untouched(env):
    from utils import loyalty_service as svc
    from models.sale import Sale
    _, sale = _make_sale(total=2000.0)

    with pytest.raises(svc.LoyaltyError):
        svc.apply_voucher_to_sale(sale, 'AMR-BOGUS1', {'id': 'staff-1'})

    stored = Sale.get(sale.id)
    assert stored.discount == 0.0
    assert stored.total == 2000.0
    assert stored.voucher_code is None


def test_sale_route_applies_a_voucher_code(env):
    from models.sale import Sale
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code
    _, sale = _make_sale(total=2000.0)

    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'voucher_code': code})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['sale']['total'] == 1750.0
    assert r.get_json()['sale']['voucher_code'] == code


def test_sale_route_rejects_a_bad_voucher_code_with_400(env):
    _, sale = _make_sale(total=2000.0)
    r = env['client'].put(f'/api/sales/{sale.id}', headers=env['H'],
                          json={'voucher_code': 'AMR-BOGUS1'})
    assert r.status_code == 400
    assert 'voucher' in r.get_json()['error'].lower()


def test_voucher_applied_then_refunded_does_not_restore_the_voucher(env):
    """A used voucher stays used through a refund. Restoring it is a
    judgement call, made through the manual adjustment endpoint."""
    from utils import loyalty_service as svc
    customer = _make_customer(balance=1000)
    reward = _make_reward(points_cost=200, value=250.0)
    code = svc.redeem_reward(customer.id, reward.id).voucher_code
    _, sale = _make_sale(total=2000.0)
    svc.apply_voucher_to_sale(sale, code, {'id': 'staff-1'})

    sale.payment_status = 'Refunded'
    svc.reverse_points_for_sale(sale, 'Refunded')

    redemption = svc.RewardRedemption.get(code)
    assert redemption.status == 'used'


# ---- admin catalog ----

def test_admin_can_create_update_and_deactivate_a_reward(env):
    from utils import loyalty_service as svc
    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': 'Free Car Wash', 'description': 'One free wash',
                                 'points_cost': 300, 'reward_type': 'service',
                                 'value': 'Free exterior car wash', 'stock': 10})
    assert r.status_code == 201, r.get_json()
    reward_id = r.get_json()['reward']['id']
    assert r.get_json()['reward']['in_stock'] is True

    r = env['client'].put(f'/api/loyalty/admin/rewards/{reward_id}', headers=env['admin_H'],
                          json={'points_cost': 350})
    assert r.status_code == 200
    assert r.get_json()['reward']['points_cost'] == 350

    r = env['client'].delete(f'/api/loyalty/admin/rewards/{reward_id}', headers=env['admin_H'])
    assert r.status_code == 200
    # Deactivation, not a hard delete: the reward document still exists so
    # vouchers already issued against it keep resolving.
    assert r.get_json()['reward']['is_active'] is False
    assert svc.Reward.get(reward_id) is not None
    # And it's gone from the customer-facing catalog.
    r = env['client'].get('/api/loyalty/admin/rewards', headers=env['admin_H'],
                          json=None, query_string={'include_inactive': '0'})
    assert reward_id not in [x['id'] for x in r.get_json()['rewards']]


def test_admin_rejects_a_reward_priced_above_the_per_transaction_cap(env):
    _reset_settings()
    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': 'Unreachable', 'points_cost': 99999,
                                 'reward_type': 'discount', 'value': 100})
    assert r.status_code == 400
    assert 'never be redeemed' in r.get_json()['error']


def test_admin_rejects_invalid_reward_input(env):
    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': '', 'points_cost': 100})
    assert r.status_code == 400

    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': 'Bad cost', 'points_cost': -50})
    assert r.status_code == 400

    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': 'Bad type', 'points_cost': 50, 'reward_type': 'nonsense'})
    assert r.status_code == 400

    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': 'x' * 200, 'points_cost': 50})
    assert r.status_code == 400


# ---- admin settings ----

def test_settings_round_trip_and_are_validated(env):
    _reset_settings()
    r = env['client'].get('/api/loyalty/admin/settings', headers=env['admin_H'])
    assert r.status_code == 200
    defaults = r.get_json()
    assert defaults['amount_per_point'] == 100.0
    assert defaults['timezone'] == 'Asia/Manila'
    assert defaults['max_redemption_per_transaction'] == 1000
    assert defaults['daily_redemption_cap'] == 2000
    assert defaults['points_expiry_days'] == 0
    assert defaults['tier_thresholds']['Gold'] > defaults['tier_thresholds']['Silver']

    r = env['client'].put('/api/loyalty/admin/settings', headers=env['admin_H'],
                          json={'amount_per_point': 250.0})
    assert r.status_code == 200
    assert r.get_json()['settings']['amount_per_point'] == 250.0

    r = env['client'].put('/api/loyalty/admin/settings', headers=env['admin_H'],
                          json={'amount_per_point': -5})
    assert r.status_code == 400

    r = env['client'].put('/api/loyalty/admin/settings', headers=env['admin_H'],
                          json={'daily_redemption_cap': 0})
    assert r.status_code == 400

    r = env['client'].put('/api/loyalty/admin/settings', headers=env['admin_H'],
                          json={'tier_thresholds': {'Bronze': 900, 'Silver': 100, 'Gold': 50}})
    assert r.status_code == 400
    _reset_settings()


def _transactions(customer_id):
    from models.loyalty import LoyaltyTransaction
    return LoyaltyTransaction.query.filter_by(customer_id=str(customer_id)).all()
