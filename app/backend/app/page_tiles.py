"""The Drawing viewer's sheets in tiles (5.5.0).

Why
---
The viewer used to draw each sheet once, whole, with PDF.js, into one bitmap
of at most 6,000 px, and zoom by enlarging that bitmap. A 36 x 24 in sheet
fits (2.2 px per point). A 900-series master plan does not: VDD_900 (Nov
2026) is 340 x 220 in, so the whole sheet got 0.25 px per point — a blur at
any zoom — and PDF.js had to load the 145 MB book and walk its 330,000 paths
before anything showed. Marcelo, 5 Nov 2026: "quando dá zoom, não renderiza
de novo ou leva muito tempo."

How
---
A sheet larger than the bitmap allows is cut into square tiles by the server,
the way the composite map is (5.0.0): MuPDF interprets the page once into a
display list kept in memory, and each tile renders only its square from it,
at the zoom the screen shows (0.01 to 0.3 s a tile on that book). Tiles are
written to disk and served from there afterwards. The viewer asks only for
the tiles on screen, at the zoom shown, over a coarser layer that fills in
first.

Tiles are derived from the stored book and kept with the composite maps
(``data\\composite\\project_<id>\\pages``): not in a backup, not in a project
package, rebuilt on demand, deleted with the book or the project.
"""

from __future__ import annotations

import atexit
import collections
import math
import os
import pickle
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .composite_service import _TILE_LOCK, project_dir

#: Square tiles of this many pixels; zoom level ``z`` draws 2**z / 64 px per
#: point (z = 6 is 1 px/pt) — the composite map's ladder.
TILE_PX = 512
#: The deepest level: 16 px per point, enough for the viewer's largest zoom
#: (8) on a screen of device pixel ratio 2.
TILE_ZMAX = 10
#: The viewer draws a sheet in tiles when PDF.js could not give it its usual
#: 2.2 px per point within its 6,000 px bitmap — longest side above this many
#: points (about 37.9 in). Kept equal to the viewer's own constants.
TILED_ABOVE_PT = 6000.0 / 2.2
#: Pages whose display list is kept in memory (the ones looked at last). A
#: master plan's list is about 130 MB; the documents themselves stay open.
DISPLAY_LISTS_KEPT = 2
#: Open documents kept.
DOCS_KEPT = 4
#: Longest a tile may take in the renderer, seconds (rc5). Past it the
#: renderer is killed — the next tile starts a new one — and the tile answered
#: as failed: a renderer that never answers (MuPDF looping on a bad page) held
#: every undrawn tile of every book, and every removal, for as long as it did
#: not answer (independent tester, rc4). The slowest tile measured is 8.6 s
#: (the tester's pathological sheet: 600,000 long hairlines on 24,000 x
#: 15,800 pt, z = 1, display list included) in the 2-CPU test container; a
#: 900-series master plan draws a tile in 0.01 to 0.3 s. Five times the
#: slowest leaves room for a slower laptop.
TILE_DEADLINE_S = 45.0
#: Closing a book or letting the display lists go: no MuPDF drawing, quick.
CHORE_DEADLINE_S = 5.0
#: A tile stopped at the deadline is refused at once for this long (seconds)
#: rather than tried again: the viewer asks again at every pan, and each try
#: would hold every other undrawn tile for a whole deadline.
SLOW_TILE_RETRY_S = 120.0
#: Each further try of the same tile that misses the deadline again doubles
#: the wait, up to this (rc7): a tile that never draws in 45 s on this machine
#: held the renderer for 45 s every ~165 s for as long as the sheet was open
#: (independent tester, rc6). 120, 240, 480, 900, 900 ... seconds.
SLOW_TILE_RETRY_MAX_S = 900.0


class TileError(ValueError):
    """No such tile — the message is for a person."""


class TileSlow(TileError):
    """The tile is too slow to draw just now (rc6): answered 503 with
    ``Retry-After: retry_after`` seconds, so the viewer can tell it from a
    tile that does not exist, say so and ask again by itself."""

    def __init__(self, message: str, retry_after: float = SLOW_TILE_RETRY_S) -> None:
        super().__init__(message)
        self.retry_after = max(1, math.ceil(retry_after))


class TileTimeout(TileSlow):
    """The renderer did not answer within its deadline and was stopped."""


def tile_scale(z: int) -> float:
    return (2.0 ** z) / 64.0


