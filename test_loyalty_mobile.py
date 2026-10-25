"""Source guards for the Capacitor/phone polish on My Rewards.

The rewards section on the customer dashboard is the customer "app" -- that
page runs inside a Capacitor WebView, so the JS that drives it is only ever
executed by a real Android WebView. There is no headless DOM in this suite,
so these tests assert the *properties* the JS must keep, the same way
test_loyalty_xss.py guards escaping and test_static_cache_busting.py guards
the ?v= cache-buster: by reading the source, plus a couple of real API
round-trips for the endpoint contracts those properties depend on.

What each test protects, and why it matters:

  - the history request can't emit offset=0/limit=0. routes/loyalty.py's
    _page_args() rejects zero with a 400, so a first page that sends
    offset=0 renders an error where the ledger should be. This was a real
    break; the guard is what keeps it fixed.
  - every HTTP status utils/loyalty_service.redeem_reward can raise has an
    entry in the client's REDEEM_ERROR_MAP, so no failure mode falls through
    to a bare "something went wrong".
  - the push routing keys the client reads are exactly the `type` values the
    server sends, so a new server-side notification can't silently stop
    being routed in-app.
  - the QR library is served from 'self'. The app's CSP (app.py) allowlists
    only 'self', cdn.jsdelivr.net, code.jquery.com and www.tuqlas.com, so a
    CDN-hosted script would be blocked in the WebView and the voucher QR
    would silently never render.

FIRESTORE_MOCK=1 only.
"""
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = 'customer_dashboard.html'


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def _template_source(relative_path=DASHBOARD):
    return _read('templates', relative_path)


def _js_block(src):
    """The page's main inline script. Everything this feature adds lives
    there; the other blocks on the page are two-liners."""
    start = src.index('// Customer Dashboard JS')
    return src[start:src.index('</script>', start)]


# ---------------------------------------------------------------------------
# History pagination: the endpoint's zero-rejection is a hard contract
# ---------------------------------------------------------------------------

def test_transactions_endpoint_rejects_zero_offset_and_limit(api):
    """/me/transactions 400s on offset=0 or limit=0. Asserted against the real
    route so the client's "omit offset on page 1" rule can't be justified by a
    stale reading of it."""
    client = api['client']
    headers = api['customer_H']

    for query in ('limit=20&offset=0', 'limit=0&offset=1', 'offset=0'):
        r = client.get(f'/api/loyalty/me/transactions?{query}', headers=headers)
        assert r.status_code == 400, f'{query} should be rejected, got {r.status_code}'

    # Omitting both is fine (limit defaults to 20, offset to 0) -- which is
    # exactly the page-1 request the dashboard sends.
    r = client.get('/api/loyalty/me/transactions?limit=20', headers=headers)
    assert r.status_code == 200, r.get_json()


def test_history_request_cannot_send_offset_zero(api):
    """The offset is interpolated into the query string only when non-zero, so
    this asserts the *conditional* rather than any single literal -- a
    refactor that hardcodes `offset=${pointsHistoryOffset}` (which is 0 on page
    one) must fail here."""
    src = _template_source()
    assert re.search(
        r"pointsHistoryOffset\s*>\s*0\s*\?\s*`&offset=\$\{pointsHistoryOffset\}`", src
    ), 'the history query must append offset only when it is greater than zero'

    # ...and limit is clamped to the route's PAGE_SIZE_MAX, so a larger page
    # size can never turn into a 400.
    assert 'HISTORY_MAX_LIMIT = 100' in src
    assert re.search(r'Math\.min\(Math\.max\(1,\s*HISTORY_PAGE_SIZE\),\s*HISTORY_MAX_LIMIT\)', src)


def test_history_pagination_guards_against_overlapping_requests(api):
    """Load more is a button a phone customer can tap twice, and the poll can
    land on top of a page fetch. Both would request the same offset and append
    the same rows twice, since the offset only advances after a response is
    applied."""
    src = _template_source()
    assert 'pointsHistoryLoading' in src, 'a re-entrancy guard is missing from the history loader'
    assert re.search(r'if\s*\(pointsHistoryLoading\)\s*return;', src)


# ---------------------------------------------------------------------------
# Redeem error mapping
# ---------------------------------------------------------------------------

