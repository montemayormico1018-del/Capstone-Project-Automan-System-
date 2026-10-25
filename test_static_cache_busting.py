"""User report: a shipped fix (the sidebar's blinking critical-stock dot)
wasn't showing up for a browser that had visited the site before. Root
cause: app.py's static-asset cache-buster hashed each file's mtime, but
Cloud Run's Buildpacks-based deploy ("gcloud run deploy --source .")
normalizes every file's mtime to a fixed timestamp for reproducible
builds -- so the ?v= query param never actually changed between deploys,
and with Cache-Control: public, max-age=31536000 (1 year) on /static/,
any browser that ever cached a file kept serving that exact byte-for-byte
version indefinitely. Fixed by hashing file content instead of mtime,
which is correct regardless of what the deploy pipeline does to
timestamps. This verifies url_for('static', ...) produces a ?v= that
actually changes when a file's content changes (not just doesn't crash).

FIRESTORE_MOCK=1 only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

if os.environ.get('FIRESTORE_MOCK') != '1':
    pytest.skip('requires FIRESTORE_MOCK=1', allow_module_level=True)


@pytest.fixture
def app():
    from app import create_app
    return create_app('development')


def test_static_url_carries_a_content_derived_version(app, tmp_path, monkeypatch):
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    (static_dir / 'probe.js').write_text('console.log("v1");')
    app.static_folder = str(static_dir)

    with app.test_request_context():
        from flask import url_for
        url_v1 = url_for('static', filename='probe.js')
    assert 'v=' in url_v1

    # Changing the file's CONTENT (not touching mtime deliberately -- the
    # bug this guards against is exactly "mtime doesn't reflect a real
    # change") must produce a different ?v=, in a fresh app instance since
    # the version cache is per-create_app() call.
    (static_dir / 'probe.js').write_text('console.log("v2 - totally different");')
    from app import create_app
    app2 = create_app('development')
    app2.static_folder = str(static_dir)
    with app2.test_request_context():
        from flask import url_for
        url_v2 = url_for('static', filename='probe.js')

    assert url_v1 != url_v2, 'changed file content must bust the cache with a new ?v='


def test_static_url_is_stable_for_unchanged_content(app):
    with app.test_request_context():
        from flask import url_for
        url_a = url_for('static', filename='js/automan-nav.js')
        url_b = url_for('static', filename='js/automan-nav.js')
    assert url_a == url_b


def test_missing_static_file_does_not_crash(app):
    with app.test_request_context():
        from flask import url_for
        # Must not raise even though the file doesn't exist.
        url = url_for('static', filename='js/does-not-exist-at-all.js')
    assert 'js/does-not-exist-at-all.js' in url
