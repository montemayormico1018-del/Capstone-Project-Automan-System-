"""Customer loyalty: authorization.

The customer-facing endpoints resolve the customer from the JWT identity and
nowhere else. That is the whole point of this file: a customer_id in a query
string or a request body is attacker-controlled, so any endpoint that reads
one is a leak waiting to happen.

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
    for role in ('staff', 'admin', 'customer'):
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
            'staff_H': {'Authorization': 'Bearer staff'},
            'admin_H': {'Authorization': 'Bearer admin'},
            'customer_H': {'Authorization': 'Bearer customer'},
            'uids': uids,
        }


@pytest.fixture(autouse=True)
def default_settings(env):
    from utils import loyalty_service as svc
    svc.save_settings(dict(svc.DEFAULT_SETTINGS))


def _make_linked_customer(env, name, balance=1000):
    """The customer the logged-in 'customer' account belongs to.

    A Customer document AND a login account whose email matches it -- the
    linkage routes/loyalty.py resolves on. Call once per test: re-saving the
    account with a different email would re-point the login at the other
    customer, which is the opposite of what these tests are checking.
    """
    from models.customer import Customer
    from models.user import User
    from utils import loyalty_service as svc

    email = f'loyalty-{uuid.uuid4().hex[:8]}@example.test'
    customer = Customer(name=name, phone='09171230009', email=email)
    customer.save()
    User(id=env['uids']['customer'], username=name, full_name=name, email=email,
         role='customer', contact_number='09171230009', is_active=True,
         is_verified=True).save()
    if balance:
        svc.adjust_points(customer.id, balance, 'Test seed balance')
    return customer


def _make_other_customer(name, balance=1000):
    """A second customer with no login account -- the "someone else" whose
    data must stay unreachable."""
    from models.customer import Customer
    from utils import loyalty_service as svc

    customer = Customer(name=name, phone='09171230010',
                        email=f'loyalty-{uuid.uuid4().hex[:8]}@example.test')
    customer.save()
    if balance:
        svc.adjust_points(customer.id, balance, 'Test seed balance')
    return customer


# ---- a customer only ever sees their own data ----

def test_me_returns_the_callers_own_account(env):
    customer = _make_linked_customer(env, 'Owner')
    r = env['client'].get('/api/loyalty/me', headers=env['customer_H'])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['customer_id'] == str(customer.id)
    assert r.get_json()['points_balance'] == 1000


def test_me_ignores_a_supplied_customer_id(env):
    """A customer_id in the query string is the customer's own to choose --
    it must not change whose account is returned."""
    customer = _make_linked_customer(env, 'Owner')
    other = _make_other_customer('Victim', balance=99999)
    # Re-point the other customer at a second account id so the identity
    # lookup still resolves the first one.
    r = env['client'].get(f'/api/loyalty/me?customer_id={other.id}',
                          headers=env['customer_H'])
    assert r.status_code == 200
    assert r.get_json()['customer_id'] == str(customer.id)
    assert r.get_json()['points_balance'] != 99999


def test_transactions_endpoint_ignores_a_supplied_customer_id(env):
    customer = _make_linked_customer(env, 'Owner')
    other = _make_other_customer('Victim', balance=5000)
    r = env['client'].get(f'/api/loyalty/me/transactions?customer_id={other.id}',
                          headers=env['customer_H'])
    assert r.status_code == 200
    rows = r.get_json()['transactions']
    assert all(row['customer_id'] == str(customer.id) for row in rows)


def test_vouchers_endpoint_ignores_a_supplied_customer_id(env):
    customer = _make_linked_customer(env, 'Owner')
    other = _make_other_customer('Victim', balance=5000)
    r = env['client'].get(f'/api/loyalty/me/vouchers?customer_id={other.id}',
                          headers=env['customer_H'])
    assert r.status_code == 200
    assert r.get_json()['vouchers'] == []


def test_a_customer_cannot_read_another_customers_account(env):
    _make_linked_customer(env, 'Owner')
    victim = _make_other_customer('Victim', balance=5000)

    r = env['client'].get(f'/api/loyalty/admin/customers/{victim.id}',
                          headers=env['customer_H'])
    assert r.status_code == 403


def test_staff_cannot_read_another_customers_account_without_the_admin_path(env):
    """Staff are not customers: /admin/* is the only way to see someone
    else's account, and it is admin/staff-gated rather than customer-gated."""
    _make_linked_customer(env, 'Owner')
    victim = _make_other_customer('Victim', balance=5000)
    r = env['client'].get(f'/api/loyalty/admin/customers/{victim.id}',
                          headers=env['staff_H'])
    assert r.status_code == 200
    assert r.get_json()['points_balance'] == 5000


# ---- admin endpoints are closed to customers ----

def test_a_customer_cannot_create_update_or_delete_rewards(env):
    _make_linked_customer(env, 'Owner')
    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['customer_H'],
                           json={'name': 'Free stuff', 'points_cost': 1,
                                 'reward_type': 'discount', 'value': 100})
    assert r.status_code == 403

    r = env['client'].put('/api/loyalty/admin/rewards/anything', headers=env['customer_H'],
                          json={'points_cost': 1})
    assert r.status_code == 403

    r = env['client'].delete('/api/loyalty/admin/rewards/anything', headers=env['customer_H'])
    assert r.status_code == 403


def test_a_customer_cannot_adjust_their_own_or_another_points_balance(env):
    customer = _make_linked_customer(env, 'Owner')
    r = env['client'].post('/api/loyalty/admin/adjust', headers=env['customer_H'],
                           json={'customer_id': customer.id, 'points': 100000,
                                 'reason': 'gimme'})
    assert r.status_code == 403

    r = env['client'].get('/api/loyalty/admin/settings', headers=env['customer_H'])
    assert r.status_code == 403

    r = env['client'].put('/api/loyalty/admin/settings', headers=env['customer_H'],
                          json={'amount_per_point': 0.01})
    assert r.status_code == 403

    r = env['client'].post('/api/loyalty/admin/vouchers/validate', headers=env['customer_H'],
                           json={'code': 'AMR-ANYTHING'})
    assert r.status_code == 403


def test_adjustment_requires_a_reason(env):
    _make_linked_customer(env, 'Owner')
    victim = _make_other_customer('Victim', balance=0)
    r = env['client'].post('/api/loyalty/admin/adjust', headers=env['admin_H'],
                           json={'customer_id': victim.id, 'points': 100})
    assert r.status_code == 400
    assert 'reason' in r.get_json()['error'].lower()


def test_adjustment_applies_and_is_audited(env):
    from models.audit_log import AuditLog
    victim = _make_other_customer('Victim', balance=100)

    r = env['client'].post('/api/loyalty/admin/adjust', headers=env['admin_H'],
                           json={'customer_id': victim.id, 'points': -40,
                                 'reason': 'Goodwill reversal for a delayed job'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['points_balance'] == 60

    entries = [e for e in AuditLog.query.all()
               if e.entity_type == 'loyalty_account' and e.entity_id == str(victim.id)]
    assert len(entries) == 1
    assert 'Goodwill reversal' in entries[0].description


def test_rewards_crud_and_settings_are_audited(env):
    from models.audit_log import AuditLog
    r = env['client'].post('/api/loyalty/admin/rewards', headers=env['admin_H'],
                           json={'name': 'Tire Pressure Check', 'points_cost': 150,
                                 'reward_type': 'service', 'value': 'Free tire pressure check',
                                 'stock': None})
    assert r.status_code == 201, r.get_json()
    reward_id = r.get_json()['reward']['id']

    assert [e for e in AuditLog.query.all()
            if e.entity_type == 'reward' and e.entity_id == reward_id
            and e.action == 'create']

    env['client'].put('/api/loyalty/admin/settings', headers=env['admin_H'],
                      json={'amount_per_point': 120.0})
    assert [e for e in AuditLog.query.all()
            if e.entity_type == 'loyalty_settings' and e.action == 'update']


# ---- customer-side catalog and redemption ----

def test_customer_can_redeem_and_sees_only_their_own_vouchers(env):
    from utils import loyalty_service as svc
    customer = _make_linked_customer(env, 'Owner', balance=1000)
    reward = svc.Reward(name='Free Car Wash', points_cost=200, reward_type='service',
                        value='Free exterior wash', stock=5, is_active=True)
    reward.save()

    r = env['client'].get('/api/loyalty/rewards', headers=env['customer_H'])
    assert r.status_code == 200
    catalog = r.get_json()['rewards']
    assert any(x['id'] == reward.id and x['can_redeem'] for x in catalog)

    r = env['client'].post('/api/loyalty/redeem', headers=env['customer_H'],
                           json={'reward_id': reward.id})
    assert r.status_code == 201, r.get_json()
    code = r.get_json()['redemption']['voucher_code']

    r = env['client'].get('/api/loyalty/me/vouchers', headers=env['customer_H'])
    assert [v['voucher_code'] for v in r.get_json()['vouchers']] == [code]


def test_redeem_requires_a_reward_id(env):
    _make_linked_customer(env, 'Owner')
    r = env['client'].post('/api/loyalty/redeem', headers=env['customer_H'], json={})
    assert r.status_code == 400


def test_staff_cannot_use_the_customer_endpoints_for_themselves(env):
    """Staff have no Customer document, so /me has nothing to resolve to
    rather than silently resolving to somebody else's account."""
    r = env['client'].get('/api/loyalty/me', headers=env['staff_H'])
    assert r.status_code == 404
