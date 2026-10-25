"""Round 2 R1: XSS-escaping regression tests.

Every dashboard in this app is a static Jinja shell -- the actual customer /
vehicle / inventory / package / etc. data is fetched as JSON and rendered
into the DOM by inline vanilla-JS template literals (`.innerHTML = ...`),
not by Jinja. That means there is no server-rendered HTML response to assert
an escaped payload against, and this test environment has no browser/JS
runtime to execute the client-side code in.

So each test below combines two checks that together prove the fix holds:

  1. An API round-trip proving a value containing
     ``<img src=x onerror=alert(1)>`` really does reach the JSON response
     byte-for-byte -- i.e. the backend does NOT sanitize it, so the
     browser-side escape in the template's JS is the only and therefore
     necessary defense.
  2. A source check on the template(s) that render that exact field,
     confirming the inline JS wraps it in the shared ``escapeHtml()``
     helper before any ``.innerHTML =`` / ``insertAdjacentHTML`` use. This
     is a regression guard: if a future edit removes the escapeHtml()
     wrapper, this fails.

Entirely FIRESTORE_MOCK=1 -- never touches the real Firebase project. Users
are seeded directly into the in-memory mock store (unique uuid-suffixed ids
per test), so this never collides with the app's own demo-account seeding.
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
    template's inline JS for every expr given."""
    src = _template_source(relative_path)
    for expr in js_exprs:
        assert f'escapeHtml({expr})' in src, (
            f'{relative_path} no longer wraps {expr} in escapeHtml() -- '
            'a raw interpolation here would be an XSS regression.'
        )


@pytest.fixture
def api():
    """Boots the app under FIRESTORE_MOCK and returns a client plus a helper
    to switch the authenticated identity mid-test, mirroring the fake-auth
    pattern already used in test_booking_capacity.py (there against a real,
    pre-seeded Firestore project; here fully self-contained under mock)."""
    import firebase_auth_utils
    from models.user import User

    uids = {}
    for role in ('admin', 'staff', 'customer'):
        uid = f'xsstest-{role}-{uuid.uuid4().hex[:8]}'
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


# ---------------------------------------------------------------------------
# inventory.html + forecast.html (both render InventoryItem.name/.sku/.supplier)
# ---------------------------------------------------------------------------

