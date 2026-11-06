"""5.5.0-rc6 — what the independent tester of rc5 found.

* R5-2: removing a book took the tile lock three times (``drop_drawing``,
  ``drop_done`` in the service, ``drop_done`` again in the route) — and once
  more for each composite map of the book; between the takes a tile of
  another book took it, so the removal waited for that tile again (88 s
  behind a book whose tiles hang). One take per removal now;
* R5-1: a tile stopped at the deadline came back as a plain 404 and was then
  refused, also as 404, for two minutes — the viewer could not tell it from a
  missing tile, gave up on it and said nothing. It is a 503 with Retry-After
  now. A renderer whose answer is already on its way at the deadline is no
  longer killed (its open books and display lists are kept);
* lows: the two-minute refusal follows the book's checksum, not its file
  path; a composite tile on disk is not served while its map is removed.

The PDFs here are synthetic — never a real venue document in a test.
"""

from __future__ import annotations

import threading
import time

import pytest

import tests.test_big_sheets_120 as B                              # noqa: E402 — sets the data folder
import tests.test_tester_rc4_120 as R4                             # noqa: E402
from app import composite_service as CS                            # noqa: E402
from app import page_tiles as PT                                   # noqa: E402
from app import services as S                                      # noqa: E402
from tests.test_tester_rc4_120 import fresh_renderer  # noqa: E402,F401
from tests.test_wireless_tie_250 import client  # noqa: E402,F401


def _count_takes(monkeypatch) -> list:
    """Every time the tile lock is taken, by whom (urgent or not)."""
    takes: list = []
    real = CS._TILE_LOCK.acquire

    def counting(urgent: bool = False):
        takes.append(urgent)
        return real(urgent)

    monkeypatch.setattr(CS._TILE_LOCK, "acquire", counting)
    return takes


# ---------------------------------------------------------------------------
# R5-2 — one take of the tile lock per removal
# ---------------------------------------------------------------------------

def test_removing_a_book_takes_the_tile_lock_once(client, tmp_path, monkeypatch):
    pid, did = B._project("rc6 one take", B._big_book(tmp_path / "MP_R52.pdf"))
    assert client.get(R4._url(pid, did, 1, 0, 0)).status_code == 200
    R4._fake_map(pid, did)                   # a composite map with a picture on disk
    takes = _count_takes(monkeypatch)
    assert client.delete(f"/api/projects/{pid}/drawings/{did}").status_code == 200
    assert len(takes) == 1, takes


def test_removing_a_project_takes_the_tile_lock_once(client, tmp_path, monkeypatch):
    pid, did = B._project("rc6 one take project", B._big_book(tmp_path / "MP_R52b.pdf"))
    assert client.get(R4._url(pid, did, 1, 0, 0)).status_code == 200
    takes = _count_takes(monkeypatch)
    S.remove_project_files(pid)
    assert len(takes) == 1, takes
    assert pid not in PT._removing_projects


def test_drop_done_does_not_wait_for_the_tile_lock():
    PT._removing_drawings.add(990501)
    release = threading.Event()

    def hold():
        with PT._TILE_LOCK:
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    time.sleep(0.1)
    th, out = R4._in_thread(lambda: PT.drop_done(drawing_id=990501))
    th.join(2.0)
    waited = th.is_alive()
    release.set()
    holder.join()
    th.join(10)
    assert not waited, "drop_done waited for the tile being drawn"
    assert 990501 not in PT._removing_drawings


def test_a_removal_waits_for_the_tile_in_flight_only(client, tmp_path, monkeypatch):
    """The tester's t19: tiles of another book drawn one after the other while
    a book is removed. rc5 waited for one of them per take of the lock."""
    pid, did = B._project("rc6 waits once", B._big_book(tmp_path / "MP_R52c.pdf"))
    in_flight = threading.Event()

    def other_books_tile():                  # each holds the lock 1 s, like a slow tile
        with PT._TILE_LOCK:
            in_flight.set()
            time.sleep(1.0)

    drawers = [threading.Thread(target=other_books_tile) for _ in range(4)]
    drawers[0].start()
    in_flight.wait(5)
    for th in drawers[1:]:
        th.start()
    time.sleep(0.1)
    t0 = time.perf_counter()
    assert client.delete(f"/api/projects/{pid}/drawings/{did}").status_code == 200
    took = time.perf_counter() - t0
    for th in drawers:
        th.join(10)
    assert took < 1.5, f"the removal waited {took:.1f} s (more than the tile in flight)"


# ---------------------------------------------------------------------------
# R5-1 — a tile too slow to draw says so, and when to ask again
# ---------------------------------------------------------------------------

