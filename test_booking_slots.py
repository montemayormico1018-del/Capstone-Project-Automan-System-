"""Round 3 R9: booking-slot capacity visibility in the customer/staff
booking UIs.

Inspection findings (see the R3 summary for the full writeup): the customer
booking page (customer_dashboard.html) already called
GET /api/appointments/availability on date selection and disabled full slots
before this round -- the only real gaps were (a) it didn't show a remaining
count for open slots, and (b) the admin/staff booking page (appointments.html)
had no slot-capacity wiring at all (a plain static <select> of times). R9
also asked for a slots endpoint returning booked/capacity/is_full; rather
than add a second endpoint that would just re-run the exact same query
against the exact same BOOKING_TIME_SLOTS/MAX_BOOKINGS_PER_SLOT source of
truth, this added an `is_full` field to the existing /availability endpoint
(kept `available` for the pre-existing customer_dashboard.html code that
already depended on it).

FIRESTORE_MOCK=1 only -- never touches real Firebase.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
TEST_DATE = '2026-11-02'  # a Monday -- shop is open


def _template_source(relative_path):
    with open(os.path.join(REPO_ROOT, 'templates', relative_path), encoding='utf-8') as f:
        return f.read()


@pytest.fixture
def api():
    import firebase_auth_utils
    from models.user import User

    uids = {}
    for role in ('admin', 'staff', 'customer'):
        uid = f'slottest-{role}-{uuid.uuid4().hex[:8]}'
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


def _book_slot(api, date, time, n):
    """Create n active appointments directly (bypassing create_appointment's
    own capacity check, which is what /availability's *count* must reflect,
    not re-derive) for the given date/time slot."""
    from models.appointment import Appointment
    for _ in range(n):
        Appointment(status='Pending', preferred_date=date, preferred_time=time).save()


def test_empty_slot_reports_zero_booked_and_not_full(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('customer')

    r = client.get(f'/api/appointments/availability?date={TEST_DATE}', headers=H)
    assert r.status_code == 200
    data = r.get_json()
    slot = next(s for s in data['slots'] if s['time'] == '08:00')
    assert slot['booked'] == 0
    assert slot['is_full'] is False
    assert slot['available'] is True
    assert slot['remaining'] == data['max_bookings_per_slot']


def test_partially_booked_slot_reports_correct_remaining(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('customer')

    _book_slot(api, TEST_DATE, '09:00:00', 1)

    r = client.get(f'/api/appointments/availability?date={TEST_DATE}', headers=H)
    data = r.get_json()
    slot = next(s for s in data['slots'] if s['time'] == '09:00')
    assert slot['booked'] == 1
    assert slot['is_full'] is False
    assert slot['available'] is True
    assert slot['remaining'] == data['max_bookings_per_slot'] - 1


def test_full_slot_reports_is_full_true(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('customer')

    max_per_slot = client.application.config.get('MAX_BOOKINGS_PER_SLOT', 3)
    _book_slot(api, TEST_DATE, '10:00:00', max_per_slot)

    r = client.get(f'/api/appointments/availability?date={TEST_DATE}', headers=H)
    data = r.get_json()
    slot = next(s for s in data['slots'] if s['time'] == '10:00')
    assert slot['booked'] == max_per_slot
    assert slot['is_full'] is True
    assert slot['available'] is False
    assert slot['remaining'] == 0


def test_closed_day_marks_every_slot_full(api):
    client, H, as_role = api['client'], api['H'], api['as_role']
    as_role('customer')

    r = client.get('/api/appointments/availability?date=2026-11-08', headers=H)  # a Sunday
    assert r.status_code == 200
    data = r.get_json()
    assert data['closed'] is True
    assert all(s['is_full'] is True and s['available'] is False for s in data['slots'])


def test_past_date_is_unavailable_to_customer_but_visible_to_staff(api):
    today = datetime.now(ZoneInfo('Asia/Manila')).date()
    date_value = today - timedelta(days=1)
    while date_value.weekday() == 6:
        date_value -= timedelta(days=1)
    date = date_value.isoformat()

    api['as_role']('customer')
    customer_data = api['client'].get(
        f'/api/appointments/availability?date={date}', headers=api['H']
    ).get_json()

    api['as_role']('staff')
    staff_data = api['client'].get(
        f'/api/appointments/availability?date={date}', headers=api['H']
    ).get_json()

    assert all(slot['is_past'] for slot in customer_data['slots'])
    assert all(slot['available'] is False for slot in customer_data['slots'])
    assert all(slot['is_past'] for slot in staff_data['slots'])
    assert all(slot['available'] for slot in staff_data['slots'])


def test_same_day_elapsed_slot_is_unavailable_to_customer(api, monkeypatch):
    import routes.appointments as appointments

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 2, 9, 30, tzinfo=tz)

    monkeypatch.setattr(appointments, 'datetime', FixedDateTime)
    api['as_role']('customer')

    data = api['client'].get(
        '/api/appointments/availability?date=2026-11-02', headers=api['H']
    ).get_json()
    slots = {slot['time']: slot for slot in data['slots']}

    assert slots['09:00']['is_past'] is True
    assert slots['09:00']['available'] is False
    assert slots['13:00']['is_past'] is False
    assert slots['13:00']['available'] is True


def test_customer_and_staff_see_identical_slot_data(api):
    client, H = api['client'], api['H']
    date = '2026-11-03'
    _book_slot(api, date, '11:00:00', 1)

    api['as_role']('customer')
    customer_data = client.get(f'/api/appointments/availability?date={date}', headers=H).get_json()

    api['as_role']('staff')
    staff_data = client.get(f'/api/appointments/availability?date={date}', headers=H).get_json()

    assert customer_data['slots'] == staff_data['slots']


# ---------------------------------------------------------------------------
# Source-level regression guards: the booking UIs must actually act on
# is_full the way R9 specifies -- customer page disables/grays out full
# slots, staff page shows the same counts but keeps options selectable with
# a staff-override label.
# ---------------------------------------------------------------------------

def test_customer_booking_ui_disables_full_and_past_slots():
    src = _template_source('customer_dashboard.html')
    start = src.index('async function loadSlotAvailability(date) {')
    end = src.index('\n        async function loadAppointments', start)
    body = src[start:end]

    assert 'info.is_past' in body
    assert 'if (info.available)' in body
    assert 'btn.disabled = true' in body


def test_staff_booking_ui_shows_counts_without_disabling():
    src = _template_source('appointments.html')
    assert 'loadStaffSlotAvailability' in src
    assert 'is_full' in src
    assert 'staff override' in src

    start = src.index('async function loadStaffSlotAvailability(date) {')
    end = src.index('\n        async function createAppointment', start)
    body = src[start:end]
    assert '.disabled = true' not in body
    assert '.disabled=true' not in body