def is_tiled(width: float, height: float) -> bool:
    return max(width, height) > TILED_ABOVE_PT


def tiles_dir(project_id: int, drawing_id: int, sha256: str, page: int) -> Path:
    """Where one sheet's tiles live; the book's checksum is in the name, so a
    replaced book never shows tiles of the one before."""
    tag = re.sub(r"[^0-9a-f]", "", (sha256 or "").lower())[:12] or "nosum"
    return project_dir(project_id) / "pages" / f"drawing_{drawing_id}_{tag}" / f"p{page}"


#: Books and projects being removed (rc2). A tile request that started
#: before the removal — it read the book's row and waited for the renderer —
#: is refused, and so is any request while the removal runs: either would open
#: the book again and write its tiles back (independent tester, 5.5.0-rc1:
#: tiles left on disk and the deleted PDF held open, which on Windows keeps
#: it from being deleted). Once the removal is done, a request finds no row;
#: SQLite may give the same id to the next book, which is then served.
_removing_drawings: set[int] = set()
_removing_projects: set[int] = set()
_dropped_drawing_at: dict[int, float] = {}
_dropped_project_at: dict[int, float] = {}
#: perf_counter, not monotonic: on Windows monotonic moves in 15 ms steps, and
#: a request in the same step as the removal read as started before it (rc2).


def _refused(project_id: int, drawing_id: int, started: float) -> bool:
    return (drawing_id in _removing_drawings or project_id in _removing_projects
            or _dropped_drawing_at.get(drawing_id, -1.0) >= started
            or _dropped_project_at.get(project_id, -1.0) >= started)


def drop_drawing(project_id: int, drawing_id: int, stored_path: Optional[str],
                 then: Optional[Callable[[], None]] = None) -> None:
    """A book is being removed: no tile of it until :func:`drop_done`, the
    book closed here (an open file cannot be deleted on Windows) and its
    tiles deleted; ``then`` (its composite maps' files) under the same hold.

    Refused from the moment the removal starts, not once it has the lock
    (rc5), and the lock is taken ahead of every tile waiting for it, once
    (rc6): the removal waits at most for the tile being drawn, which the
    renderer's deadline bounds. Each further take of the lock waited again
    for a tile of another book that took it in between (independent tester,
    rc5: 88 s behind a book whose tiles hang)."""
    base = project_dir(project_id) / "pages"
    _removing_drawings.add(drawing_id)
    _dropped_drawing_at[drawing_id] = time.perf_counter()
    with _TILE_LOCK.first():
        if stored_path:
            forget(stored_path)
        if base.is_dir():
            for d in base.glob(f"drawing_{drawing_id}_*"):
                shutil.rmtree(d, ignore_errors=True)
        if then is not None:
            then()


def drop_project(project_id: int, uploads_folder: str,
                 then: Optional[Callable[[], None]] = None) -> None:
    """A project is being removed: no tile of it until :func:`drop_done`, its
    books closed, then ``then`` (its folders deleted) under the same, single
    hold of the lock (rc6)."""
    _removing_projects.add(project_id)
    _dropped_project_at[project_id] = time.perf_counter()
    with _TILE_LOCK.first():
        forget_under(uploads_folder)
        if then is not None:
            then()


def drop_done(*, drawing_id: Optional[int] = None, project_id: Optional[int] = None) -> None:
    """The removal is finished (row and file gone). Only the sets change —
    without the tile lock (rc6): a tile reads them without it, and taking the
    lock here waited for whatever tile held it."""
    if drawing_id is not None:
        _removing_drawings.discard(drawing_id)
    if project_id is not None:
        _removing_projects.discard(project_id)


# ---------------------------------------------------------------------------
# Open pages (callers hold _TILE_LOCK)
# ---------------------------------------------------------------------------

_docs: "collections.OrderedDict[str, object]" = collections.OrderedDict()
_lists: "collections.OrderedDict[tuple[str, int], object]" = collections.OrderedDict()


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _display_list(path: str, page: int):
    import pymupdf
    k = _key(path)
    dl = _lists.get((k, page))
    if dl is not None:
        _lists.move_to_end((k, page))
        return dl
    doc = _docs.get(k)
    if doc is None:
        doc = pymupdf.open(path)
        _docs[k] = doc
        while len(_docs) > DOCS_KEPT:
            old, od = _docs.popitem(last=False)
            for lk in [x for x in _lists if x[0] == old]:
                del _lists[lk]
            od.close()
    else:
        _docs.move_to_end(k)
    if not 1 <= page <= doc.page_count:
        raise TileError("no such page")
    dl = doc[page - 1].get_displaylist()
    _lists[(k, page)] = dl
    while len(_lists) > DISPLAY_LISTS_KEPT:
        _lists.popitem(last=False)
    return dl


