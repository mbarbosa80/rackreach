"""5.5.0-rc7 — what the independent tester of rc6 found.

* R6-2: a tile that never draws within the deadline was refused for 120 s
  after each try, for ever: every ~165 s it held the renderer for 45 s more.
  The wait now doubles with each try that fails again (capped);
* R6-3: a composite tile waiting for the lock while its map, its book or its
  project was removed composed the map's picture again (orphan files, an
  extra compose of a book being removed);
* R6-4: a project's folders were deleted while the tile lock was held — every
  other project's undrawn tiles waited for the whole ``rmtree`` (25 s for
  300,000 tiles). They are moved aside under the lock and deleted after it; an
  aside folder left by a crash is deleted at the next start.

(R6-1 is in the viewer: ``qa/viewer_tiles_rc5.py``.)
The PDFs here are synthetic — never a real venue document in a test.
"""

from __future__ import annotations

import shutil
import threading
import time
import time as _time

import pytest

import tests.test_big_sheets_120 as B                              # noqa: E402 — sets the data folder
import tests.test_tester_rc4_120 as R4                             # noqa: E402
from app import composite_service as CS                            # noqa: E402
from app import page_tiles as PT                                   # noqa: E402
from app import services as S                                      # noqa: E402
from app.db import SessionLocal                                    # noqa: E402
from tests.test_tester_rc4_120 import fresh_renderer  # noqa: E402,F401
from tests.test_tester_rc5_120 import _REAL_CHILD                  # noqa: E402
from tests.test_wireless_tie_250 import client  # noqa: E402,F401


class _Clock:
    """``page_tiles``' clock, moved on by hand: two minutes pass in a test."""

    def __init__(self) -> None:
        self.offset = 0.0

    def perf_counter(self) -> float:
        return _time.perf_counter() + self.offset

    def __getattr__(self, name):
        return getattr(_time, name)


# ---------------------------------------------------------------------------
# R6-2 — the wait grows with each try that fails again
# ---------------------------------------------------------------------------

def test_a_tile_that_keeps_missing_the_deadline_waits_longer_each_time(
        client, tmp_path, monkeypatch, fresh_renderer):
    pid, did = B._project("rc7 backoff", B._big_book(tmp_path / "MP_R62.pdf"))
    clock = _Clock()
    monkeypatch.setattr(PT, "time", clock)
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 0.5)
    monkeypatch.setattr(PT, "_CHILD", R4._SILENT_CHILD)
    url = R4._url(pid, did, 0, 0, 0)
    waits = []
    for _ in range(5):
        r = client.get(url)                  # tried, stopped at the deadline
        assert r.status_code == 503, r.text
        waits.append(int(r.headers["retry-after"]))
        # Asked again within the wait: refused at once, the wait unchanged.
        again = client.get(url)
        assert again.status_code == 503 and int(again.headers["retry-after"]) <= waits[-1]
        clock.offset += waits[-1] + 1         # the wait is over
    assert waits == [120, 240, 480, 900, 900], waits
    # Drawn at last: a later failure starts from the first wait again.
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 45.0)
    monkeypatch.setattr(PT, "_CHILD", _REAL_CHILD)
    with PT._TILE_LOCK:
        PT._renderer.stop()
    assert client.get(url).status_code == 200
    shutil.rmtree(PT.tiles_dir(pid, did, B._sha(did), 1), ignore_errors=True)
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 0.5)
    monkeypatch.setattr(PT, "_CHILD", R4._SILENT_CHILD)
    with PT._TILE_LOCK:
        PT._renderer.stop()
    r = client.get(url)
    assert (r.status_code, r.headers.get("retry-after")) == (503, "120")


# ---------------------------------------------------------------------------
# R6-3 — no composing for a map, a book or a project being removed
# ---------------------------------------------------------------------------

@pytest.fixture
def composes(monkeypatch):
    calls: list = []

    def fake(row, drawing):
        calls.append(row.id)
        raise CS.CompositeError("composed (the test stops here)")

    monkeypatch.setattr(CS, "_compose", fake)
    return calls


