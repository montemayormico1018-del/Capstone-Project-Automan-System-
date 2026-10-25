"""XSS-escaping regression guards for the loyalty pages.

Same two-part approach as test_xss_escaping.py, applied to the two templates
this feature added:

  1. an API round-trip proving a value containing ``<img src=x onerror=...>``
     really does reach the JSON response byte-for-byte, so the browser-side
     escape is the only defense; and
  2. a source check confirming the inline JS wraps that field in the shared
     ``escapeHtml()`` helper before any ``.innerHTML =``.

Reward names, descriptions, image URLs and notes are all admin-supplied free
text, and every one of them lands in an innerHTML template literal on both
the customer dashboard and the admin Loyalty page -- so this is a real
attack surface, not a theoretical one.

FIRESTORE_MOCK=1 only.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)

PAYLOAD = '<img src=x onerror=alert(1)>'
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _template_source(relative_path):
    with open(os.path.join(REPO_ROOT, 'templates', relative_path), encoding='utf-8') as f:
        return f.read()


def assert_escaped_in_template(relative_path, *js_exprs):
    """Regression guard: escapeHtml(<expr>) must appear verbatim in the
    template's inline JS for every expr given.

    Allows a trailing ``|| default`` inside the call -- templates here write
    ``escapeHtml(name || 'Unnamed')`` for fields that may be empty, which is
    still fully escaped, just not literally ``escapeHtml(name)``.
    """
    import re
    src = _template_source(relative_path)
    for expr in js_exprs:
        pattern = r'escapeHtml\(\s*' + re.escape(expr) + r'\s*(?:\|\|[^()]*)?\)'
        assert re.search(pattern, src), (
            f'{relative_path} no longer wraps {expr} in escapeHtml() -- '
            'a raw interpolation here would be an XSS regression.'
        )


@pytest.fixture
def api():
    import firebase_auth_utils
    from models.user import User

    uids = {}
    for role in ('admin', 'staff', 'customer'):
        uid = f'loyaltyxss-{role}-{uuid.uuid4().hex[:8]}'
        User(
            id=uid, username=f'{role}_{uid}', full_name=f'{role.title()} Tester',
            email=f'{uid}@example.test', role=role, contact_number='09123456789',
            is_active=True, is_verified=True,
        ).save()
        uids[role] = uid

    firebase_auth_utils.verify_id_token = lambda token: {'uid': uids.get(token, 'unknown')}

    from app import create_app
    app = create_app()
    yield {
        'client': app.test_client(),
        'H': {'Authorization': 'Bearer staff'},
        'admin_H': {'Authorization': 'Bearer admin'},
        'customer_H': {'Authorization': 'Bearer customer'},
        'uids': uids,
    }


@pytest.fixture(autouse=True)
def default_settings(api):
    from utils import loyalty_service as svc
    with api['client'].application.app_context():
        svc.save_settings(dict(svc.DEFAULT_SETTINGS))


# ---------------------------------------------------------------------------
# reward name / description -- admin-created, rendered to staff AND customer
# ---------------------------------------------------------------------------

def test_reward_name_and_description_payload_escaped(api):
    client = api['client']
    r = client.post('/api/loyalty/admin/rewards', headers=api['admin_H'], json={
        'name': PAYLOAD,
        'description': PAYLOAD,
        'points_cost': 100,
        'reward_type': 'service',
        'value': 'Free service',
    })
    assert r.status_code == 201, r.get_json()
    reward_id = r.get_json()['reward']['id']

    # The backend stores and returns it verbatim -- escaping has to happen
    # client-side.
    r = client.get('/api/loyalty/admin/rewards', headers=api['admin_H'])
    stored = next(x for x in r.get_json()['rewards'] if x['id'] == reward_id)
    assert stored['name'] == PAYLOAD
    assert stored['description'] == PAYLOAD

    # Both surfaces that render a reward card.
    assert_escaped_in_template('loyalty.html', 'r.name')
    assert_escaped_in_template('loyalty.html', 'r.description')
    assert_escaped_in_template('customer_dashboard.html', 'r.name')
    assert_escaped_in_template('customer_dashboard.html', 'r.description')


def test_reward_image_url_payload_escaped(api):
    client = api['client']
    r = client.post('/api/loyalty/admin/rewards', headers=api['admin_H'], json={
        'name': 'Image payload',
        'points_cost': 100,
        'reward_type': 'service',
        'value': 'Free service',
        'image_url': f'x" onerror="alert(1)',
    })
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['reward']['image_url'] == 'x" onerror="alert(1)'

    # image_url is interpolated straight into a src="" attribute, so the
    # quote-escaping half of escapeHtml() is what protects it.
    assert_escaped_in_template('loyalty.html', 'r.image_url')
    assert_escaped_in_template('customer_dashboard.html', 'r.image_url')


# ---------------------------------------------------------------------------
# voucher code + customer-facing transaction notes
# ---------------------------------------------------------------------------

def test_customer_loyalty_payload_escaped(api):
    from models.customer import Customer
    from models.user import User
    from utils import loyalty_service as svc

    email = f'loyaltyxss-{uuid.uuid4().hex[:8]}@example.test'
    customer = Customer(name=PAYLOAD, phone='09171230012', email=email)
    customer.save()
    # The customer-facing endpoints resolve the customer by matching the
    # logged-in account's email, so point the account at this record.
    User(id=api['uids']['customer'], username='xss-customer', full_name=PAYLOAD,
         email=email, role='customer', contact_number='09171230012',
         is_active=True, is_verified=True).save()

    with api['client'].application.app_context():
        # A note carrying the payload, as a manual adjustment would.
        svc.adjust_points(customer.id, 100, PAYLOAD)
        reward = svc.Reward(name=PAYLOAD, points_cost=10, reward_type='discount',
                            value=5, is_active=True)
        reward.save()
        redemption = svc.redeem_reward(customer.id, reward.id)

    r = api['client'].get('/api/loyalty/me/transactions', headers=api['customer_H'])
    assert r.status_code == 200
    assert any(t['note'] == PAYLOAD for t in r.get_json()['transactions'])

    r = api['client'].get('/api/loyalty/me/vouchers', headers=api['customer_H'])
    assert r.status_code == 200
    assert r.get_json()['vouchers'][0]['voucher_code'] == redemption.voucher_code

    # Transaction notes and reward names on the customer's own dashboard.
    assert_escaped_in_template('customer_dashboard.html', 't.note')
    assert_escaped_in_template('customer_dashboard.html', 'v.reward_name')
    assert_escaped_in_template('customer_dashboard.html', 'v.voucher_code')


# ---------------------------------------------------------------------------
# admin page: customer search results and the voucher validator
# ---------------------------------------------------------------------------

def test_admin_loyalty_page_escapes_customer_and_voucher_fields(api):
    from models.loyalty import RewardRedemption

    r = api['client'].post('/api/appointments/customers', headers=api['admin_H'], json={
        'name': PAYLOAD, 'email': f'loyaltyxss-{uuid.uuid4().hex[:8]}@example.test',
        'phone': '09171230013', 'address': '',
    })
    assert r.status_code == 201, r.get_json()

    r = api['client'].get('/api/appointments/customers', headers=api['admin_H'])
    assert any(c['name'] == PAYLOAD for c in r.get_json())

    # The admin page renders search results, customer names, and the
    # voucher's reward name / customer name.
    assert_escaped_in_template('loyalty.html', 'c.name')
    assert_escaped_in_template('loyalty.html', 'c.email')
    assert_escaped_in_template('loyalty.html', 'customer.name')
    assert_escaped_in_template('loyalty.html', 'data.customer_name')
    assert_escaped_in_template('loyalty.html', 'data.reward_name')


def test_admin_adjust_reason_payload_escaped(api):
    """The adjustment reason is free text an admin types, and it's stored on
    the ledger and rendered back on the customer dashboard."""
    from models.customer import Customer

    customer = Customer(name='Reason Target', phone='09171230014',
                        email=f'loyaltyxss-{uuid.uuid4().hex[:8]}@example.test')
    customer.save()

    r = api['client'].post('/api/loyalty/admin/adjust', headers=api['admin_H'], json={
        'customer_id': customer.id, 'points': 50, 'reason': PAYLOAD,
    })
    assert r.status_code == 200, r.get_json()

    r = api['client'].get(f'/api/loyalty/admin/customers/{customer.id}', headers=api['admin_H'])
    assert r.status_code == 200
    # The reason lands in the audit description too.
    from models.audit_log import AuditLog
    entries = [e for e in AuditLog.query.all()
               if e.entity_type == 'loyalty_account' and e.entity_id == str(customer.id)]
    assert PAYLOAD in entries[0].description
    # ...which the admin page never interpolates raw, and the ledger note
    # (shown to the customer) is escaped on the dashboard.
    assert_escaped_in_template('customer_dashboard.html', 't.note')