def test_inventory_item_name_payload_escaped(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('staff')

    r = client.post('/api/inventory', headers=H, json={
        'name': PAYLOAD, 'sku': f'XSS-{uuid.uuid4().hex[:6]}',
        'category': 'Parts', 'price': 10, 'stock': 1,
    })
    assert r.status_code == 201, r.get_json()
    item_id = r.get_json()['item']['id']

    r = client.get(f'/api/inventory/{item_id}', headers=H)
    assert r.status_code == 200
    # The backend stores/returns the payload verbatim -- proving escaping
    # must happen client-side, which is exactly what the assertions below verify.
    assert r.get_json()['name'] == PAYLOAD

    assert_escaped_in_template('inventory.html', 'item.name')
    assert_escaped_in_template('forecast.html', 'item.name')
    assert_escaped_in_template('job_orders.html', 'i.name')


# ---------------------------------------------------------------------------
# users.html
# ---------------------------------------------------------------------------

def test_user_full_name_payload_escaped(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('admin')

    r = client.post('/api/users', headers=H, json={
        'role': 'staff', 'full_name': PAYLOAD,
        'email': f'{uuid.uuid4().hex[:8]}@example.test',
        'password': 'Test123!', 'contact_number': '09123456789',
    })
    assert r.status_code == 201, r.get_json()

    r = client.get('/api/users', headers=H)
    assert r.status_code == 200
    created = next(u for u in r.get_json() if u['full_name'] == PAYLOAD)
    assert created['full_name'] == PAYLOAD

    assert_escaped_in_template('users.html', 'u.full_name')


# ---------------------------------------------------------------------------
# customers.html
# ---------------------------------------------------------------------------

def test_customer_name_payload_escaped(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('staff')

    r = client.post('/api/appointments/customers', headers=H, json={
        'name': PAYLOAD, 'phone': '09123456789', 'email': 'cust@example.test',
    })
    assert r.status_code == 201, r.get_json()

    r = client.get('/api/appointments/customers', headers=H)
    assert r.status_code == 200
    created = next(c for c in r.get_json() if c['name'] == PAYLOAD)
    assert created['name'] == PAYLOAD

    assert_escaped_in_template('customers.html', 'c.name')
    # Two more sinks found while re-verifying this file: SweetAlert2's
    # `title`/`html` options render as raw HTML just like .innerHTML (only
    # its `text` option is safe/textContent-based) -- the customer-detail and
    # vehicle-list popups here interpolate customer.name/email/phone/address
    # and vehicle fields into exactly those options.
    assert_escaped_in_template(
        'customers.html', 'customer.name', "customer.email || '-'",
        "customer.phone || '-'", "customer.address || '-'", 'formatVehicleBadge(v)',
    )


# ---------------------------------------------------------------------------
# packages.html + customer_dashboard.html
# ---------------------------------------------------------------------------

def test_package_name_and_item_name_payload_escaped(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('staff')

    r = client.post('/api/packages', headers=H, json={
        'name': PAYLOAD,
        'items': [{'item_name': PAYLOAD}],
    })
    assert r.status_code == 201, r.get_json()

    r = client.get('/api/packages', headers=H)
    assert r.status_code == 200
    created = next(p for p in r.get_json() if p['name'] == PAYLOAD)
    assert created['name'] == PAYLOAD
    assert created['items'][0]['item_name'] == PAYLOAD

    assert_escaped_in_template('packages.html', 'p.name')
    assert_escaped_in_template('customer_dashboard.html', 'p.name', 'item.item_name')


# ---------------------------------------------------------------------------
# appointments.html (service dropdown) + packages.html (pricing modal, via
# Swal.fire's html: option -- another raw-HTML sink found while re-verifying
# this file, same as customers.html's title:/html: sinks above) +
# customer_dashboard.html (service cards).
# ---------------------------------------------------------------------------

def test_service_name_payload_escaped(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('staff')

    r = client.post('/api/appointments/services', headers=H, json={
        'name': PAYLOAD, 'price': 100,
    })
    assert r.status_code == 201, r.get_json()

    r = client.get('/api/appointments/services', headers=H)
    assert r.status_code == 200
    created = next(s for s in r.get_json() if s['name'] == PAYLOAD)
    assert created['name'] == PAYLOAD

    assert_escaped_in_template('appointments.html', 's.name')
    assert_escaped_in_template('packages.html', 's.name')
    assert_escaped_in_template('customer_dashboard.html', 's.name')


# ---------------------------------------------------------------------------
# customer_dashboard.html + dashboard.html + appointments.html: vehicle make.
# Customers are restricted to a fixed catalog of makes/models
# (_is_valid_catalog_make_model), so the free-text injection path only
# exists for admin/staff creating a vehicle on a customer's behalf -- which
# is exactly the path exercised here.
# ---------------------------------------------------------------------------

def test_vehicle_make_payload_escaped(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('staff')

    r = client.post('/api/appointments/customers', headers=H, json={
        'name': 'Vehicle Owner', 'phone': '09123456789',
    })
    assert r.status_code == 201, r.get_json()
    customer_id = r.get_json()['customer']['id']

    r = client.post('/api/appointments/vehicles', headers=H, json={
        'customer_id': customer_id, 'make': PAYLOAD, 'model': 'Test Model',
    })
    assert r.status_code == 201, r.get_json()

    r = client.get(f'/api/appointments/vehicles?customer_id={customer_id}', headers=H)
    assert r.status_code == 200
    data = r.get_json()
    vehicles = data if isinstance(data, list) else data.get('vehicles', [])
    created = next(v for v in vehicles if v['make'] == PAYLOAD)
    assert created['make'] == PAYLOAD

    assert_escaped_in_template('customer_dashboard.html', '`${v.make || \'\'} ${v.model || \'\'}`.trim()')
    assert_escaped_in_template('appointments.html', "apt.vehicle?.make || ''")
    assert_escaped_in_template('job_orders.html', "`${jo.vehicle.make || ''} ${jo.vehicle.model || ''}`.trim()")


# ---------------------------------------------------------------------------
# automan-nav.js's shared escapeHtml(): attribute-context safety.
# A value containing a bare double-quote must not be able to break out of an
# HTML attribute (e.g. title="...", alt="...", value="...") -- this is the
# exact gap the textContent->innerHTML round-trip implementation had before
# Round 2's rewrite to explicit character replacement.
# ---------------------------------------------------------------------------

def test_shared_escape_html_neutralizes_attribute_breakout():
    import re
    with open(os.path.join(REPO_ROOT, 'static', 'js', 'automan-nav.js'), encoding='utf-8') as f:
        js_src = f.read()

    # Extract the escapeHtml function body and eval it under a minimal JS-like
    # shim is overkill for a Python test -- instead, assert the specific
    # regex-replacement chain (the actual fix) is present and covers all 5
    # characters that matter in both tag-content and attribute-value position.
    match = re.search(r'window\.escapeHtml = function escapeHtml\(value\) \{(.*?)\n    \};', js_src, re.S)
    assert match, 'escapeHtml() implementation not found in automan-nav.js'
    body = match.group(1)
    for char, entity in [('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('"', '&quot;'), ("'", '&#39;')]:
        assert entity in body, f'escapeHtml() no longer escapes {char!r} -- attribute-breakout regression'
    # The old, insufficient implementation escaped via a textContent/innerHTML
    # round trip and never mentioned &quot;/&#39; at all -- so the assertions
    # above are sufficient to distinguish the fixed version from the bug.


@pytest.mark.parametrize('template', [
    'customer_dashboard.html',
    'login.html',
    'register.html',
    'forgot_password.html',
    'reset_password.html',
])
def test_local_escape_html_copies_match_shared_fix(template):
    """These 5 templates don't load automan-nav.js, so each carries its own
    copy of escapeHtml(). All 5 originally shipped with the old, insufficient
    textContent->innerHTML round-trip version (escapes only &/</>, not "/'),
    which Round 2 fixed in automan-nav.js's copy but missed in these local
    ones -- discovered and fixed while re-verifying customer_dashboard.html.
    """
    src = _template_source(template)
    assert 'function escapeHtml(value)' in src
    assert '&quot;' in src and '&#39;' in src, (
        f'{template}\'s local escapeHtml() copy does not escape " or \' -- '
        'it is still the old textContent-round-trip version, which stays '
        'breakable out of any HTML attribute (alt=, title=, value=, etc.) '
        'with a bare double-quote.'
    )
    assert 'document.createElement(\'div\')' not in src.split('function escapeHtml(value)', 1)[1].split('\n\n', 1)[0], (
        f'{template} still uses the old textContent/innerHTML round-trip '
        'implementation of escapeHtml() instead of explicit character replacement.'
    )