def test_a_composite_tile_does_not_compose_a_map_being_removed(client, tmp_path, monkeypatch, composes):
    from app.models import CompositeMap, Project
    pid, did = B._project("rc7 map removed", B._big_book(tmp_path / "MP_R63.pdf"))
    mid = R4._fake_map(pid, did)             # its picture is not on disk
    url = f"/api/projects/{pid}/composite/{mid}/tiles/3/1/0.png"   # nor this tile
    real_audit = CS._audit

    def slow_audit(*a, **k):                 # between the files gone and the row gone
        time.sleep(1.0)
        return real_audit(*a, **k)

    monkeypatch.setattr(CS, "_audit", slow_audit)
    release = threading.Event()

    def hold():                              # a tile being drawn
        with PT._TILE_LOCK:
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    time.sleep(0.1)

    def remove():
        with SessionLocal() as db:
            CS.delete(db, db.get(Project, pid), db.get(CompositeMap, mid))

    got: dict = {}
    asker = threading.Thread(target=lambda: got.update(r=client.get(url)))
    asker.start()
    time.sleep(0.4)                          # past the on-disk step, waiting for the lock
    removal, rout = R4._in_thread(remove)
    time.sleep(0.3)                          # the removal waits for it too, ahead
    release.set()
    for th in (holder, removal, asker):
        th.join(15)
    assert "error" not in rout, rout
    assert composes == [], "the map being removed was composed again"
    assert got["r"].status_code == 404


@pytest.mark.parametrize("what", ["drawing", "project"])
def test_a_composite_tile_does_not_compose_for_a_book_or_project_being_removed(
        client, tmp_path, composes, what):
    pid, did = B._project(f"rc7 {what} removed", B._big_book(tmp_path / f"MP_R63{what}.pdf"))
    mid = R4._fake_map(pid, did)
    url = f"/api/projects/{pid}/composite/{mid}/tiles/3/1/0.png"
    flags, key = ((PT._removing_drawings, did) if what == "drawing"
                  else (PT._removing_projects, pid))
    flags.add(key)
    try:
        r = client.get(url)
    finally:
        flags.discard(key)
    assert composes == [] and r.status_code == 404, (composes, r.status_code)


# ---------------------------------------------------------------------------
# R6-4 — a project's folders are deleted after the tile lock is let go
# ---------------------------------------------------------------------------

def test_a_project_removal_deletes_its_folders_after_letting_go_of_the_lock(
        client, tmp_path, monkeypatch):
    pid, did = B._project("rc7 rmtree", B._big_book(tmp_path / "MP_R64.pdf"))
    assert client.get(R4._url(pid, did, 1, 0, 0)).status_code == 200
    (S.settings.exports_dir / f"project_{pid}").mkdir(parents=True, exist_ok=True)
    (S.settings.exports_dir / f"project_{pid}" / "report.xlsx").write_bytes(b"x")
    under_lock: list[str] = []
    real = shutil.rmtree

    def watching(path, *a, **k):
        if CS._TILE_LOCK.locked():
            under_lock.append(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(shutil, "rmtree", watching)
    S.remove_project_files(pid)
    assert under_lock == [], under_lock
    for base in (S.settings.uploads_dir, S.settings.exports_dir, S.settings.composite_dir):
        assert not (base / f"project_{pid}").exists()
        assert not [p for p in base.iterdir() if f"project_{pid}" in p.name], list(base.iterdir())


def test_an_aside_folder_left_by_a_crash_is_deleted_at_the_next_start(client):
    from fastapi.testclient import TestClient
    from app.main import app
    left = []
    for base in (S.settings.uploads_dir, S.settings.exports_dir, S.settings.composite_dir):
        d = base / ".removing-project_987654-1"
        (d / "sub").mkdir(parents=True, exist_ok=True)
        (d / "sub" / "f.png").write_bytes(b"x")
        left.append(d)
    keep = S.settings.uploads_dir / ".removing-notes"       # not a removal's name: kept
    keep.mkdir(parents=True, exist_ok=True)
    with TestClient(app):                    # the application starts
        end = time.time() + 20
        while time.time() < end and any(d.exists() for d in left):
            time.sleep(0.1)
    assert not any(d.exists() for d in left), [str(d) for d in left if d.exists()]
    assert keep.is_dir()
    keep.rmdir()


def test_rc8_leftover_cleanup_takes_ascii_digits_only_and_reports_only_what_went(tmp_path, monkeypatch):
    """rc7 tester R7-L1/L2: a non-ASCII digit name is not ours; a symlink is
    neither followed nor reported as deleted."""
    import os
    import types
    from app import services as S
    dirs = {}
    for name in ("uploads_dir", "exports_dir", "composite_dir"):
        dirs[name] = tmp_path / name
        dirs[name].mkdir()
    monkeypatch.setattr(S, "settings", types.SimpleNamespace(**dirs))
    up = tmp_path / "uploads_dir"
    (up / ".removing-project_7-123").mkdir()
    (up / ".removing-project_٣-123").mkdir()
    target = tmp_path / "keep"
    target.mkdir()
    (target / "f.txt").write_text("x")
    try:
        os.symlink(target, up / ".removing-project_8-456", target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        linked = False
    gone = S.delete_removal_leftovers()
    assert gone == [str(up / ".removing-project_7-123")]
    assert (up / ".removing-project_٣-123").exists()
    assert (target / "f.txt").exists()
    if linked:
        assert (up / ".removing-project_8-456").is_symlink()
