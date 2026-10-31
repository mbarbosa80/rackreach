"""5.1.2 - the composite map draws faster, and an update never leaves the
server down because of a task an administrator registered.

Marcelo, 31 Oct 2026: can the maps on the composition screen render faster?
Measured on a 28 MB level: every tile took 1.6 to 2 s whatever the zoom,
because PyMuPDF read the whole composed page again for each square; the tiles
were drawn one at a time, so the first opening of the map took about a minute.
Two changes: the page is kept interpreted (a display list) for the maps used
last, and the zoom levels a map opens at are drawn ahead in the background
once it is composed. The picture must not change by a single pixel.

The same day, the update to 5.1.1 on that laptop could not replace the
watchdog task (registered by an install accepted at the UAC prompt) and
setup.bat stopped before starting the server.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

TMP = tempfile.mkdtemp(prefix="ner-512-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/t512.sqlite3")

from app import composite_service as CS                            # noqa: E402
from app.domain import composite as C                              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _nothing_drawing_ahead(monkeypatch):
    """The suite turns drawing ahead off (conftest.py); these tests are about
    it, so it is on here. Anything still queued is finished first - the worker
    opens pictures and lets kept pages go - and the tests hold the tile lock
    where they inspect what is kept in memory."""
    monkeypatch.setenv("NER_DRAW_AHEAD", "1")
    assert CS.ahead_idle(300)
    yield


def _busy_pdf(path: Path, width: float = 3000.0, height: float = 1900.0) -> str:
    """A vector page dense enough to tell a cut-out from a redraw: grids,
    hatching, circles, text at several sizes, a rotated block."""
    doc = pymupdf.open()
    pg = doc.new_page(width=width, height=height)
    sh = pg.new_shape()
    for i in range(0, int(width), 37):
        sh.draw_line((i, 0), (i * 0.7, height))
    for j in range(0, int(height), 23):
        sh.draw_line((0, j), (width, j * 1.1))
    sh.finish(color=(0.2, 0.2, 0.2), width=0.4)
    for k in range(60):
        sh.draw_circle((50 + k * 47, 300 + (k % 7) * 160), 18 + k % 9)
    sh.finish(color=(0.8, 0.1, 0.1), fill=(0.95, 0.9, 0.2), width=1.2)
    sh.commit()
    for k in range(40):
        pg.insert_text((40 + k * 70, 120 + (k % 5) * 300), f"ROOM {k:03d}",
                       fontsize=6 + k % 11, color=(0, 0, 0.6))
    pg.insert_textbox(pymupdf.Rect(1200, 900, 1700, 1300), "Rotated note " * 20,
                      fontsize=9, rotate=90)
    doc.save(str(path))
    doc.close()
    return str(path)


def _pixels(png: bytes) -> tuple:
    pix = pymupdf.Pixmap(png)
    return pix.width, pix.height, pix.n, bytes(pix.samples)


def _page_tile(path: str, z: int, x: int, y: int) -> bytes:
    """The tile exactly as 5.1.1 cut it: straight from the page."""
    keep = C.DISPLAY_LISTS_KEPT
    C.forget(path)
    C.DISPLAY_LISTS_KEPT = 0
    try:
        return C.render_tile(path, z, x, y)
    finally:
        C.DISPLAY_LISTS_KEPT = keep
        C.forget(path)


# --- 1. the same picture -----------------------------------------------------

@pytest.mark.parametrize("z", [0, 2, 4, 6, 8, 9])
def test_a_tile_from_the_kept_page_is_pixel_for_pixel_the_old_tile(tmp_path, z):
    path = _busy_pdf(tmp_path / "base.pdf")
    nx, ny = C.tiles_across(3000.0, 1900.0, z)
    cells = {(0, 0), (nx - 1, ny - 1), (nx // 2, ny // 2), (nx, ny)}   # (nx, ny): off the map
    with CS._TILE_LOCK:
        old = {c: _page_tile(path, z, *c) for c in cells}
        try:
            for c in cells:
                assert _pixels(C.render_tile(path, z, *c)) == _pixels(old[c]), (z, c)
            assert C._open_docs[path]["dl"] is not None, "the page is kept between tiles"
        finally:
            C.forget(path)


def test_only_the_maps_used_last_keep_their_page(tmp_path):
    paths = [_busy_pdf(tmp_path / f"b{i}.pdf", 800, 600) for i in range(4)]
    with CS._TILE_LOCK:
        try:
            for p in paths:
                C.render_tile(p, 3, 0, 0)
            kept = [p for p in paths if C._open_docs[p]["dl"] is not None]
            assert kept == paths[-C.DISPLAY_LISTS_KEPT:]
            C.render_tile(paths[0], 3, 0, 0)          # used again: kept again
            assert C._open_docs[paths[0]]["dl"] is not None
            C.release_pages()
            assert all(C._open_docs[p]["dl"] is None for p in paths)
            assert _pixels(C.render_tile(paths[1], 3, 0, 0)) == \
                _pixels(_page_tile(paths[1], 3, 0, 0))
        finally:
            for p in paths:
                C.forget(p)
        assert not any(p in C._open_docs for p in paths)


def test_a_fifth_map_closes_the_oldest(tmp_path):
    paths = [_busy_pdf(tmp_path / f"c{i}.pdf", 400, 300) for i in range(5)]
    with CS._TILE_LOCK:
        try:
            for p in paths:
                C.render_tile(p, 2, 0, 0)
            assert paths[0] not in C._open_docs and len(
                [p for p in paths if p in C._open_docs]) == 4
        finally:
            for p in paths:
                C.forget(p)


# --- 2. what is drawn ahead ---------------------------------------------------

def test_the_plan_is_every_tile_of_the_opening_levels_coarse_first():
    plan = C.prerender_plan(10856.0, 6337.0)       # the level measured on 31 Oct
    zs = [z for z, _, _ in plan]
    assert zs == sorted(zs) and zs[0] == 0 and zs[-1] == C.PRERENDER_ZMAX
    for z in range(C.PRERENDER_ZMAX + 1):
        nx, ny = C.tiles_across(10856.0, 6337.0, z)
        assert sorted((x, y) for zz, x, y in plan if zz == z) == \
            sorted((x, y) for x in range(nx) for y in range(ny))
    assert (5, 10, 6) in plan and (5, 11, 0) not in plan


def test_a_very_large_map_is_drawn_ahead_only_up_to_the_cap():
    plan = C.prerender_plan(60000.0, 40000.0)
    assert 0 < len(plan) <= C.PRERENDER_MAX_TILES
    assert max(z for z, _, _ in plan) < C.PRERENDER_ZMAX
    assert C.prerender_plan(10.0, 10.0)[0] == (0, 0, 0)


def _fake_map(tmp_name: str, pid: int = 990512):
    base = CS.project_dir(pid) / f"{tmp_name}.pdf"
    _busy_pdf(base)
    return SimpleNamespace(id=pid, project_id=pid, base_file=base.name,
                           frame={"width": 3000.0, "height": 1900.0})


def test_drawing_ahead_writes_every_planned_tile_and_the_same_pixels():
    cmap = _fake_map("ahead-a")
    base = str(CS.base_path(cmap))
    try:
        n = CS.draw_ahead(cmap)
        assert n == len(C.prerender_plan(3000.0, 1900.0)) > 0
        assert CS.ahead_idle(120)
        tiles = CS._tiles_dir(cmap)
        for z, x, y in C.prerender_plan(3000.0, 1900.0):
            f = tiles / str(z) / f"{x}_{y}.png"
            assert f.exists(), (z, x, y)
        for z, x, y in [(0, 0, 0), (4, 1, 0), (5, 2, 1)]:
            f = tiles / str(z) / f"{x}_{y}.png"
            assert _pixels(f.read_bytes()) == _pixels(_page_tile(base, z, x, y))
        assert not list(tiles.rglob("*.part")), "no half-written tile is left"
    finally:
        with CS._TILE_LOCK:
            C.forget(base)


def test_two_maps_queued_together_are_both_drawn_and_the_worker_lets_go():
    a, b = _fake_map("ahead-b1"), _fake_map("ahead-b2")
    try:
        CS.draw_ahead(a)
        CS.draw_ahead(b)
        assert CS.ahead_idle(180)
        for m in (a, b):
            assert (CS._tiles_dir(m) / str(C.PRERENDER_ZMAX)).is_dir()
        # Idle, the worker ends and the kept pages are let go.
        for _ in range(200):
            if CS._ahead_worker is None:
                break
            threading.Event().wait(0.05)
        assert CS._ahead_worker is None
        assert all(e["dl"] is None for e in C._open_docs.values())
        # A map queued after that starts a new worker.
        c = _fake_map("ahead-b3")
        CS.draw_ahead(c)
        assert CS.ahead_idle(120) and (CS._tiles_dir(c) / "0" / "0_0.png").exists()
    finally:
        with CS._TILE_LOCK:
            for m in (a, b):
                C.forget(str(CS.base_path(m)))


def test_a_picture_replaced_meanwhile_is_not_drawn_on():
    cmap = _fake_map("ahead-gone")
    base = CS.base_path(cmap)
    plan = C.prerender_plan(3000.0, 1900.0)
    base.unlink()
    CS._draw_plan(str(base), CS._tiles_dir(cmap), plan)
    assert not CS._tiles_dir(cmap).exists()


def test_the_switch_turns_drawing_ahead_off(monkeypatch):
    cmap = _fake_map("switched-off")
    monkeypatch.setenv("NER_DRAW_AHEAD", "0")
    assert CS.draw_ahead(cmap) == 0
    assert not CS._tiles_dir(cmap).exists()
    conf = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("NER_DRAW_AHEAD", "0")' in conf


def test_a_map_with_no_picture_queues_nothing():
    assert CS.draw_ahead(SimpleNamespace(id=1, project_id=990512, base_file="",
                                         frame={"width": 10, "height": 10})) == 0
    assert CS.draw_ahead(SimpleNamespace(id=1, project_id=990512, base_file="x.pdf",
                                         frame=None)) == 0


def test_a_deleted_project_takes_its_maps_even_with_a_picture_held_open():
    """Found by the 5.0.0 test of deleting a project once the map was drawn
    ahead: the server held the picture open and Windows would not delete it.
    It was so before 5.1.2 too, for a map someone had just looked at."""
    pid = 990777
    cmap = _fake_map("held", pid)
    base = str(CS.base_path(cmap))
    with CS._TILE_LOCK:
        CS._write_tile(CS._tiles_dir(cmap) / "0" / "0_0.png", C.render_tile(base, 0, 0, 0))
    assert base in C._open_docs                     # held open, as after a look
    CS.draw_ahead(cmap)                             # and still being drawn ahead
    from app import services as S
    gone = S.remove_project_files(pid)
    assert str(CS.settings.composite_dir / f"project_{pid}") in gone
    assert not (CS.settings.composite_dir / f"project_{pid}").exists()
    assert base not in C._open_docs
    assert CS.ahead_idle(60)
    assert not (CS.settings.composite_dir / f"project_{pid}").exists(), \
        "nothing queued writes the folder back"


def test_a_tile_is_written_whole():
    d = CS.project_dir(990512) / "whole"
    p = d / "3" / "1_2.png"
    CS._write_tile(p, b"\x89PNG-test")
    assert p.read_bytes() == b"\x89PNG-test"
    assert [f.name for f in p.parent.iterdir()] == ["1_2.png"]


def test_build_and_leaving_a_sheet_off_both_queue_the_map():
    src = (ROOT / "backend" / "app" / "composite_service.py").read_text(encoding="utf-8")
    build = src[src.index("def build("):src.index("def _compose(")]
    assert build.index("db.commit()") < build.index("draw_ahead(row)")
    off = src[src.index("def set_excluded("):src.index("def delete(")]
    assert off.index("db.commit()") < off.index("draw_ahead(cmap)")
    tile = src[src.index("def tile("):src.index("def _write_tile(")]
    assert "_write_tile(path, png)" in tile and "write_bytes" not in tile


# --- 3. the installer ---------------------------------------------------------

def test_a_watchdog_registered_by_an_administrator_is_kept():
    ps = (ROOT / "tools" / "watchdog-install.ps1").read_text(encoding="utf-8")
    keep = ps.index("IndexOf($vbs")
    assert keep < ps.index('Write-Output "FAILED')
    assert "exit 0" in ps[keep:ps.index('Write-Output "FAILED')]
    assert "schtasks.exe /Query /TN $name /XML" in ps


@pytest.mark.skipif(not (ROOT / "setup.bat").exists(),
                    reason="setup.bat is not copied into the folder it installs")
def test_setup_starts_the_server_even_when_the_tasks_report_a_problem():
    text = (ROOT / "setup.bat").read_text(encoding="utf-8", errors="replace")
    label = text.index("\n:services") + 1                  # the label, not "goto :services"
    block = text[label:text.index("[5/6] Starting the server")]
    assert "autostart.bat reported a problem" in block
    assert "goto :cleanup" not in block, "a task problem must not skip starting the server"
    raw = (ROOT / "setup.bat").read_bytes()
    assert b"\r\n" in raw and not __import__("re").findall(rb"(?<!\r)\n", raw)
