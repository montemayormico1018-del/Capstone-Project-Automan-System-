"""Round 3 R7: routes/quotation.py consolidation regression tests.

routes/quotation.py used to be dead code -- defined but never registered in
register_blueprints() -- while the actual, working quotation view/approve/
reject pages lived directly on the Flask app in app.py. This round moved
app.py's versions into the blueprint (discarding the stale routes/quotation.py
logic, which was missing signed-token verification entirely) and registered
it under url_prefix='/quotation', keeping the exact same paths that
utils/sms_utils.py and routes/appointments.py hardcode in the SMS/quotation
links they send, and that quotation_view.html posts back to.

FIRESTORE_MOCK=1 only -- never touches real Firebase/SMS.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)


@pytest.fixture
def app_client():
    from app import create_app
    app = create_app()
    return app.test_client()


@pytest.fixture
def pending_appointment():
    from models.appointment import Appointment
    appt = Appointment(status='Pending', preferred_date='2026-12-01', preferred_time='10:00:00')
    appt.save()
    return appt


def test_quotation_routes_registered_at_original_paths():
    """Regression guard for the consolidation itself: same 3 paths, same
    methods, served by the quotation blueprint (not ad-hoc app.py routes)."""
    from app import create_app
    app = create_app()
    rules = {r.rule: (r.methods, r.endpoint) for r in app.url_map.iter_rules() if 'quotation' in r.rule}

    assert '/quotation/view/<appointment_id>' in rules
    assert rules['/quotation/view/<appointment_id>'][0] >= {'GET'}
    assert rules['/quotation/view/<appointment_id>'][1] == 'quotation.view_quotation'

    assert '/quotation/approve/<appointment_id>' in rules
    assert rules['/quotation/approve/<appointment_id>'][0] >= {'POST'}
    assert rules['/quotation/approve/<appointment_id>'][1] == 'quotation.approve_quotation_web'

    assert '/quotation/reject/<appointment_id>' in rules
    assert rules['/quotation/reject/<appointment_id>'][0] >= {'POST'}
    assert rules['/quotation/reject/<appointment_id>'][1] == 'quotation.reject_quotation_web'

    # Exactly 3 -- no leftover duplicate registered elsewhere (e.g. still on
    # app.py directly, which would show up as an extra rule with a
    # non-blueprint endpoint name for the same path).
    quotation_paths = [r for r in rules if r.startswith('/quotation/')]
    assert len(quotation_paths) == 3


def test_view_quotation_renders_for_pending_appointment(app_client, pending_appointment):
    r = app_client.get(f'/quotation/view/{pending_appointment.id}')
    assert r.status_code == 200
    assert b'Appointment not found' not in r.data


def test_view_quotation_404_for_unknown_id(app_client):
    r = app_client.get('/quotation/view/does-not-exist')
    assert r.status_code == 404


def test_approve_quotation_rejects_missing_or_bad_token(app_client, pending_appointment):
    # No token at all.
    r = app_client.post(f'/quotation/approve/{pending_appointment.id}')
    assert r.status_code == 403
    assert 'Invalid or expired' in r.get_json()['error']

    # Wrong/garbage token.
    r = app_client.post(f'/quotation/approve/{pending_appointment.id}', data={'token': 'not-a-real-token'})
    assert r.status_code == 403

    # Appointment must still be Pending -- neither attempt approved it.
    from models.appointment import Appointment
    refreshed = Appointment.get(pending_appointment.id)
    assert refreshed.status == 'Pending'


def test_approve_quotation_succeeds_with_valid_signed_token(app_client, pending_appointment):
    from utils.quotation_token import generate_quotation_token
    from models.appointment import Appointment

    with app_client.application.app_context():
        token = generate_quotation_token(pending_appointment.id)

    r = app_client.post(f'/quotation/approve/{pending_appointment.id}', data={'token': token})
    assert r.status_code == 200

    refreshed = Appointment.get(pending_appointment.id)
    assert refreshed.status == 'Approved'


def test_reject_quotation_succeeds_with_valid_signed_token(app_client, pending_appointment):
    from utils.quotation_token import generate_quotation_token
    from models.appointment import Appointment

    with app_client.application.app_context():
        token = generate_quotation_token(pending_appointment.id)

    r = app_client.post(f'/quotation/reject/{pending_appointment.id}', data={'token': token})
    assert r.status_code == 200

    refreshed = Appointment.get(pending_appointment.id)
    assert refreshed.status == 'Rejected'


def test_token_for_one_appointment_does_not_approve_another(app_client, pending_appointment):
    """A signed token is scoped to the exact appointment id it was minted
    for -- this is the whole point of R7's token check (the old dead
    routes/quotation.py had no such check at all)."""
    from models.appointment import Appointment
    from utils.quotation_token import generate_quotation_token

    other = Appointment(status='Pending', preferred_date='2026-12-02', preferred_time='11:00:00')
    other.save()

    with app_client.application.app_context():
        token_for_other = generate_quotation_token(other.id)

    r = app_client.post(f'/quotation/approve/{pending_appointment.id}', data={'token': token_for_other})
    assert r.status_code == 403

    refreshed = Appointment.get(pending_appointment.id)
    assert refreshed.status == 'Pending'