def _forget_here(path: str) -> None:
    """Close one book in this process (callers hold _TILE_LOCK)."""
    k = _key(path)
    for lk in [x for x in _lists if x[0] == k]:
        del _lists[lk]
    doc = _docs.pop(k, None)
    if doc is not None:
        doc.close()


def _forget_under_here(folder: str) -> None:
    root = _key(folder).rstrip("\\/") + os.sep
    for k in [k for k in _docs if k.startswith(root)]:
        _forget_here(k)


def _chore(request: tuple) -> None:
    """Ask the renderer to close or let go of something. A renderer that
    stopped, or did not answer and was stopped, holds nothing any more: the
    chore is done either way (rc6 — a removal failed on it)."""
    try:
        _renderer.ask(request, start=False)
    except TileError:
        pass


def forget(path: str) -> None:
    """Close one book, here and in the renderer (callers hold _TILE_LOCK)."""
    _forget_here(path)
    _chore(("forget", path))


def forget_under(folder: str) -> None:
    """Close every book kept open under ``folder`` (callers hold _TILE_LOCK)."""
    _forget_under_here(folder)
    _chore(("forget_under", folder))


def release() -> None:
    """Let go of the kept display lists (the documents stay open, cheap)."""
    with _TILE_LOCK:
        _lists.clear()
        _chore(("release",))


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def tiles_across(width: float, height: float, z: int) -> tuple[int, int]:
    span = TILE_PX / tile_scale(z)
    return max(1, math.ceil(width / span)), max(1, math.ceil(height / span))


def render_tile(path: str, page: int, width: float, height: float,
                z: int, x: int, y: int) -> bytes:
    """One PNG tile of a sheet, drawn by the renderer process (rc4) — or here
    when it cannot run. Callers hold _TILE_LOCK."""
    args = (path, page, width, height, z, x, y)
    png = _renderer.ask(("tile",) + args)
    if png is _NOT_RUNNING:
        return _render_here(*args)
    return png


def _render_here(path: str, page: int, width: float, height: float,
                 z: int, x: int, y: int) -> bytes:
    """One PNG tile of a sheet (displayed coordinates, the viewer's). The
    last row and column stop at the edge of the sheet, so they are narrower."""
    import pymupdf
    s = tile_scale(z)
    span = TILE_PX / s
    clip = pymupdf.Rect(x * span, y * span, min((x + 1) * span, width),
                        min((y + 1) * span, height))
    if clip.is_empty:
        raise TileError("no such tile")
    dl = _display_list(path, page)
    pix = dl.get_pixmap(matrix=pymupdf.Matrix(s, s), clip=clip, alpha=False)
    return pix.tobytes("png")


