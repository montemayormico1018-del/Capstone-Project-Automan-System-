"""Tests for per-slot booking capacity (MAX_BOOKINGS_PER_SLOT) in
routes/appointments.py: create_appointment()'s capacity check + race-
condition guard, and the /api/appointments/availability endpoint.

Runs against the project's real Firestore database (no separate test DB —
see test_plate_validation.py for the same pattern) and cleans up every
document it creates. Uses a fixed test date far in the future so it never
collides with real bookings.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

TEST_DATE = '2026-09-20'
TEST_TIME = '14:00'  # distinct from test_plate_validation's slots

CUSTOMER_UID = 'Uq8d6v2rFjTIFtHiOnmIK8dEoQr2'  # real customer, Firestore id '6'
STAFF_UID = 'tceCQCKPsieYi2JOiTX71MfosbI2'


@pytest.fixture
def booking_env():
    import firebase_auth_utils

    current = {'uid': STAFF_UID}
    firebase_auth_utils.verify_id_token = lambda token: {'uid': current['uid']}

    from app import create_app
    app = create_app()
    client = app.test_client()
    H = {'Authorization': 'Bearer fake'}

    def as_role(uid):
        current['uid'] = uid

    created = {'vehicles': [], 'appointments': []}

    def make_vehicle(idx):
        as_role(CUSTOMER_UID)
        r = client.post('/api/appointments/vehicles', headers=H, json={
            'make': 'Toyota', 'model': f'CapPytest{idx}',
            'identifier_type': 'plate_number', 'plate_or_sticker_no': f'CPT{1000 + idx}',
        })
        assert r.status_code == 201, r.get_json()
        vid = r.get_json()['vehicle']['id']
        created['vehicles'].append(vid)
        return vid

    def book(vehicle_id, uid=CUSTOMER_UID, date=TEST_DATE, time=TEST_TIME, **extra):
        as_role(uid)
        payload = {'vehicle_id': vehicle_id, 'preferred_date': date, 'preferred_time': time,
                   'service_ids': [svc_id(client, H)]}
        payload.update(extra)
        r = client.post('/api/appointments', headers=H, json=payload)
        if r.status_code == 201:
            created['appointments'].append(r.get_json()['appointment']['id'])
        return r

    def get_service_id():
        as_role(STAFF_UID)
        svcs = client.get('/api/appointments/services', headers=H).get_json()
        return next(s for s in svcs if s.get('is_active', True))['id']

    yield {
        'client': client, 'H': H, 'as_role': as_role,
        'make_vehicle': make_vehicle, 'book': book, 'service_id': get_service_id(),
    }

    from firebase_config import init_firebase, get_db
    init_firebase()
    db = get_db()
    for aid in created['appointments']:
        db.collection('appointments').document(aid).delete()
    for vid in created['vehicles']:
        db.collection('vehicles').document(vid).delete()


def svc_id(client, H):
    svcs = client.get('/api/appointments/services', headers=H).get_json()
    return next(s for s in svcs if s.get('is_active', True))['id']


def test_three_bookings_succeed_fourth_gets_409(booking_env):
    make_vehicle, book = booking_env['make_vehicle'], booking_env['book']

    responses = []
    for i in range(4):
        vid = make_vehicle(i)
        responses.append(book(vid))

    assert [r.status_code for r in responses[:3]] == [201, 201, 201]
    assert responses[3].status_code == 409
    assert 'fully booked' in responses[3].get_json()['error'].lower()


def test_cancelled_appointment_frees_the_slot(booking_env):
    client, H, as_role = booking_env['client'], booking_env['H'], booking_env['as_role']
    make_vehicle, book = booking_env['make_vehicle'], booking_env['book']

    booked_ids = []
    for i in range(3):
        vid = make_vehicle(i)
        r = book(vid)
        assert r.status_code == 201
        booked_ids.append(r.get_json()['appointment']['id'])

    # Slot is now full — a 4th booking is rejected.
    vid4 = make_vehicle(4)
    r = book(vid4)
    assert r.status_code == 409

    # Cancel one of the three -> slot has room again.
    as_role(STAFF_UID)
    r = client.put(f'/api/appointments/{booked_ids[0]}', headers=H, json={'status': 'Cancelled'})
    assert r.status_code == 200

    r = book(vid4)
    assert r.status_code == 201


def test_availability_reports_unavailable_only_at_capacity(booking_env):
    client, H, as_role = booking_env['client'], booking_env['H'], booking_env['as_role']
    make_vehicle, book = booking_env['make_vehicle'], booking_env['book']

    for i in range(2):
        vid = make_vehicle(i)
        r = book(vid)
        assert r.status_code == 201

    as_role(STAFF_UID)
    r = client.get(f'/api/appointments/availability?date={TEST_DATE}', headers=H)
    assert r.status_code == 200
    data = r.get_json()
    slot = next(s for s in data['slots'] if s['time'] == TEST_TIME)
    assert slot['booked'] == 2
    assert slot['available'] is True  # 2 of 3 (default cap) — not yet full

    vid3 = make_vehicle(2)
    r = book(vid3)
    assert r.status_code == 201

    r = client.get(f'/api/appointments/availability?date={TEST_DATE}', headers=H)
    slot = next(s for s in r.get_json()['slots'] if s['time'] == TEST_TIME)
    assert slot['booked'] == 3
    assert slot['available'] is False  # now at capacity

    other_slot = next(s for s in r.get_json()['slots'] if s['time'] != TEST_TIME)
    assert other_slot['available'] is True  # untouched slots stay available


def test_staff_role_can_exceed_the_limit(booking_env):
    make_vehicle, book = booking_env['make_vehicle'], booking_env['book']

    results = []
    for i in range(5):  # more than MAX_BOOKINGS_PER_SLOT (default 3)
        vid = make_vehicle(i)
        r = book(vid, uid=STAFF_UID, customer_id='6')
        results.append(r.status_code)

    assert results == [201, 201, 201, 201, 201]
