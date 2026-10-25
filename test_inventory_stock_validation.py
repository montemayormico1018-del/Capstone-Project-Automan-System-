"""Inventory create/update must reject a negative stock (and price/cost/
reorder_level) instead of silently saving it -- adjust_stock() already
guarded against this via InventoryMovement.record_usage/record_receipt;
create/update let it straight through until now.

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

    uid = f'stockval-staff-{uuid.uuid4().hex[:8]}'
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


def test_create_rejects_negative_stock(api):
    r = api['client'].post('/api/inventory', headers=api['H'], json={
        'name': 'Bad Item', 'price': 100, 'stock': -5,
    })
    assert r.status_code == 400
    assert 'negative' in r.get_json()['error'].lower()


def test_create_rejects_negative_price(api):
    r = api['client'].post('/api/inventory', headers=api['H'], json={
        'name': 'Bad Item', 'price': -1,
    })
    assert r.status_code == 400


def test_create_rejects_negative_reorder_level(api):
    r = api['client'].post('/api/inventory', headers=api['H'], json={
        'name': 'Bad Item', 'price': 100, 'reorder_level': -1,
    })
    assert r.status_code == 400


def test_create_accepts_zero_stock(api):
    r = api['client'].post('/api/inventory', headers=api['H'], json={
        'name': 'Zero Stock Item', 'price': 100, 'stock': 0,
    })
    assert r.status_code == 201


def test_create_allows_omitted_cost(api):
    r = api['client'].post('/api/inventory', headers=api['H'], json={
        'name': 'No Cost Item', 'price': 100,
    })
    assert r.status_code == 201
    assert r.get_json()['item']['cost'] is None


def _create_item(api, **overrides):
    payload = {'name': 'Editable Item', 'price': 100, 'stock': 10}
    payload.update(overrides)
    r = api['client'].post('/api/inventory', headers=api['H'], json=payload)
    assert r.status_code == 201
    return r.get_json()['item']['id']


def test_update_rejects_negative_stock(api):
    item_id = _create_item(api)
    r = api['client'].put(f'/api/inventory/{item_id}', headers=api['H'], json={'stock': -1})
    assert r.status_code == 400
    assert 'negative' in r.get_json()['error'].lower()

    # And the item's real stock was untouched.
    from models.inventory import InventoryItem
    item = InventoryItem.get(item_id)
    assert item.stock == 10


def test_update_rejects_non_numeric_stock(api):
    item_id = _create_item(api)
    r = api['client'].put(f'/api/inventory/{item_id}', headers=api['H'], json={'stock': 'a lot'})
    assert r.status_code == 400


def test_update_accepts_zero_stock(api):
    item_id = _create_item(api)
    r = api['client'].put(f'/api/inventory/{item_id}', headers=api['H'], json={'stock': 0})
    assert r.status_code == 200
    assert r.get_json()['item']['stock'] == 0


def test_update_can_still_clear_cost_to_null(api):
    item_id = _create_item(api, cost=50)
    r = api['client'].put(f'/api/inventory/{item_id}', headers=api['H'], json={'cost': None})
    assert r.status_code == 200
    assert r.get_json()['item']['cost'] is None
