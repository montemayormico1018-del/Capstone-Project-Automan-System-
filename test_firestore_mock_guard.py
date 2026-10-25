"""Round 4 R11: the test suite must never reach real Firestore, even if run
bare (no env vars set by hand).

conftest.py sets FIRESTORE_MOCK=1/TESTING=1 via os.environ.setdefault() at
its own top level -- the earliest point in the whole pytest session, before
any test module or app code is imported -- so firebase_config.py's
module-level `USE_MOCK` constant is computed correctly the one time it's
ever read. firestore_db.py carries a second, independent guard: if TESTING=1
is set but FIRESTORE_MOCK isn't '1' (the two having been forced apart,
rather than the normal case of conftest having set both together), importing
it raises RuntimeError instead of quietly proceeding towards the real
Firestore client that firebase_config.py would otherwise build (a real
firebase-credentials.json ships in this repo).

This test deliberately re-imports firestore_db under that "disagreed" state
to prove the guard fires, then restores the module and env vars exactly as
it found them so the rest of the suite -- which needs the real mock-backed
firestore_db already imported -- is unaffected.
"""
import importlib
import os
import sys

import pytest


def test_firestore_db_raises_when_testing_without_mock(monkeypatch):
    monkeypatch.setenv('TESTING', '1')
    monkeypatch.delenv('FIRESTORE_MOCK', raising=False)

    original_module = sys.modules.pop('firestore_db', None)
    try:
        with pytest.raises(RuntimeError, match='FIRESTORE_MOCK'):
            importlib.import_module('firestore_db')
    finally:
        # A module that raises during its own import is not left behind in
        # sys.modules by Python's import machinery, but pop defensively
        # anyway before restoring the real, already-initialized module the
        # rest of this session depends on.
        sys.modules.pop('firestore_db', None)
        if original_module is not None:
            sys.modules['firestore_db'] = original_module


def test_firestore_db_imports_cleanly_when_mock_and_testing_agree(monkeypatch):
    """Sanity check for the guard's other branch: TESTING=1 with
    FIRESTORE_MOCK=1 (the normal, conftest-set combination) must NOT raise."""
    monkeypatch.setenv('TESTING', '1')
    monkeypatch.setenv('FIRESTORE_MOCK', '1')

    original_module = sys.modules.pop('firestore_db', None)
    try:
        importlib.import_module('firestore_db')
    finally:
        sys.modules.pop('firestore_db', None)
        if original_module is not None:
            sys.modules['firestore_db'] = original_module


def test_conftest_sets_mock_env_vars_before_pytest_import():
    """Regression guard on conftest.py's own structure: the setdefault()
    calls must appear before `import pytest`, since that ordering is what
    guarantees they run before anything else in the session gets a chance
    to import firebase_config/firestore_db."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(repo_root, 'conftest.py'), encoding='utf-8') as f:
        src = f.read()

    setdefault_pos = src.find("os.environ.setdefault('FIRESTORE_MOCK'")
    testing_pos = src.find("os.environ.setdefault('TESTING'")
    pytest_import_pos = src.find('import pytest')

    assert setdefault_pos != -1, 'conftest.py no longer sets FIRESTORE_MOCK'
    assert testing_pos != -1, 'conftest.py no longer sets TESTING'
    assert pytest_import_pos != -1
    assert setdefault_pos < pytest_import_pos
    assert testing_pos < pytest_import_pos


def test_firestore_mock_is_actually_active_this_session():
    """End-to-end confirmation that the whole chain worked for THIS actual
    pytest run: firebase_config.USE_MOCK must be True, meaning every test in
    this session is talking to the offline mock, not a real project."""
    import firebase_config
    assert firebase_config.USE_MOCK is True