def _redeem_statuses():
    """Every status code redeem_reward can raise, read off the service rather
    than hardcoded, so a new rule in loyalty_service can't skip the UI."""
    from utils import loyalty_service as svc
    statuses = set()
    with open(os.path.join(REPO_ROOT, 'utils', 'loyalty_service.py'), encoding='utf-8') as f:
        service_src = f.read()
    # Scoped to redeem_reward's body so unrelated rules (voucher application,
    # adjustments) don't widen the required set.
    body = service_src[service_src.index('def redeem_reward('):]
    body = body[:body.index('\ndef ')]
    for match in re.finditer(r'raise LoyaltyError\((?:[^()]|\([^()]*\))*?(\d{3})\)', body, re.S):
        statuses.add(int(match.group(1)))
    return statuses, svc


def test_redeem_error_map_covers_every_status_the_service_can_raise(api):
    statuses, _ = _redeem_statuses()
    # 400 is LoyaltyError's default argument rather than a literal at the raise
    # site, so assert the real minimum explicitly and let the source scan add
    # the rest.
    assert 403 in statuses and 404 in statuses and 409 in statuses
    assert 429 in statuses, 'the daily-cap 429 must still be raised by the service'
    assert 503 in statuses

    src = _template_source()
    for status in sorted(statuses | {400}):
        assert re.search(rf'^\s*{status}:\s', src, re.M), (
            f'REDEEM_ERROR_MAP has no entry for HTTP {status}; that failure would '
            'reach the customer as a generic error.'
        )


def test_redeem_error_messages_say_what_the_customer_can_do(api):
    """Each mapped status has to name the actual problem, not just restate the
    status. Substring checks on the wording the UI would actually show."""
    src = _template_source()
    expectations = {
        429: 'daily',          # daily redemption cap
        403: 'unavailable',    # program switched off
        503: 'try again',      # voucher code generation is retryable
        404: 'catalog',        # reward no longer listed
    }
    for status, word in expectations.items():
        entry = re.search(rf'^\s*{status}:\s*[\'"]([^\'"]+)[\'"]', src, re.M)
        assert entry, f'no REDEEM_ERROR_MAP entry for {status}'
        assert word in entry.group(1).lower(), (
            f'the {status} message should mention "{word}": {entry.group(1)!r}'
        )

    # Insufficient points and out of stock are both 409, so the status alone
    # can't distinguish them -- the server's own message is what carries the
    # detail, and the client must keep it rather than replacing it.
    assert 'redeemErrorMessage' in src
    assert re.search(r'const detail = \(serverMessage \|\| \'\'\)\.trim\(\);', src)
    assert "return headline + ' (' + detail + ')';" in src, (
        'the server message must be appended to the mapped headline, otherwise '
        'every 409 reads as the same generic sentence'
    )


def test_redeem_failures_use_the_map(api):
    src = _template_source()
    assert re.search(r'redeemErrorMessage\(response\.status,\s*data\.error\)', src), (
        'confirmRedeem must route failures through redeemErrorMessage()'
    )


# ---------------------------------------------------------------------------
# Push routing
# ---------------------------------------------------------------------------

def test_loyalty_push_types_match_what_the_server_sends(api):
    """utils/loyalty_service pushes data.type; the client routes on
    data.type.indexOf('loyalty_') === 0. If a notify_* helper is added or
    renamed without the client knowing, this fails rather than the tap
    silently falling back to a full page reload."""
    service_src = _read('utils', 'loyalty_service.py')
    sent = set(re.findall(r"'type':\s*'(loyalty_[a-z_]+)'", service_src))
    assert sent == {
        'loyalty_points_earned',
        'loyalty_reward_redeemed',
        'loyalty_voucher_expiring',
    }

    client_src = _template_source()
    # The routing key is read off notification.notification.data, and only
    # payload types starting with loyalty_ skip the reload.
    assert 'notification.notification' in client_src
    assert re.search(r"type\.indexOf\('loyalty_'\)\s*===\s*0", client_src)
    # Every non-loyalty push must still reload -- that's the pre-existing
    # behaviour the app depends on.
    assert re.search(
        r"push\.addListener\('pushNotificationActionPerformed'[\s\S]{0,600}?"
        r"openLoyaltyPushTarget\(notification\)[\s\S]{0,200}?window\.location\.reload\(\)",
        client_src,
    ), 'loyalty pushes route in-app; everything else must still reload'