def _write(path: Path, png: bytes) -> None:
    """Whole or not at all: a tile that exists is read without the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.part")
    tmp.write_bytes(png)
    os.replace(tmp, path)


def _tile_path(project_id: int, drawing_id: int, sha256: str, page: int,
               width: float, height: float, z: int, x: int, y: int) -> Path:
    if not 0 <= z <= TILE_ZMAX or x < 0 or y < 0:
        raise TileError("no such tile")
    nx, ny = tiles_across(width, height, z)
    if x >= nx or y >= ny:
        raise TileError("no such tile")
    return tiles_dir(project_id, drawing_id, sha256, page) / str(z) / f"{x}_{y}.png"


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:                     # not drawn yet, or deleted by a removal
        return None


#: Tiles stopped at the deadline: (book's checksum, page, z, x, y) -> (when,
#: the wait) (rc5; by the checksum since rc6 — a book replaced at the same
#: path is another book; the wait grows since rc7). Forgotten once drawn.
_too_slow: dict[tuple, tuple[float, float]] = {}


def _slow_key(sha256: str, stored_path: str, page: int, z: int, x: int, y: int) -> tuple:
    return (sha256 or _key(stored_path or ""), page, z, x, y)


def _next_wait(key: tuple, now: float) -> float:
    """The wait after this try missed the deadline: the first one, or twice
    the last when the try came soon after the last wait ended."""
    last = _too_slow.get(key)
    if last is None or now - (last[0] + last[1]) > SLOW_TILE_RETRY_MAX_S:
        return SLOW_TILE_RETRY_S
    return min(SLOW_TILE_RETRY_MAX_S, last[1] * 2)


def _check_slow(key: tuple, now: float) -> None:
    when, wait = _too_slow.get(key, (-1e18, 0.0))
    left = wait - (now - when)
    if left > 0:
        raise TileSlow(f"this part of the sheet took longer than {TILE_DEADLINE_S:.0f} s to "
                       f"draw; it is tried again in {math.ceil(left)} s", retry_after=left)


def cached_tile(project_id: int, drawing_id: int, sha256: str, page: int,
                width: float, height: float, z: int, x: int, y: int) -> Optional[bytes]:
    """The tile when it is on disk, else None — without the lock or the
    renderer (rc5): a tile drawn before used to wait for its turn behind
    every undrawn tile queued before it (22 s, independent tester, rc4). A
    book or project being removed serves nothing; a tile refused for being
    too slow is refused here too, without a turn in the drawing queue."""
    # Refused before the path is worked out (rc7): working it out makes the
    # project's folder, which a removal has just moved aside.
    now = time.perf_counter()
    if _refused(project_id, drawing_id, now):
        raise TileError("the book was removed")
    path = _tile_path(project_id, drawing_id, sha256, page, width, height, z, x, y)
    png = _read(path)
    if png is None and sha256:
        _check_slow(_slow_key(sha256, "", page, z, x, y), now)
    return png


def tile(project_id: int, drawing_id: int, sha256: str, stored_path: str, page: int,
         width: float, height: float, z: int, x: int, y: int) -> bytes:
    """A tile, from disk when it was drawn before."""
    started = time.perf_counter()
    if _refused(project_id, drawing_id, started):
        raise TileError("the book was removed")
    path = _tile_path(project_id, drawing_id, sha256, page, width, height, z, x, y)
    png = _read(path)
    if png is not None:
        return png
    if not stored_path or not Path(stored_path).exists():
        raise TileError("the stored file is missing")
    slow_key = _slow_key(sha256, stored_path, page, z, x, y)
    _check_slow(slow_key, started)
    with _TILE_LOCK:
        if _refused(project_id, drawing_id, started):
            raise TileError("the book was removed")
        png = _read(path)               # drawn while this request waited
        if png is not None:
            return png
        if not Path(stored_path).exists():
            raise TileError("the stored file is missing")
        try:
            png = render_tile(stored_path, page, width, height, z, x, y)
        except TileTimeout as exc:
            now = time.perf_counter()
            wait = _next_wait(slow_key, now)
            _too_slow[slow_key] = (now, wait)
            exc.retry_after = max(1, math.ceil(wait))
            raise
        _too_slow.pop(slow_key, None)
        _write(path, png)
    return png


# ---------------------------------------------------------------------------
# The renderer process (5.5.0, rc4)
# ---------------------------------------------------------------------------
#
# MuPDF keeps Python's interpreter lock for the whole of a tile. Drawn in the
# server's process, a tile that takes seconds held everything else the server
# does for those seconds - /healthz answered in 37 s behind a burst of slow
# tiles, even on threads of their own (independent tester, rc3). The tiles
# are drawn in a child interpreter instead, which keeps the open books and
# their display lists; the server only waits on a pipe, which costs it
# nothing. A book that makes MuPDF crash takes the renderer down, not the
# server: the next tile starts a new one. ``NER_INPROCESS=1`` draws here.

_NOT_RUNNING = object()

_CHILD = r"""
import pickle, struct, sys
_in, _out = sys.stdin.buffer, sys.stdout.buffer
n = struct.unpack('>I', _in.read(4))[0]
sys.path[:0] = [p for p in pickle.loads(_in.read(n)) if p not in sys.path]
sys.stdout = sys.stderr
from app import page_tiles
page_tiles.serve(_in, _out)
"""


def _send(stream, obj) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(struct.pack(">I", len(data)))
    stream.write(data)
    stream.flush()


def _recv(stream):
    head = stream.read(4)
    if len(head) < 4:
        raise EOFError
    (n,) = struct.unpack(">I", head)
    body = stream.read(n)
    if len(body) < n:
        raise EOFError
    return pickle.loads(body)


def serve(inp, out) -> None:
    """The renderer's loop (in the child): one request, one answer."""
    ops = {"tile": _render_here, "forget": _forget_here,
           "forget_under": _forget_under_here, "release": lambda: _lists.clear()}
    while True:
        try:
            req = _recv(inp)
        except (EOFError, OSError):
            return                                      # the server went away
        try:
            try:
                answer = ("ok", ops[req[0]](*req[1:]))
            except TileError as exc:
                answer = ("tile_error", str(exc))
            except Exception as exc:                    # noqa: BLE001 — told to the server
                answer = ("error", f"{type(exc).__name__}: {exc}")
            _send(out, answer)
        except OSError:
            # The server went away while this tile was drawn: nobody to tell
            # (rc4 tried to report the broken pipe down the broken pipe and
            # left two tracebacks in the server log).
            return


