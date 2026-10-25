"""GET /api/inventory/critical-count -- the sidebar's blinking-dot data
source. Reuses the same "critical = stock <= 25% of reorder_level" ratio
InventoryAnalytics.calculate_reorder_point() falls back to, without its
expensive per-item movement-history aggregation.

The mock Firestore store is shared across this whole pytest session (other
test files create InventoryItem records too), so every test here reads the
endpoint's count *before* adding its own item(s) and asserts on the delta,
rather than an absolute count.

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

    uid = f'stockdot-staff-{uuid.uuid4().hex[:8]}'
    User(
        id=uid, username=f'staff_{uid}', full_name='Staff Tester',
        email=f'{uid}@example.test', role='staff', contact_number='09123456789',
        is_active=True, is_verified=True,
    ).save()
    firebase_auth_utils.verify_id_token = lambda token: {'uid': uid}

    from app import create_app
    app = create_app()
    client = app.test_client()
    H = {'Authorization': 'Bearer fake'}

    def critical_count():
        return client.get('/api/inventory/critical-count', headers=H).get_json()['critical_count']

    return {'client': client, 'H': H, 'critical_count': critical_count}


def _make_item(stock, reorder_level):
    from models.enhanced_models import InventoryItem
    item = InventoryItem(
        name=f'Test Part {uuid.uuid4().hex[:6]}', sku=f'SKU-{uuid.uuid4().hex[:6]}',
        price=100, stock=stock, reorder_level=reorder_level,
    )
    item.save()
    return item


def test_new_item_well_above_critical_does_not_increase_the_count(api):
    before = api['critical_count']()
    _make_item(stock=20, reorder_level=10)  # 200% of reorder_level, nowhere near critical
    after = api['critical_count']()
    assert after == before


def test_item_at_25_percent_of_reorder_level_increases_the_count(api):
    before = api['critical_count']()
    _make_item(stock=2, reorder_level=10)  # 20% of reorder_level -> critical
    after = api['critical_count']()
    assert after == before + 1


def test_item_above_25_percent_does_not_count_as_critical(api):
    before = api['critical_count']()
    _make_item(stock=8, reorder_level=10)  # 80% of reorder_level -> not critical
    after = api['critical_count']()
    assert after == before


def test_exactly_25_percent_counts_as_critical_boundary(api):
    before = api['critical_count']()
    _make_item(stock=5, reorder_level=20)  # exactly 25% -> critical (<=, not <)
    after = api['critical_count']()
    assert after == before + 1
