"""Session-wide test plumbing (4.8.0).

Every test module makes its own data folder with ``tempfile.mkdtemp`` — a
database and uploads of its own, so the modules never see each other's rows.
Nothing took them away again: each ``run-tests.bat`` left about 36 ``ner-*``
folders in ``%TEMP%``, and the QA pass of 28 Oct 2026 counted 413 of them
(195 MB) on the work laptop. The folders this run made are recorded here and
removed when the run ends; a folder another program made is never touched.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile

# 5.1.2: a composite map is drawn ahead in the background once it is built.
# The suite builds dozens of maps of invented books and looks at none of their
# pictures; drawing them all ahead made run-tests several times slower. Only
# tests/test_composite_speed_512.py turns it on, for the tests about it.
os.environ.setdefault("NER_DRAW_AHEAD", "0")

# 5.4.0: the suite describes the team edition; inside a public installation
# (EDITION = public) it must still read the same words. The public words have
# their own test, which pins the public edition itself.
os.environ.setdefault("RR_EDITION", "team")

_made: list[str] = []
_mkdtemp = tempfile.mkdtemp


def _recording_mkdtemp(*args, **kwargs):
    path = _mkdtemp(*args, **kwargs)
    _made.append(path)
    return path


tempfile.mkdtemp = _recording_mkdtemp


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 — pytest's signature
    # Close the database connections first: on Windows an open SQLite file
    # cannot be deleted.
    try:
        from app import db as _db
        _db.engine.dispose()
    except Exception:  # noqa: BLE001 — the app may never have been imported
        pass
    gc.collect()
    for path in _made:
        shutil.rmtree(path, ignore_errors=True)
