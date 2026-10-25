"""Panel item #13: "one account, one browser session" -- verifying the
actual enforcement in firebase_auth_utils.py's firebase_required(), not just
that the field exists on User.

/login itself can't be exercised here: it exchanges a real password for a
Firebase ID token via a live Google REST call (exchange_password_for_token_full),
which is exactly the kind of real-service call FIRESTORE_MOCK=1 tests must
never make. So these tests drive the actual mechanism at the layer that
matters -- manipulating User.active_session_id the same way /login does
internally, then hitting a real @jwt_required() endpoint with different
X-Session-Id headers and checking what firebase_required() actually does.

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

    uid = f'sesstest-{uuid.uuid4().hex[:8]}'
    user = User(
        id=uid, username=f'user_{uid}', full_name='Session Tester',
        email=f'{uid}@example.test', role='staff', contact_number='09123456789',
        is_active=True, is_verified=True, active_session_id='session-A',
    )
    user.save()
    firebase_auth_utils.verify_id_token = lambda token: {'uid': uid}

    from app import create_app
    app = create_app()
    client = app.test_client()
    return {'client': client, 'uid': uid}


ENDPOINT = '/api/inventory/critical-count'


def test_request_with_no_session_header_is_let_through(api):
    """Documented escape hatch for older/cached clients that never send the
    header at all -- not a bypass of a real session, just "can't check"."""
    r = api['client'].get(ENDPOINT, headers={'Authorization': 'Bearer fake'})
    assert r.status_code == 200


def test_request_with_current_session_id_succeeds(api):
    r = api['client'].get(ENDPOINT, headers={
        'Authorization': 'Bearer fake', 'X-Session-Id': 'session-A',
    })
    assert r.status_code == 200


def test_stale_session_id_is_rejected_after_a_new_login(api):
    from models.user import User

    # Mirrors exactly what routes/auth.py's login() does on a fresh login:
    # mint a new session id and overwrite the user's active one.
    user = User.get(api['uid'])
    user.active_session_id = 'session-B'
    user.save()

    r = api['client'].get(ENDPOINT, headers={
        'Authorization': 'Bearer fake', 'X-Session-Id': 'session-A',
    })
    assert r.status_code == 401
    data = r.get_json()
    assert data.get('session_conflict') is True


def test_new_session_id_still_works_after_the_conflict(api):
    from models.user import User

    user = User.get(api['uid'])
    user.active_session_id = 'session-B'
    user.save()

    r = api['client'].get(ENDPOINT, headers={
        'Authorization': 'Bearer fake', 'X-Session-Id': 'session-B',
    })
    assert r.status_code == 200


def test_second_browsers_own_subsequent_login_locks_out_the_first(api):
    """The realistic sequence: browser A is logged in (session-A). Browser B
    logs into the SAME account (session-B). Browser A's very next request,
    still holding session-A, must now be rejected."""
    from models.user import User
    client = api['client']

    # Browser A working normally.
    r = client.get(ENDPOINT, headers={'Authorization': 'Bearer fake', 'X-Session-Id': 'session-A'})
    assert r.status_code == 200

    # Browser B logs in -- same effect /login has: a fresh session id wins.
    user = User.get(api['uid'])
    user.active_session_id = 'session-B'
    user.save()

    # Browser A, unaware, tries its next request with its old session id.
    r = client.get(ENDPOINT, headers={'Authorization': 'Bearer fake', 'X-Session-Id': 'session-A'})
    assert r.status_code == 401
    assert r.get_json().get('session_conflict') is True

    # Browser B keeps working.
    r = client.get(ENDPOINT, headers={'Authorization': 'Bearer fake', 'X-Session-Id': 'session-B'})
    assert r.status_code == 200
