"""Tests for plate/conduction-sticker validation (utils/plate_utils.py) and
its wiring into the vehicle and appointment-booking routes.

Format/normalization tests are pure unit tests. The duplicate-check and
legacy-vehicle tests exercise the real Flask app against the project's
actual Firestore database (this project has no separate test DB — see the
rest of the codebase's ad-hoc verification scripts), and clean up every
document they create.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from utils.plate_utils import validate_plate, normalize_plate


# ---------------------------------------------------------------------------
# Pure format / normalization tests
# ---------------------------------------------------------------------------

def test_valid_new_car_plate():
    ok, normalized, err = validate_plate('NBC1234', 'plate')
    assert ok is True
    assert normalized == 'NBC1234'
    assert err is None


def test_valid_old_car_plate():
    ok, normalized, err = validate_plate('ABC123', 'plate')
    assert ok is True
    assert normalized == 'ABC123'
    assert err is None


def test_valid_conduction_sticker():
    ok, normalized, err = validate_plate('NC1234', 'conduction')
    assert ok is True
    assert normalized == 'NC1234'
    assert err is None


@pytest.mark.parametrize('raw', ['nbc-1234', 'NBC 1234', 'nbc1234', 'Nbc-1234'])
def test_lowercase_and_spaced_input_normalizes_identically(raw):
    ok, normalized, err = validate_plate(raw, 'plate')
    assert ok is True
    assert normalized == 'NBC1234'


def test_wrong_length_rejected():
    # 4 chars: too short even for the permissive 5-7 char motorcycle rule,
    # and doesn't match the 3-letter+3/4-digit rules either.
    ok, normalized, err = validate_plate('AB12', 'plate')
    assert ok is False
    assert normalized is None
    assert 'Invalid plate number format' in err


def test_letters_in_digit_positions_rejected():
    # Conduction has only ONE accepted rule (2 letters + 4 digits, no
    # permissive fallback like 'plate' has), so it's the format where
    # "letters where digits belong" is unambiguously rejected.
    ok, normalized, err = validate_plate('NCABCD', 'conduction')
    assert ok is False
    assert normalized is None


def test_conduction_sticker_wrong_format_rejected_with_expected_message():
    ok, normalized, err = validate_plate('NBC1234', 'conduction')
    assert ok is False
    assert normalized is None
    assert '2 letters followed by 4 digits' in err
    assert 'NC 1234' in err


def test_missing_value_rejected():
    ok, normalized, err = validate_plate('', 'plate')
    assert ok is False
    assert err == 'Plate number or conduction sticker is required.'

    ok, normalized, err = validate_plate(None, 'conduction')
    assert ok is False
    assert err == 'Plate number or conduction sticker is required.'


def test_normalize_plate_strips_spaces_and_hyphens():
    assert normalize_plate('nbc-1234') == 'NBC1234'
    assert normalize_plate('NBC 1234') == 'NBC1234'
    assert normalize_plate('N B C-1-2-3-4') == 'NBC1234'


# ---------------------------------------------------------------------------
# Integration tests against the real Flask app + Firestore
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def app_client():
    import firebase_auth_utils

    current_uid = {'uid': None}
    firebase_auth_utils.verify_id_token = lambda token: {'uid': current_uid['uid']}

    from app import create_app
    app = create_app()
    client = app.test_client()

    def as_role(uid):
        current_uid['uid'] = uid

    yield client, as_role


STAFF_UID = 'tceCQCKPsieYi2JOiTX71MfosbI2'
CUSTOMER_UID = 'Uq8d6v2rFjTIFtHiOnmIK8dEoQr2'  # customer id '6' in Firestore


def test_duplicate_plate_under_same_customer_rejected(app_client):
    client, as_role = app_client
    as_role(STAFF_UID)
    H = {'Authorization': 'Bearer fake'}
    created_ids = []
    try:
        r1 = client.post('/api/appointments/vehicles', headers=H, json={
            'customer_id': '6', 'make': 'Toyota', 'model': 'Test1',
            'identifier_type': 'plate_number', 'plate_or_sticker_no': 'PYT1234',
        })
        assert r1.status_code == 201
        created_ids.append(r1.get_json()['vehicle']['id'])

        # Same customer, same normalized plate (different casing/spacing) -> rejected
        r2 = client.post('/api/appointments/vehicles', headers=H, json={
            'customer_id': '6', 'make': 'Toyota', 'model': 'Test2',
            'identifier_type': 'plate_number', 'plate_or_sticker_no': 'pyt-1234',
        })
        assert r2.status_code == 400
        assert 'already registered' in r2.get_json()['error']
    finally:
        from firebase_config import init_firebase, get_db
        init_firebase()
        db = get_db()
        for vid in created_ids:
            db.collection('vehicles').document(vid).delete()


def test_legacy_vehicle_without_plate_blocks_booking_until_provided(app_client):
    """A vehicle with no plate_or_sticker_no on file (legacy data) must not
    crash the booking flow — it should be rejected with a clear message
    until the customer supplies one, exactly like a brand-new booking."""
    client, as_role = app_client
    H = {'Authorization': 'Bearer fake'}
    created_ids = {'vehicle': None, 'appointment': None}
    try:
        as_role(STAFF_UID)
        r = client.post('/api/appointments/vehicles', headers=H, json={
            'customer_id': '6', 'make': 'Toyota', 'model': 'LegacyNoPlate',
            # no identifier_type/plate_or_sticker_no at all — simulates a
            # pre-existing Firestore document created before this field existed
        })
        assert r.status_code == 201
        vehicle_id = r.get_json()['vehicle']['id']
        created_ids['vehicle'] = vehicle_id
        assert r.get_json()['vehicle']['plate_or_sticker_no'] is None

        as_role(CUSTOMER_UID)
        svcs = client.get('/api/appointments/services', headers=H).get_json()
        svc = next(s for s in svcs if s.get('is_active', True))

        # No plate typed at booking time -> must be rejected, not crash
        r2 = client.post('/api/appointments', headers=H, json={
            'vehicle_id': vehicle_id, 'preferred_date': '2026-08-01',
            'preferred_time': '09:00', 'service_ids': [svc['id']],
        })
        assert r2.status_code == 400
        assert 'required' in r2.get_json()['error'].lower()

        # Supplying one at booking time completes it successfully
        r3 = client.post('/api/appointments', headers=H, json={
            'vehicle_id': vehicle_id, 'plate_number': 'new-9999',
            'preferred_date': '2026-08-01', 'preferred_time': '09:00',
            'service_ids': [svc['id']],
        })
        assert r3.status_code == 201
        created_ids['appointment'] = r3.get_json()['appointment']['id']
        assert r3.get_json()['appointment']['plate_number'] == 'NEW9999'
    finally:
        from firebase_config import init_firebase, get_db
        init_firebase()
        db = get_db()
        if created_ids['appointment']:
            db.collection('appointments').document(created_ids['appointment']).delete()
        if created_ids['vehicle']:
            db.collection('vehicles').document(created_ids['vehicle']).delete()