def test_loyalty_push_opens_the_named_voucher(api):
    """loyalty_reward_redeemed and loyalty_voucher_expiring both carry
    voucher_code, and tapping them is expected to land on that voucher's QR."""
    src = _template_source()
    assert re.search(r'if\s*\(code\)\s*\{\s*setTimeout\(function\s*\(\)\s*\{ openVoucherDetail', src), (
        'a push carrying voucher_code must open that voucher detail'
    )
    # The foreground handler has to refresh too, or the toast announces a
    # balance the number on screen doesn't show.
    assert re.search(
        r"push\.addListener\('pushNotificationReceived'[\s\S]{0,2500}?if \(isLoyalty\) refreshLoyaltyLive",
        src,
    )


def test_foreground_reasons_are_wired(api):
    """Three triggers, one entry point: returning to the foreground, regaining
    window focus, and a loyalty push."""
    src = _template_source()
    for event in ('visibilitychange', 'focus'):
        assert re.search(
            rf"addEventListener\('{event}'[\s\S]{{0,220}}?refreshLoyaltyLive\(", src
        ), f'{event} must trigger a loyalty refresh'
    assert re.search(r'if \(isLoyalty\) refreshLoyaltyLive', src)

    # Polling exists, is bounded, and only runs while the section is visible.
    assert re.search(r'LOYALTY_POLL_MS\s*=\s*\d+', src)
    assert 'rewardsSectionOnScreen()' in src
    assert 'setInterval' in src and 'clearInterval' in src
    assert re.search(r"addEventListener\('visibilitychange'[\s\S]{0,700}?stopLoyaltyPolling\(\)", src)

    # An in-flight guard: visibilitychange and focus both fire on one Android
    # foregrounding, and a push can follow immediately after.
    assert re.search(r'if\s*\(loyaltyRefreshing\)\s*return;', src)
    assert re.search(r'LOYALTY_REFRESH_COOLDOWN_MS\s*=\s*\d+', src)


# ---------------------------------------------------------------------------
# Voucher detail / QR / copy
# ---------------------------------------------------------------------------

def test_voucher_qr_library_is_served_from_self(api):
    """The app's CSP script-src allowlist is 'self', cdn.jsdelivr.net,
    code.jquery.com and www.tuqlas.com. A CDN-hosted QR script would be
    blocked inside the WebView, so the library is vendored under /static."""
    src = _template_source()
    assert re.search(r'<script src="\{\{ url_for\(.static., filename=.js/qrcode\.min\.js.\) \}\}">', src)
    # A CDN-hosted QR script would be blocked inside the WebView. (Only a real
    # tag counts -- the template's own comment explains why it's vendored.)
    assert not re.search(r'(?:src|href)="https://cdnjs', src), (
        'cdnjs is not in the CSP allowlist, so a CDN-hosted QR script would not load'
    )

    assert os.path.exists(os.path.join(REPO_ROOT, 'static', 'js', 'qrcode.min.js')), (
        'the vendored QR library is referenced by the template but missing from /static'
    )

    csp = _read('app.py')
    script_src = re.search(r'script-src ([^;"]+)', csp).group(1)
    assert 'cdn.jsdelivr.net' in script_src
    for host in ('cdnjs', 'unpkg', 'cdn.skypack.dev'):
        assert host not in script_src, f'{host} is not in the CSP allowlist'


def test_voucher_detail_shows_code_copy_and_qr(api):
    src = _template_source()
    for element_id in ('voucherDetailCode', 'voucherDetailCopyBtn',
                       'voucherDetailQr', 'voucherDetailStatus', 'voucherDetailMeta'):
        assert f'id="{element_id}"' in src, f'#{element_id} is missing from the voucher detail modal'

    # The code is set as text, never as HTML, and the QR is built in-page.
    assert re.search(r"codeEl\.textContent = voucher\.voucher_code", src)
    assert 'new QRCode(target' in src
    assert 'typeof QRCode === \'undefined\'' in src, (
        'a missing/blocked QR library must degrade to the plain code, not an empty box'
    )
    assert 'navigator.clipboard' in src and 'legacyCopyToClipboard' in src