def test_a_tile_stopped_at_the_deadline_answers_503_with_when_to_ask_again(
        client, tmp_path, monkeypatch, fresh_renderer):
    pid, did = B._project("rc6 slow 503", B._big_book(tmp_path / "MP_R51.pdf"))
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 1.5)
    monkeypatch.setattr(PT, "_CHILD", R4._SILENT_CHILD)
    t0 = time.perf_counter()
    r = client.get(R4._url(pid, did, 0, 0, 0))
    first = time.perf_counter() - t0
    assert (r.status_code, r.headers.get("retry-after")) == (503, str(int(PT.SLOW_TILE_RETRY_S))), \
        (r.status_code, r.headers, r.text)
    assert first < 5.0
    # Asked again at once: refused at once, with the seconds still to wait.
    t0 = time.perf_counter()
    r = client.get(R4._url(pid, did, 0, 0, 0))
    assert time.perf_counter() - t0 < 1.0
    assert r.status_code == 503, r.text
    assert 1 <= int(r.headers["retry-after"]) <= int(PT.SLOW_TILE_RETRY_S)
    # Once the refusal ends the tile is drawn (a real renderer this time).
    # The real renderer gets the real deadline: starting it (a new interpreter
    # and PyMuPDF) takes longer than the short test deadline on Windows.
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 45.0)
    monkeypatch.setattr(PT, "_CHILD", _REAL_CHILD)
    PT._too_slow.clear()
    assert client.get(R4._url(pid, did, 0, 0, 0)).status_code == 200


#: The renderer's own child script, for a test that swapped it for a fake.
_REAL_CHILD = PT._CHILD

_SLOW_ANSWER_CHILD = r"""
import pickle, struct, sys, time
_in, _out = sys.stdin.buffer, sys.stdout.buffer
n = struct.unpack('>I', _in.read(4))[0]; _in.read(n)
while True:
    head = _in.read(4)
    if len(head) < 4:
        break
    _in.read(struct.unpack('>I', head)[0])
    data = pickle.dumps(("ok", b"PNG-ANSWER"))
    _out.write(struct.pack('>I', len(data))); _out.flush()   # the answer starts...
    time.sleep(2.5)                                           # ...and ends after the deadline
    _out.write(data); _out.flush()
"""


def test_an_answer_under_way_at_the_deadline_is_served_and_the_renderer_kept(
        tmp_path, monkeypatch, fresh_renderer):
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 1.0)
    monkeypatch.setattr(PT, "_CHILD", _SLOW_ANSWER_CHILD)
    book = B._big_book(tmp_path / "MP_R51b.pdf")
    with PT._TILE_LOCK:
        png = PT.render_tile(str(book), 1, 6000, 4000, 0, 0, 0)
        pid = PT._renderer.pid
    assert png == b"PNG-ANSWER"
    assert pid is not None                   # not killed: its books stay open


def test_closing_a_book_does_not_fail_on_a_renderer_that_does_not_answer(
        tmp_path, monkeypatch, fresh_renderer):
    """Removing a book asks the renderer to close it; one that does not answer
    is stopped (which closes everything it held) — the removal goes on."""
    monkeypatch.setattr(PT, "CHORE_DEADLINE_S", 1.0)
    monkeypatch.setattr(PT, "_CHILD", R4._SILENT_CHILD)
    book = B._big_book(tmp_path / "MP_R51c.pdf")
    with PT._TILE_LOCK:
        PT._renderer._start()
        t0 = time.perf_counter()
        PT.forget(str(book))                 # raises nothing
        PT.forget_under(str(tmp_path))
        took = time.perf_counter() - t0
    assert took < 3.0, took
    assert PT._renderer.proc is None


# ---------------------------------------------------------------------------
# Lows
# ---------------------------------------------------------------------------

def test_the_refusal_follows_the_book_not_its_file(tmp_path, monkeypatch, fresh_renderer):
    """Another book stored at the same path (a book replaced) is not refused
    because the one before was too slow."""
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 1.0)
    monkeypatch.setattr(PT, "_CHILD", R4._SILENT_CHILD)
    book = B._big_book(tmp_path / "MP_R5L.pdf")
    with pytest.raises(PT.TileError):
        PT.tile(990601, 990601, "ab" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 45.0)   # the real renderer, the real deadline
    monkeypatch.setattr(PT, "_CHILD", _REAL_CHILD)
    with pytest.raises(PT.TileError):        # the same book: refused
        PT.tile(990601, 990601, "ab" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    assert PT.tile(990601, 990601, "cd" * 32, str(book), 1, 6000, 4000, 0, 0, 0)


def test_a_composite_tile_on_disk_is_not_served_while_its_map_is_removed(client, tmp_path):
    from app.db import SessionLocal
    from app.models import CompositeMap
    pid, did = B._project("rc6 map removing", B._big_book(tmp_path / "MP_R5M.pdf"))
    mid = R4._fake_map(pid, did)
    url = f"/api/projects/{pid}/composite/{mid}/tiles/3/0/0.png"
    assert client.get(url).status_code == 200
    release = threading.Event()

    def hold():                              # a tile being drawn: the removal waits
        with PT._TILE_LOCK:
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    time.sleep(0.1)
    with SessionLocal() as db:
        m = db.get(CompositeMap, mid)
        drop, _ = R4._in_thread(CS._drop_files, m)
        time.sleep(0.2)
        try:
            during = client.get(url).status_code
        finally:
            release.set()
            holder.join()
            drop.join(10)
    assert during == 404
