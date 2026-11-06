"""5.5.0-rc5 — what the independent tester of rc4 found in the tiles.

* R4-5: a tile whose client had hung up was drawn anyway — behind the
  ``@app.middleware("http")`` that names the signed-in person,
  ``request.is_disconnected()`` never turned True;
* R4-3: a renderer that never answers held every undrawn tile, and every
  removal of a book or a project, for as long as it did not answer;
* R4-1: a tile already on disk waited behind the undrawn tiles queued before
  it, on the tiles' three threads;
* R4-4: a removal waited for tiles that took the lock while it waited;
* R4-2b: the marked-PDF export's child process unpickled its arguments before
  it knew where ``app`` is — started from the installation's root without
  PYTHONPATH (``mac/start.command``) the export failed.

The PDFs here are synthetic — never a real venue document in a test.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest

import tests.test_big_sheets_120 as B                              # noqa: E402 — sets the data folder
from app import page_tiles as PT                                   # noqa: E402
from app import services as S                                      # noqa: E402
from app.api import tile_runner as TR                              # noqa: E402
from app.db import SessionLocal                                    # noqa: E402
from tests.test_wireless_tie_250 import client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
POSIX = sys.platform != "win32"


def _url(pid: int, did: int, z: int, x: int, y: int) -> str:
    return f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/{z}/{x}/{y}.png"


class _Busy:
    """Every thread of the tile pool taken by a job that waits for
    :meth:`release` — what a burst of slow tiles does. Released on its own
    after ``limit`` seconds, so a test of the old code fails instead of
    hanging."""

    def __init__(self, limit: float = 6.0) -> None:
        self.ev = threading.Event()
        self.futs = [TR._pool.submit(self.ev.wait, 30) for _ in range(TR.TILE_THREADS)]
        self.timer = threading.Timer(limit, self.ev.set)
        self.timer.start()
        time.sleep(0.2)

    def release(self) -> None:
        self.ev.set()
        self.timer.cancel()
        for f in self.futs:
            f.result(30)


def _asgi_get(path: str, cookie: str, *, gone: bool, timeout: float = 30.0) -> int:
    """GET ``path`` through the whole application, as uvicorn calls it, with
    a client that has hung up (``gone``) right after sending the request."""
    from app.main import app
    sent: list[dict] = []

    async def go():
        state = {"asked": False}

        async def receive():
            if not state["asked"]:
                state["asked"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            if gone:
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "client": ("127.0.0.1", 50000),
                 "server": ("testserver", 80),
                 "headers": [(b"host", b"testserver"),
                             (b"cookie", f"ner_session={cookie}".encode())]}
        await asyncio.wait_for(app(scope, receive, send), timeout)

    asyncio.run(go())
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _count_draws(monkeypatch) -> list:
    drawn: list = []
    real = PT.render_tile

    def counting(*a, **k):
        drawn.append(a)
        return real(*a, **k)

    monkeypatch.setattr(PT, "render_tile", counting)
    return drawn


# ---------------------------------------------------------------------------
# R4-5 — a request gone before its turn is not drawn
# ---------------------------------------------------------------------------

def test_a_tile_whose_client_is_gone_before_its_turn_is_not_drawn(client, tmp_path, monkeypatch):
    """Through the real application: its middleware, the tile guard, the
    route and ``run_tile``. rc4 drew all 96 abandoned tiles of the tester."""
    pid, did = B._project("rc5 gone", B._big_book(tmp_path / "MP_R45.pdf"))
    drawn = _count_draws(monkeypatch)
    busy = _Busy(limit=6.0)
    t0 = time.perf_counter()
    try:
        status = _asgi_get(_url(pid, did, 6, 2, 2), client.cookies.get("ner_session"), gone=True)
    finally:
        took = time.perf_counter() - t0
        busy.release()
    time.sleep(1.0)                          # a cancelled job must stay cancelled
    on_disk = PT.tiles_dir(pid, did, B._sha(did), 1) / "6" / "2_2.png"
    assert (status, drawn, on_disk.exists()) == (499, [], False), (status, len(drawn), took)
    assert took < 5.0, took


def test_a_tile_whose_client_stays_is_drawn_after_the_wait(client, tmp_path):
    pid, did = B._project("rc5 stays", B._big_book(tmp_path / "MP_R45b.pdf"))
    busy = _Busy(limit=2.5)
    try:
        status = _asgi_get(_url(pid, did, 6, 3, 3), client.cookies.get("ner_session"), gone=False)
    finally:
        busy.release()
    assert status == 200


def test_the_signed_in_name_still_reaches_the_audit_log(client):
    """The middleware that names the person is now a plain ASGI one."""
    from sqlalchemy import select
    from app.models import AuditLog
    pid = client.post("/api/projects", json={"name": "rc5 actor", "venue_code": "R45"}).json()["id"]
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    with SessionLocal() as db:
        row = db.execute(select(AuditLog).where(AuditLog.action == "project.deleted",
                                                AuditLog.entity_id == pid)).scalar_one()
    assert row.actor == "tester"
    assert S.CURRENT_ACTOR.get() is None


def test_no_http_middleware_hides_a_hang_up():
    """``@app.middleware("http")`` (Starlette's BaseHTTPMiddleware) is what
    hid the hang-up: none is left."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from app.main import app
    assert not [m for m in app.user_middleware if m.cls is BaseHTTPMiddleware]


# ---------------------------------------------------------------------------
# R4-3 — a renderer that never answers
# ---------------------------------------------------------------------------

_SILENT_CHILD = r"""
import struct, sys, time
_in = sys.stdin.buffer
n = struct.unpack('>I', _in.read(4))[0]; _in.read(n)
while True:
    head = _in.read(4)
    if len(head) < 4:
        break
    _in.read(struct.unpack('>I', head)[0])
    time.sleep(3600)
"""


@pytest.fixture
def fresh_renderer(monkeypatch):
    monkeypatch.delenv("NER_INPROCESS", raising=False)
    with PT._TILE_LOCK:
        PT._renderer.stop()
    if hasattr(PT, "_too_slow"):
        PT._too_slow.clear()
    yield
    with PT._TILE_LOCK:
        PT._renderer.stop()
    if hasattr(PT, "_too_slow"):
        PT._too_slow.clear()


def _in_thread(fn, *args):
    out: dict = {}

    def run():
        t0 = time.perf_counter()
        try:
            out["value"] = fn(*args)
        except Exception as exc:                            # noqa: BLE001
            out["error"] = exc
        out["took"] = time.perf_counter() - t0

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, out


def test_a_renderer_that_never_answers_is_stopped_at_the_deadline(tmp_path, monkeypatch, fresh_renderer):
    """A fake renderer that reads the request and never answers (MuPDF
    looping on a bad page). rc4 waited on the pipe for ever."""
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 1.5, raising=False)
    monkeypatch.setattr(PT, "_CHILD", _SILENT_CHILD)
    book = B._big_book(tmp_path / "MP_R43.pdf")
    th, out = _in_thread(PT.tile, 990101, 990101, "ab" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    th.join(8.0)
    stuck = th.is_alive()
    if stuck:                                # rc4: unstick it, then fail
        PT._renderer.proc.kill()
        th.join(10)
    assert not stuck, "the tile waited for the renderer with no limit"
    assert isinstance(out.get("error"), PT.TileError), out
    assert out["took"] < 4.0, out
    assert PT._renderer.proc is None         # killed and discarded
    # The same tile is not tried again at once (it would hold the lock for a
    # whole deadline each time the viewer asks): refused straight away.
    t0 = time.perf_counter()
    with pytest.raises(PT.TileError):
        PT.tile(990101, 990101, "ab" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    assert time.perf_counter() - t0 < 0.5
    # Another tile starts a new renderer, a real one this time.
    monkeypatch.undo()
    monkeypatch.delenv("NER_INPROCESS", raising=False)
    assert PT.tile(990101, 990101, "ab" * 32, str(book), 1, 6000, 4000, 6, 5, 3)
    assert PT._renderer.pid not in (None, os.getpid())


@pytest.mark.skipif(not POSIX, reason="SIGSTOP is POSIX")
def test_a_frozen_renderer_holds_a_removal_at_most_one_deadline(tmp_path, monkeypatch, fresh_renderer):
    """The tester's repro: SIGSTOP the renderer, then ask for a tile of one
    book and remove another project's book. rc4: both waited until SIGCONT."""
    import signal
    monkeypatch.setattr(PT, "TILE_DEADLINE_S", 2.0, raising=False)
    book = B._big_book(tmp_path / "MP_R43b.pdf")
    other = B._big_book(tmp_path / "MP_R43c.pdf")
    with PT._TILE_LOCK:                      # a renderer up, the page read
        PT.render_tile(str(book), 1, 6000, 4000, 0, 0, 0)
    pid = PT._renderer.pid
    os.kill(pid, signal.SIGSTOP)
    try:
        tile_th, tile_out = _in_thread(PT.tile, 990201, 990201, "cd" * 32, str(book), 1,
                                       6000, 4000, 6, 1, 1)
        time.sleep(0.3)
        drop_th, drop_out = _in_thread(PT.drop_drawing, 990301, 990301, str(other))
        drop_th.join(6.0)
        tile_th.join(6.0)
        stuck = drop_th.is_alive() or tile_th.is_alive()
    finally:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
    drop_th.join(30)
    tile_th.join(30)
    PT.drop_done(drawing_id=990301)
    assert not stuck, "a frozen renderer held the removal and the tile"
    assert "error" not in drop_out and drop_out["took"] < 2.0 + 2.0, drop_out
    assert isinstance(tile_out.get("error"), PT.TileError), tile_out
    assert PT._renderer.pid != pid


def test_an_idle_renderer_that_dies_is_reaped(tmp_path, monkeypatch, fresh_renderer):
    """Low (OK-2): a renderer that died while idle stayed a zombie until the
    next undrawn tile."""
    book = B._big_book(tmp_path / "MP_R43d.pdf")
    with PT._TILE_LOCK:
        PT.render_tile(str(book), 1, 6000, 4000, 0, 0, 0)
    proc = PT._renderer.proc
    proc.kill()
    end = time.time() + 5
    while time.time() < end and proc.returncode is None:
        time.sleep(0.05)
    assert proc.returncode is not None       # waited for: no <defunct> left


def test_the_renderer_leaves_quietly_when_the_server_is_gone():
    """Low (OK-4): it tried to report the broken pipe down the broken pipe and
    left a two-exception traceback in the server log."""
    import io

    class Gone(io.RawIOBase):
        def writable(self):
            return True

        def write(self, b):
            raise BrokenPipeError(32, "Broken pipe")

    inp = io.BytesIO()
    PT._send(inp, ("release",))
    inp.seek(0)
    PT.serve(inp, Gone())                    # returns, raises nothing


# ---------------------------------------------------------------------------
# R4-1 — a tile on disk does not wait for the drawing queue
# ---------------------------------------------------------------------------

def test_a_tile_on_disk_is_served_while_the_tile_threads_are_busy(client, tmp_path):
    pid, did = B._project("rc5 disk hit", B._big_book(tmp_path / "MP_R41.pdf"))
    assert client.get(_url(pid, did, 2, 0, 0)).status_code == 200      # now on disk
    busy = _Busy(limit=6.0)
    try:
        t0 = time.perf_counter()
        r = client.get(_url(pid, did, 2, 0, 0))
        took = time.perf_counter() - t0
    finally:
        busy.release()
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert took < 2.0, f"a tile on disk waited {took:.1f} s behind the drawing queue"


def test_a_tile_on_disk_is_served_while_the_renderer_holds_the_lock(client, tmp_path):
    pid, did = B._project("rc5 disk hit lock", B._big_book(tmp_path / "MP_R41b.pdf"))
    assert client.get(_url(pid, did, 1, 0, 0)).status_code == 200
    release = threading.Event()

    def hold():
        with PT._TILE_LOCK:
            release.wait(6)

    th = threading.Thread(target=hold)
    th.start()
    try:
        time.sleep(0.2)
        t0 = time.perf_counter()
        r = client.get(_url(pid, did, 1, 0, 0))
        took = time.perf_counter() - t0
    finally:
        release.set()
        th.join()
    assert r.status_code == 200 and took < 2.0, took


def test_a_tile_on_disk_of_a_book_being_removed_is_not_served(client, tmp_path):
    pid, did = B._project("rc5 disk removing", B._big_book(tmp_path / "MP_R41c.pdf"))
    assert client.get(_url(pid, did, 2, 0, 0)).status_code == 200
    PT._removing_drawings.add(did)
    try:
        assert client.get(_url(pid, did, 2, 0, 0)).status_code == 404
    finally:
        PT._removing_drawings.discard(did)
    PT._removing_projects.add(pid)
    try:
        assert client.get(_url(pid, did, 2, 0, 0)).status_code == 404
    finally:
        PT._removing_projects.discard(pid)
    assert client.get(_url(pid, did, 2, 0, 0)).status_code == 200


def test_a_tile_on_disk_still_needs_a_session(client, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    pid, did = B._project("rc5 disk auth", B._big_book(tmp_path / "MP_R41d.pdf"))
    assert client.get(_url(pid, did, 2, 0, 0)).status_code == 200
    assert TestClient(app).get(_url(pid, did, 2, 0, 0)).status_code == 401


def _fake_map(pid: int, did: int) -> int:
    """A composite map row with one tile on disk — the map's own picture is
    not needed to serve a tile already drawn."""
    from app import composite_service as CS
    from app.models import CompositeMap
    with SessionLocal() as db:
        m = CompositeMap(project_id=pid, drawing_id=did, group_key="R41", label="R41",
                         mpu=0.01, frame={"x0": 0, "y0": 0, "width": 6000, "height": 4000},
                         sheets=[], base_file="r41_base.pdf")
        db.add(m)
        db.commit()
        tile = CS._tiles_dir(m) / "3" / "0_0.png"
        tile.parent.mkdir(parents=True, exist_ok=True)
        tile.write_bytes(pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 8, 8), False).tobytes("png"))
        return m.id


def test_a_composite_tile_on_disk_is_served_while_the_tile_threads_are_busy(client, tmp_path):
    pid, did = B._project("rc5 composite hit", B._big_book(tmp_path / "MP_R41e.pdf"))
    mid = _fake_map(pid, did)
    url = f"/api/projects/{pid}/composite/{mid}/tiles/3/0/0.png"
    busy = _Busy(limit=6.0)
    try:
        t0 = time.perf_counter()
        r = client.get(url)
        took = time.perf_counter() - t0
    finally:
        busy.release()
    assert r.status_code == 200 and took < 2.0, (r.status_code, took)
    PT._removing_projects.add(pid)
    try:
        assert client.get(url).status_code == 404
    finally:
        PT._removing_projects.discard(pid)


# ---------------------------------------------------------------------------
# R4-4 — a removal goes before the tiles waiting for the lock
# ---------------------------------------------------------------------------

def test_a_removal_goes_before_the_tiles_waiting_for_the_lock(tmp_path, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(PT, "forget", lambda path: order.append("removal"))
    release = threading.Event()

    def drawing():                            # the tile being drawn
        with PT._TILE_LOCK:
            release.wait(10)

    def waiting(i):                           # tiles queued behind it
        with PT._TILE_LOCK:
            order.append(f"tile{i}")

    first = threading.Thread(target=drawing)
    first.start()
    time.sleep(0.1)
    tiles = [threading.Thread(target=waiting, args=(i,)) for i in range(3)]
    for th in tiles:
        th.start()
        time.sleep(0.05)
    removal = threading.Thread(target=PT.drop_drawing, args=(990401, 990401, str(tmp_path / "x.pdf")))
    removal.start()
    time.sleep(0.2)
    # Refused as soon as the removal starts, not once it has the lock.
    assert 990401 in PT._removing_drawings
    release.set()
    for th in [first, removal, *tiles]:
        th.join(10)
    PT.drop_done(drawing_id=990401)
    assert order[0] == "removal", order


# ---------------------------------------------------------------------------
# R4-2b — the child process finds ``app`` however the server was started
# ---------------------------------------------------------------------------

def test_the_child_finds_app_when_the_server_runs_from_the_root_without_pythonpath():
    """``cd ROOT; python3 -s tools/serve.py`` — mac/start.command — with no
    PYTHONPATH: serve.py puts ``backend`` on sys.path itself, and the child
    started from the root cannot import ``app`` until it is told where."""
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'backend')!r})\n"
        "from app import isolate\n"
        "from app.page_tiles import TileError\n"
        "print(isolate.run('builtins.repr', TileError('carried across')))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NER_INPROCESS")}
    env["PYTHONNOUSERSITE"] = "1"
    r = subprocess.run([sys.executable, "-s", "-c", script], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "carried across" in r.stdout


def test_the_marked_pdf_falls_back_when_the_child_cannot_import(monkeypatch, tmp_path):
    """Should a child still fail to import the application, the marked PDF
    is made in this process — the function never ran over there."""
    from app import isolate
    from app.api import routes as R
    calls: list = []

    def no_app(*a, **k):
        raise isolate.IsolatedError("ModuleNotFoundError", "No module named 'app'")

    monkeypatch.setattr(isolate, "run", no_app)
    import app.domain.reports as REP
    monkeypatch.setattr(REP, "export_marked_pdf_job",
                        lambda src, out, progress=None, **kw: calls.append(src) or {"off_page": [1]})
    assert R._marked_pdf("a.pdf", str(tmp_path / "b.pdf")) == [1]
    assert calls == ["a.pdf"]
    # Any other failure in the child is a failure, never run again here (a book
    # that crashed the child would crash the server).

    def crashed(*a, **k):
        raise isolate.IsolatedError("ChildExit", "the reader process ended without a result")

    monkeypatch.setattr(isolate, "run", crashed)
    with pytest.raises(isolate.IsolatedError):
        R._marked_pdf("a.pdf", str(tmp_path / "b.pdf"))
    assert calls == ["a.pdf"]