def test_used_and_expired_vouchers_are_dimmed_with_the_server_status(api):
    """The dimmed styling must be driven by the server's status, never a
    client-side date comparison -- voucher_status_now() already folds expiry
    into the stored status."""
    src = _template_source()
    assert "v.status === 'active' ? '' : ' voucher-tile-inactive'" in src
    assert re.search(r"isActive = voucher\.status === 'active'", src)
    # Each non-active state reads differently rather than just looking grey.
    assert re.search(r"voucher\.status === 'used'", src)
    assert 'This voucher has expired.' in src


def test_voucher_tiles_open_the_detail_view(api):
    src = _template_source()
    # Tappable and keyboard-reachable: role=button + tabindex + a keydown
    # handler, since a role=button div isn't focusable or Enter-activatable
    # on its own.
    assert 'role="button" tabindex="0"' in src
    assert re.search(r"onkeydown=\"if \(event\.key === 'Enter'", src)
    assert re.search(r"onclick=\"openVoucherDetail\('\$\{escapeHtml\(v\.voucher_code", src)


# ---------------------------------------------------------------------------
# Phone layout + Android back button
# ---------------------------------------------------------------------------

def test_viewport_allows_safe_area_insets(api):
    src = _template_source()
    assert re.search(r'<meta name="viewport"[^>]*viewport-fit=cover', src)
    # Inset has to be consumed where content touches an edge, not merely
    # allowed: the fixed topbar, the page gutters, and the section's bottom.
    for pattern in (r'env\(safe-area-inset-top\)', r'env\(safe-area-inset-left\)',
                    r'env\(safe-area-inset-right\)', r'env\(safe-area-inset-bottom\)'):
        assert re.search(pattern, src), f'{pattern} is never used'


def test_rewards_section_is_single_column_and_44px_tappable(api):
    src = _template_source()
    # The catalog grid renders col-12 col-sm-6; below sm that pair is ~150px
    # wide per column at 360px, so the phone block pins it to one per row.
    assert re.search(
        r'#rewardsCatalog > \[class\*="col-"\][\s\S]{0,200}?max-width:\s*100%\s*!important', src
    )
    assert re.search(r'#rewardsSection \.btn[\s\S]{0,120}?min-height:\s*44px\s*!important', src)
    # Long admin-supplied text must wrap rather than widen the card.
    assert re.search(r'#rewardsSection \.card-body[\s\S]{0,400}?overflow-wrap:\s*anywhere', src)


def test_hardware_back_peels_off_one_layer_at_a_time(api):
    src = _template_source()
    assert "addListener('backButton'" in src
    # Order matters: voucher modal, then the Swal, then leave the section.
    order = [m.start() for m in re.finditer(
        r"bootstrap\.Modal\.getInstance\(modalEl\)\.close\(\)|Swal\.close\(\)|showSection\('dashboard'\)",
        src)]
    assert len(order) == 3 and order == sorted(order), (
        'back must close the voucher modal, then the dialog, then leave My Rewards'
    )
    # Unhandled means the shell's default (minimise) still happens.
    assert re.search(r'return \{ handled: false \};', src)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def api():
    """Same shape test_loyalty_xss.py uses: a customer account plus a matching
    Customer record, which is how routes/loyalty.py resolves 'me'."""
    import firebase_auth_utils
    from models.customer import Customer
    from models.user import User

    uid = f'loyaltymobile-{uuid.uuid4().hex[:8]}'
    email = f'{uid}@example.test'
    User(id=uid, username=f'lm_{uid}', full_name='Mobile Tester', email=email,
         role='customer', contact_number='09171230015',
         is_active=True, is_verified=True).save()
    firebase_auth_utils.verify_id_token = lambda token: {'uid': uid if token == 'customer' else 'unknown'}
    Customer(name='Mobile Tester', phone='09171230015', email=email).save()

    from app import create_app
    app = create_app()
    yield {
        'client': app.test_client(),
        'customer_H': {'Authorization': 'Bearer customer'},
        'email': email,
    }