class _Renderer:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.failed_to_start = False

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc is not None and self.proc.poll() is None else None

    def _start(self) -> bool:
        if os.environ.get("NER_INPROCESS") == "1" or self.failed_to_start:
            return False
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        backend = str(Path(__file__).resolve().parents[1])
        paths = [backend] + [os.path.abspath(p) for p in sys.path if p]
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-c", _CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None, creationflags=creation, cwd=backend, env=dict(os.environ))
            _send(self.proc.stdin, paths)
        except OSError:
            self.proc = None
            self.failed_to_start = True                 # draw here from now on
            return False
        # Waited for as soon as it ends (rc5): a renderer that died while idle
        # stayed a <defunct> process until the next undrawn tile.
        threading.Thread(target=_reap, args=(self.proc,), name="ner-renderer-reaper",
                         daemon=True).start()
        return True

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:                           # noqa: BLE001
                pass

    def ask(self, request: tuple, *, start: bool = True):
        """Send one request (callers hold _TILE_LOCK). ``_NOT_RUNNING`` when
        there is no renderer and ``start`` is False, or it cannot start.

        Answered within :data:`TILE_DEADLINE_S` (a tile) or
        :data:`CHORE_DEADLINE_S` (anything else), or the renderer is killed,
        which ends the wait on the pipe, and :class:`TileTimeout` raised."""
        if self.proc is None or self.proc.poll() is not None:
            self.proc = None
            if not start or not self._start():
                return _NOT_RUNNING
        proc = self.proc
        deadline = TILE_DEADLINE_S if request[0] == "tile" else CHORE_DEADLINE_S
        # An answer already on its way at the deadline is waited for (rc6),
        # a little longer, instead of killing the renderer with the books and
        # display lists it holds: the tile was drawn in time.
        guard = threading.Lock()
        state = {"answering": False, "killed": False, "done": False}
        timers: list[threading.Timer] = []

        def _kill() -> None:
            with guard:
                if state["done"]:
                    return
                state["killed"] = True
            try:
                proc.kill()
            except Exception:                           # noqa: BLE001 — already gone
                pass

        def _expire() -> None:
            with guard:
                if state["done"]:
                    return
                answering = state["answering"]
            if answering:
                _arm(CHORE_DEADLINE_S, _kill)
            else:
                _kill()

        def _arm(seconds: float, fn) -> None:
            t = threading.Timer(seconds, fn)
            t.daemon = True
            timers.append(t)
            t.start()

        _arm(deadline, _expire)
        try:
            _send(proc.stdin, request)
            head = proc.stdout.read(4)
            with guard:
                state["answering"] = not state["killed"]
            if len(head) < 4:
                raise EOFError
            (n,) = struct.unpack(">I", head)
            body = proc.stdout.read(n)
            if len(body) < n:
                raise EOFError
            kind, value = pickle.loads(body)
        except (EOFError, OSError, pickle.UnpicklingError, ValueError):
            self.stop()
            if state["killed"]:
                raise TileTimeout(f"the renderer took longer than {deadline:.0f} s and was "
                                  "stopped; the next tile starts a new one")
            raise TileError("the renderer stopped; asking again starts a new one")
        finally:
            with guard:
                state["done"] = True
            for t in list(timers):
                t.cancel()
        if kind == "tile_error":
            raise TileError(value)
        if kind == "error":
            raise RuntimeError(value)
        return value


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait()
    except Exception:                                   # noqa: BLE001
        pass


_renderer = _Renderer()
atexit.register(_renderer.stop)
