"""Mobile app report: submitting an appointment "takes too long". Root
cause: generate_appointment_number() in routes/appointments.py called
Appointment.query.all() -- a full scan of every appointment ever created,
just to count today's -- on every single booking. Scoped to a
created_at >= start-of-today range filter instead (same pattern already
used in routes/reports.py). This verifies the sequence numbering still
works correctly under the new scoped query: old appointments (from other
days) must not be counted, and same-day numbering must still increment
correctly and never collide.

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

from routes.appointments import generate_appointment_number


@pytest.fixture
def customer():
    from models.customer import Customer
    c = Customer(name='Numbering Customer', phone='09171234567', email=f'{uuid.uuid4().hex[:8]}@example.test')
    c.save()
    return c


def test_first_appointment_today_gets_sequence_1():
    number = generate_appointment_number()
    today = datetime.utcnow().strftime('%Y%m%d')
    assert number == f'APT-{today}-0001'


def test_old_appointments_from_other_days_are_not_counted(customer):
    from models.appointment import Appointment

    old = Appointment(customer_id=customer.id, preferred_date='2020-01-01', preferred_time='09:00:00',
                       appointment_number='APT-20200101-0001',
                       created_at=datetime(2020, 1, 1))
    old.save()

    number = generate_appointment_number()
    today = datetime.utcnow().strftime('%Y%m%d')
    assert number == f'APT-{today}-0001'


def test_numbering_increments_correctly_within_the_same_day(customer):
    from models.appointment import Appointment

    today = datetime.utcnow().strftime('%Y%m%d')
    first = Appointment(customer_id=customer.id, preferred_date='2026-12-15', preferred_time='09:00:00',
                         appointment_number=generate_appointment_number())
    first.save()
    assert first.appointment_number == f'APT-{today}-0001'

    second_number = generate_appointment_number()
    assert second_number == f'APT-{today}-0002'
