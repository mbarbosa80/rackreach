"""Map 1.2.0 (from RackReach 5.5.0) — the 900-series master plans: read without holding every stroke, shown in tiles.

Marcelo, 5 Nov 2026, on VDD_900_CKT_O.pdf (four sheets of 340 x 220 in,
145 MB): the upload took a long time (the import ran 37 minutes, the NER
placing 10), and in the viewer "quando dá zoom, não renderiza de novo ou leva
muito tempo". Two causes, two changes:

* the import walked ``page.get_drawings()`` — 330,000 paths, 2 GB — and built
  a leader index of all 2.7 million thin strokes. The strokes are now read one
  at a time and only those that can matter are kept (``domain/linework.py``,
  ``pdf_ingest.page_strokes_and_rooms``). What is found must be exactly what
  the full read finds: that is what most of these tests check;
* the viewer drew a sheet into one bitmap of at most 6,000 px and enlarged
  it. A sheet larger than that now comes in tiles from the server, at the zoom
  shown (``app/page_tiles.py``, ``components/PageTiles.tsx``).

The PDFs here are synthetic — never a real venue document in a test.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pymupdf
import pytest

TMP = tempfile.mkdtemp(prefix="ner-map120-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/tmap120.sqlite3")

from app import page_tiles as PT                                   # noqa: E402
from app import services as S                                      # noqa: E402
from app.db import SessionLocal, init_db                           # noqa: E402
from app.domain import pdf_ingest as P                             # noqa: E402
from app.domain.linework import stream_drawings                    # noqa: E402
from app.models import Project                                     # noqa: E402
from tests.test_key_plans_drawn_rooms_2130 import _arrow, _label, make_book  # noqa: E402
from tests.test_ner_sources_2150 import make_3x_book               # noqa: E402
from tests.test_wireless_tie_250 import client  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
BLACK = (0, 0, 0)


# ---------------------------------------------------------------------------
# Reading the strokes one at a time gives exactly get_drawings()
# ---------------------------------------------------------------------------

def _mixed_sheet(doc, rotation: int):
    """Every kind of path a book has: fill + stroke of one path (PDF ``B``,
    which the list form merges into one "fs" path), fills, strokes, curves,
    rectangles, dashes, a fill right before an unrelated stroke."""
    p = doc.new_page(width=900, height=600)
    sh = p.new_shape()
    sh.draw_rect(pymupdf.Rect(50, 50, 120, 90))
    sh.finish(color=(1, 0, 0), fill=(0, 0, 1), width=0.5)            # fs
    sh.draw_polyline([(200, 200), (205, 190), (210, 200)])
    sh.finish(color=BLACK, fill=BLACK, width=0.24, closePath=True)  # arrowhead-like fs
    sh.draw_rect(pymupdf.Rect(300, 50, 340, 70))
    sh.finish(fill=(0, 1, 0))                                       # fill only
    sh.draw_line((300, 80), (500, 80))
    sh.finish(color=(0, 0, 0), width=0.2, dashes="[3 2] 0")         # unrelated stroke after it
    sh.draw_bezier((100, 300), (150, 250), (200, 350), (250, 300))
    sh.finish(color=(0.2, 0.2, 0.2), width=1.5)
    sh.draw_circle((600, 300), 40)
    sh.finish(color=(0, 0, 0), fill=(1, 1, 0), width=0.3)
    sh.draw_quad(pymupdf.Quad((700, 100), (780, 100), (700, 160), (790, 170)))
    sh.finish(color=(0, 0, 1), width=0.4)
    sh.commit()
    p.set_rotation(rotation)
    return p


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_streamed_paths_are_get_drawings_path_for_path(tmp_path, rotation):
    doc = pymupdf.open()
    _mixed_sheet(doc, rotation)
    path = tmp_path / f"mixed{rotation}.pdf"
    doc.save(str(path))
    page = pymupdf.open(str(path))[0]
    full = page.get_drawings()
    streamed, count = stream_drawings(page)
    assert count == len(full) == len(streamed)
    assert any(d["type"] == "fs" for d in full), "the book must exercise the fill+stroke merge"
    for a, b in zip(full, streamed):
        assert a == b


def test_keep_sees_every_path_and_counts_them_all(tmp_path):
    doc = pymupdf.open()
    _mixed_sheet(doc, 0)
    page = doc[0]
    seen = []
    kept, count = stream_drawings(page, keep=lambda raw: seen.append(raw["type"]) or False)
    assert kept == [] and count == len(page.get_drawings()) == len(seen)


# ---------------------------------------------------------------------------
# The rooms are the rooms the full read finds
# ---------------------------------------------------------------------------

def _full_read(page, page_no):
    items = list(P._iter_text_items(page, page_no))
    bands = P._legend_bands(items)
    return P._page_drawn_rooms(page, page_no, items, page.get_drawings(), bands)


def _new_read(page, page_no):
    items = list(P._iter_text_items(page, page_no))
    bands = P._legend_bands(items)
    rooms, count = P.page_strokes_and_rooms(page, page_no, items, bands)
    assert count == len(page.get_drawings())
    return rooms


def _books(tmp_path) -> list[Path]:
    return [make_book(tmp_path / "a.pdf", detail_pages=2), make_3x_book(tmp_path / "b.pdf")]


def test_every_sheet_gives_the_same_rooms_as_the_full_read(tmp_path):
    found = 0
    for book in _books(tmp_path):
        doc = pymupdf.open(str(book))
        for i in range(doc.page_count):
            old, new = _full_read(doc[i], i + 1), _new_read(doc[i], i + 1)
            assert new == old, (book.name, i + 1)
            found += len(old)
    # The books carry codes, labels with leaders (one path and two) and
    # block symbols: the comparison is about something.
    assert found >= 8


def test_a_leader_walk_past_the_window_reads_the_whole_sheet(tmp_path, monkeypatch):
    """With the window shrunk to nothing every leader walks out of it: the
    sheet is read whole and the rooms are still the full read's."""
    book = make_book(tmp_path / "a.pdf")
    doc = pymupdf.open(str(book))
    page = doc[1]
    old = _full_read(page, 2)
    assert any(r.via_leader for r in old)
    monkeypatch.setattr(P, "LEADER_REACH_PT", 4.0)
    before = P.leader_fallbacks
    assert _new_read(page, 2) == old
    assert P.leader_fallbacks > before


def test_a_walk_past_the_window_widens_it_before_reading_the_whole_sheet(tmp_path, monkeypatch):
    book = make_book(tmp_path / "a.pdf")
    doc = pymupdf.open(str(book))
    page = doc[1]
    old = _full_read(page, 2)
    monkeypatch.setattr(P, "LEADER_REACH_PT", 30.0)
    monkeypatch.setattr(P, "LEADER_WIDENING", (1, 4))

    def no_full_read(self, *a, **k):
        raise AssertionError("the sheet was read whole")

    items = list(P._iter_text_items(page, 2))
    bands = P._legend_bands(items)
    before = P.leader_fallbacks
    monkeypatch.setattr(pymupdf.Page, "get_drawings", no_full_read)
    rooms, _ = P.page_strokes_and_rooms(page, 2, items, bands)
    assert rooms == old
    assert P.leader_fallbacks == before + 1


def test_strokes_far_from_every_label_are_not_kept(tmp_path, monkeypatch):
    """20,000 thin strokes away from the only label: counted, not held."""
    doc = pymupdf.open()
    p = doc.new_page(width=8000, height=5000)
    box = _label(p, 100, 100, ["Network Equipment Rack (NER) -", "Non-Critical"])
    _arrow(p, (box[2] + 40, 170), start=(box[2] + 1, 99))
    sh = p.new_shape()
    for i in range(20000):
        x = 3000 + (i % 200) * 20
        y = 2000 + (i // 200) * 20
        sh.draw_line((x, y), (x + 15, y + 3))
        sh.finish(color=BLACK, width=0.2)          # one path per stroke
    sh.commit()
    path = tmp_path / "noise.pdf"
    doc.save(str(path))
    page = pymupdf.open(str(path))[0]

    seen = {}
    real = P.leader_geometry

    def spy(drawings, rot):
        seen["n"] = len(drawings)
        return real(drawings, rot)

    monkeypatch.setattr(P, "leader_geometry", spy)
    items = list(P._iter_text_items(page, 1))
    rooms, count = P.page_strokes_and_rooms(page, 1, items, P._legend_bands(items))
    assert count >= 20002
    assert seen["n"] < 20
    assert len(rooms) == 1 and rooms[0].via_leader


def test_the_extraction_counts_every_sheet_and_finds_the_same(tmp_path):
    """extract_drawing: every page's path count is get_drawings()'s (the key
    plan included, whose strokes are no longer walked), and the rooms are the
    full read's on every detail sheet."""
    book = make_book(tmp_path / "a.pdf", detail_pages=2)
    ex = P.extract_drawing(str(book), known_names=[])
    doc = pymupdf.open(str(book))
    for info in ex.pages:
        pg = doc[info.number - 1]
        assert info.vector_count == len(pg.get_drawings())
    expected = []
    for info in ex.pages:
        if not info.key_plan:
            expected += _full_read(doc[info.number - 1], info.number)
    assert ex.rooms == expected and expected


def test_read_drawn_rooms_matches_the_full_read(tmp_path):
    book = make_3x_book(tmp_path / "b.pdf")
    doc = pymupdf.open(str(book))
    expected = []
    for i in range(doc.page_count):
        expected += _full_read(doc[i], i + 1)
    assert P.read_drawn_rooms(str(book)) == expected


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def _big_book(path: Path, rotation: int = 0) -> Path:
    """One master plan of 6,000 x 4,000 pt with a red square at a known spot."""
    doc = pymupdf.open()
    p = doc.new_page(width=6000, height=4000)
    p.draw_rect(pymupdf.Rect(3000, 2000, 3100, 2100), color=None, fill=(1, 0, 0))
    p.insert_text((100, 100), "MASTER PLAN LEVEL 01", fontsize=40)
    p.set_rotation(rotation)
    doc.save(str(path))
    return path


def _project(name: str, book: Path) -> tuple[int, int]:
    """A project with the book stored where an upload is: data/uploads."""
    import shutil
    init_db()
    with SessionLocal() as db:
        p = Project(name=name, venue_code="TST")
        db.add(p)
        db.commit()
        dest = S.settings.uploads_dir / f"project_{p.id}"
        dest.mkdir(parents=True, exist_ok=True)
        stored = dest / book.name
        shutil.copy2(book, stored)
        d = S.ingest_drawing(db, p, stored, book.name)
        return p.id, d.id


def _rgb(png: bytes, x: int, y: int) -> tuple:
    pix = pymupdf.Pixmap(png)
    return tuple(pix.pixel(x, y)[:3])


def test_a_tile_shows_the_sheet_where_the_viewer_draws_it(client, tmp_path):
    pid, did = _project("map120 tiles", _big_book(tmp_path / "MP_900.pdf"))
    # z = 6 is 1 px per point: the square's corner (3000, 2000) is in tile
    # (5, 3), at pixel (3000 - 2560, 2000 - 1536).
    r = client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/6/5/3.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert _rgb(r.content, 440 + 10, 464 + 10) == (255, 0, 0)
    assert _rgb(r.content, 440 - 10, 464 - 10) == (255, 255, 255)
    # Kept on disk and served from there the second time.
    on_disk = PT.tiles_dir(pid, did, _sha(did), 1) / "6" / "5_3.png"
    assert on_disk.read_bytes() == r.content
    assert client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/6/5/3.png").content == r.content


def _sha(did: int) -> str:
    from app.models import Drawing
    with SessionLocal() as db:
        return db.get(Drawing, did).sha256


def test_the_last_column_stops_at_the_edge_and_nothing_lies_beyond(client, tmp_path):
    pid, did = _project("map120 edges", _big_book(tmp_path / "MP_901.pdf"))
    # 6,000 pt at 1 px/pt: 11 full columns of 512 and one of 368.
    r = client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/6/11/0.png")
    assert r.status_code == 200 and pymupdf.Pixmap(r.content).width == 6000 - 11 * 512
    for bad in ("6/12/0", "6/0/8", "11/0/0", "6/-1/0"):
        assert client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/{bad}.png").status_code == 404
    assert client.get(f"/api/projects/{pid}/drawings/{did}/pages/2/tiles/0/0/0.png").status_code == 404
    assert client.get(f"/api/projects/{pid}/drawings/{did + 999}/pages/1/tiles/0/0/0.png").status_code == 404


@pytest.mark.parametrize("rotation", [90, 270])
def test_a_turned_sheet_is_tiled_as_it_is_displayed(client, tmp_path, rotation):
    book = _big_book(tmp_path / f"MP_9{rotation}.pdf", rotation)
    pid, did = _project(f"map120 turned {rotation}", book)
    page = pymupdf.open(str(book))[0]
    clip = pymupdf.Rect(512, 1024, 1024, 1536)                 # tile (1, 2) at z = 6
    want = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), clip=clip, alpha=False).tobytes("png")
    got = client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/6/1/2.png").content
    assert pymupdf.Pixmap(got).samples == pymupdf.Pixmap(want).samples


def test_removing_the_book_takes_its_tiles(client, tmp_path):
    pid, did = _project("map120 remove", _big_book(tmp_path / "MP_902.pdf"))
    assert client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/2/0/0.png").status_code == 200
    folder = PT.tiles_dir(pid, did, _sha(did), 1)
    assert folder.is_dir()
    assert client.delete(f"/api/projects/{pid}/drawings/{did}").status_code == 200
    assert not folder.exists()


def test_removing_the_project_takes_its_tiles_with_the_book_held_open(client, tmp_path):
    pid, did = _project("map120 remove project", _big_book(tmp_path / "MP_903.pdf"))
    assert client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/3/0/0.png").status_code == 200
    stored = Path(S.settings.uploads_dir / f"project_{pid}")
    folder = PT.tiles_dir(pid, did, _sha(did), 1)
    assert stored.is_dir() and folder.is_dir()
    S.remove_project_files(pid)
    assert not stored.exists() and not folder.exists()


def test_only_the_pages_looked_at_last_keep_their_display_list(tmp_path):
    books = [_big_book(tmp_path / f"MP_95{i}.pdf") for i in range(3)]
    PT.release()
    for b in books:
        with PT._TILE_LOCK:
            PT._render_here(str(b), 1, 6000, 4000, 0, 0, 0)
    assert len(PT._lists) == PT.DISPLAY_LISTS_KEPT
    PT.release()
    assert not PT._lists


def test_the_viewer_and_the_server_agree_on_which_sheets_are_tiled():
    """One rule in two places: the server's constant, the viewer's, and the
    viewer's own bitmap limits it is derived from."""
    viewer = (ROOT / "frontend" / "src" / "pages" / "Viewer.tsx").read_text(encoding="utf-8")
    tiles = (ROOT / "frontend" / "src" / "components" / "PageTiles.tsx").read_text(encoding="utf-8")
    cap = float(re.search(r"const MAX_CANVAS_PX = (\d+)", viewer).group(1))
    scale = float(re.search(r"const RENDER_SCALE = ([\d.]+)", viewer).group(1))
    a, b = re.search(r"TILED_ABOVE_PT = (\d+) / ([\d.]+)", tiles).groups()
    assert float(a) / float(b) == cap / scale == PT.TILED_ABOVE_PT
    assert f"TILE_PX = {PT.TILE_PX}" in tiles and f"TILE_ZMAX = {PT.TILE_ZMAX}" in tiles
    # The ordinary 34 x 22 in sheets of the real books stay with PDF.js.
    assert not PT.is_tiled(2449, 1585) and PT.is_tiled(24491, 15846)


def test_a_whole_page_picture_of_a_master_plan_is_capped(tmp_path):
    """The page picture endpoint drew any page at the dpi asked: a master plan
    at 110 dpi is 37,000 px wide. Capped at 6,000; an ordinary sheet is drawn
    as before."""
    big = _big_book(tmp_path / "MP_960.pdf")
    pix = pymupdf.Pixmap(P.render_page_png(str(big), 1, dpi=110))
    assert max(pix.width, pix.height) <= P.RENDER_MAX_PX
    doc = pymupdf.open()
    doc.new_page(width=2448, height=1584)
    small = tmp_path / "sheet.pdf"
    doc.save(str(small))
    pix = pymupdf.Pixmap(P.render_page_png(str(small), 1, dpi=110))
    want = pymupdf.open(str(small))[0].get_pixmap(dpi=110)
    assert (pix.width, pix.height) == (want.width, want.height)


def test_the_progress_moves_inside_a_sheet_and_never_back(tmp_path, monkeypatch):
    """Marcelo, 5 Nov 2026: the bar "parece não funcionar" on the master plan —
    it moved once per sheet, and a sheet is tens of seconds. Now it moves
    while the strokes are read, and only forward."""
    from app.domain import linework
    monkeypatch.setattr(linework, "TICK_EVERY", 5)
    book = make_book(tmp_path / "a.pdf", detail_pages=2)
    for run in ("extract", "rooms"):
        seen: list[tuple[float, str]] = []
        if run == "extract":
            P.extract_drawing(str(book), known_names=[], progress=lambda f, s: seen.append((f, s)))
        else:
            P.read_drawn_rooms(str(book), progress=lambda f, s: seen.append((f, s)))
        fracs = [f for f, _ in seen]
        assert fracs == sorted(fracs), run
        assert all(0.0 <= f <= 1.0 for f in fracs)
        per_page = [s for _, s in seen if "page 2" in s]
        assert len(per_page) >= 4, (run, per_page)
        assert any("strokes read" in s for _, s in seen)


# ---------------------------------------------------------------------------
# rc2 — the independent tester's findings on rc1
# ---------------------------------------------------------------------------

def _raw_page(content: bytes):
    doc = pymupdf.open()
    p = doc.new_page(width=600, height=400)
    p.insert_text((10, 10), "x")                     # a font resource and a stream
    doc.update_stream(p.get_contents()[0], content)
    return doc, p


@pytest.mark.parametrize("content", [
    b"1 0 0 rg 10 10 m 100 10 l 50 80 l h f BT /F1 10 Tf 200 200 Td (hi) Tj ET "
    b"10 10 m 100 10 l 50 80 l h S",
    b"1 0 0 rg 10 10 m 100 10 l 50 80 l f 0 0 1 RG 10 10 m 100 10 l 50 80 l h S",
    b"1 0 0 rg 10 10 m 100 10 l 50 80 l h f 10 10 m 100 10 l 60 80 l h S",
])
def test_a_fill_and_its_stroke_merge_as_get_drawings_merges_them(content):
    """F3: text between a fill and the stroke of the same path does not part
    them in get_drawings(); the merge keeps the fill's keys."""
    doc, page = _raw_page(content)
    full = page.get_drawings()
    streamed, count = stream_drawings(page)
    assert count == len(full)
    assert streamed == full


def test_a_removed_book_is_never_tiled_again(tmp_path):
    """F1: a request that read the book's row before it was removed, and
    waited for the renderer, opened the book again and wrote its tiles back."""
    book = _big_book(tmp_path / "MP_970.pdf")
    PT.drop_drawing(990001, 990001, str(book))
    with pytest.raises(PT.TileError):
        PT.tile(990001, 990001, "ab" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    assert not PT.tiles_dir(990001, 990001, "ab" * 32, 1).exists()
    assert all(not k.endswith("mp_970.pdf") for k in PT._docs)
    PT.drop_project(990002, str(tmp_path))
    with pytest.raises(PT.TileError):
        PT.tile(990002, 990003, "cd" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    # Once the removal is done, the id may come back with another book
    # (SQLite reuses the highest id): that book is served.
    PT.drop_done(drawing_id=990001)
    PT.drop_done(project_id=990002)
    assert PT.tile(990001, 990001, "ab" * 32, str(book), 1, 6000, 4000, 0, 0, 0)
    assert PT.tile(990002, 990003, "cd" * 32, str(book), 1, 6000, 4000, 0, 0, 0)


def _gil_bound(seconds_hint: float = 0.3) -> None:
    """Hold Python's interpreter lock the way MuPDF does while it draws a
    tile: a regular expression that backtracks keeps it for its whole run."""
    import re as _re
    _re.match(r"(a+)+$", "a" * 23 + "b")


def test_a_burst_of_tiles_leaves_the_server_answering(client, tmp_path, monkeypatch):
    """F2 (rc1) and its second form (rc3): forty tiles asked at once held every
    database connection, then every thread of the server's pool, and each slow
    tile kept the interpreter lock — sign-in, the project list and /healthz
    waited tens of seconds. Tiles now have threads of their own."""
    import threading
    import time as _t
    pid, did = _project("map120 burst", _big_book(tmp_path / "MP_971.pdf"))
    real = PT.render_tile

    def slow(*a, **k):
        _gil_bound()
        return real(*a, **k)

    monkeypatch.setattr(PT, "render_tile", slow)
    codes: list[int] = []

    def ask(x, y):
        r = client.get(f"/api/projects/{pid}/drawings/{did}/pages/1/tiles/6/{x}/{y}.png")
        codes.append(r.status_code)

    threads = [threading.Thread(target=ask, args=(x, y)) for x in range(8) for y in range(5)]
    for th in threads:
        th.start()
    _t.sleep(1.0)
    waits = {}
    for url in ("/api/projects", "/healthz", "/api/auth/state"):
        t0 = _t.time()
        r = client.get(url)
        waits[url] = (r.status_code, _t.time() - t0)
    for th in threads:
        th.join(180)
    assert all(code == 200 for code, _ in waits.values()), waits
    assert all(w < 5.0 for _, w in waits.values()), waits
    assert codes.count(200) == 40


def test_an_error_inside_the_stroke_reading_is_raised_not_a_crash(tmp_path):
    """rc3: a cancel raised while MuPDF was handing the strokes over crashed
    the server process (an exception cannot cross MuPDF's C++ loop). It is now
    held until MuPDF is done, then raised."""
    from app.domain import linework
    book = make_book(tmp_path / "a.pdf")
    page = pymupdf.open(str(book))[1]

    class Stop(Exception):
        pass

    def tick(n):
        raise Stop()

    old = linework.TICK_EVERY
    linework.TICK_EVERY = 1
    try:
        with pytest.raises(Stop):
            stream_drawings(page, tick=tick)

        def keep(raw):
            raise Stop()

        with pytest.raises(Stop):
            stream_drawings(page, keep=keep)
    finally:
        linework.TICK_EVERY = old
    # And the page reads normally afterwards.
    assert stream_drawings(page)[1] == len(page.get_drawings())


def test_a_cancelled_place_ners_records_nothing(tmp_path, monkeypatch):
    """rc3: cancelling Place NERs while it read a book was caught as "the book
    cannot be read", and the step recorded "0 rooms found" for the book."""
    from app import isolate
    from app.jobs import JobCancelled
    from app.models import Drawing
    pid, did = _project("map120 cancel place", make_book(tmp_path / "TST_400.pdf"))
    with SessionLocal() as db:
        before = dict(db.get(Drawing, did).extract_summary or {})

    def cancelled(*a, **k):
        raise JobCancelled()

    monkeypatch.setattr(isolate, "run", cancelled)
    with SessionLocal() as db:
        with pytest.raises(JobCancelled):
            S.place_ners_from_drawing(db, db.get(Project, pid))
    with SessionLocal() as db:
        after = dict(db.get(Drawing, did).extract_summary or {})
    assert after.get("ner_pass") == before.get("ner_pass")
    assert "ner_pass_error" not in after



def test_tiles_are_drawn_by_a_renderer_process_and_it_closes_the_books(tmp_path, monkeypatch):
    """rc4: MuPDF keeps the interpreter lock for the whole of a tile, so a slow
    tile drawn in the server held every request (/healthz 37 s, independent
    tester, rc3). Tiles are drawn in a child process; the server waits on a
    pipe. Its books are closed there when a book goes."""
    import os as _os
    monkeypatch.delenv("NER_INPROCESS", raising=False)
    book = _big_book(tmp_path / "MP_980.pdf")
    with PT._TILE_LOCK:
        png = PT.render_tile(str(book), 1, 6000, 4000, 6, 5, 3)
        assert PT._renderer.pid not in (None, _os.getpid())
        assert png == PT._render_here(str(book), 1, 6000, 4000, 6, 5, 3)
        PT.forget(str(book))
        PT._forget_here(str(book))
    # A renderer that died (a book that crashes MuPDF) is replaced by the
    # next tile; the server is untouched.
    first = PT._renderer.pid
    PT._renderer.proc.kill()
    PT._renderer.proc.wait(10)
    with PT._TILE_LOCK:
        assert PT.render_tile(str(book), 1, 6000, 4000, 6, 5, 3) == png
    assert PT._renderer.pid not in (None, first)
    with PT._TILE_LOCK:
        with pytest.raises(PT.TileError):
            PT.render_tile(str(book), 1, 6000, 4000, 6, 99, 99)   # empty clip


def test_a_slow_tile_in_the_renderer_does_not_hold_the_server(tmp_path, monkeypatch):
    """The interpreter lock of this process stays free while the renderer
    draws: drawn here, a ticker thread stalls for as long as the tile takes."""
    import random
    import threading
    import time as _t
    monkeypatch.delenv("NER_INPROCESS", raising=False)
    rnd = random.Random(1)
    doc = pymupdf.open()
    p = doc.new_page(width=20000, height=14000)
    sh = p.new_shape()                      # one dashed, half-transparent scribble:
    sh.draw_polyline([(rnd.uniform(0, 20000), rnd.uniform(0, 14000)) for _ in range(9000)])
    sh.finish(color=(0, 0, 0), width=2, dashes="[3 2] 0", stroke_opacity=0.5, closePath=False)
    sh.commit()
    path = tmp_path / "SLOW.pdf"
    doc.save(str(path))
    with PT._TILE_LOCK:                      # the renderer up and the page read
        PT.render_tile(str(path), 1, 20000, 14000, 0, 0, 0)
    gaps = []
    stop = threading.Event()

    def ticker():
        last = _t.perf_counter()
        while not stop.is_set():
            _t.sleep(0.01)
            now = _t.perf_counter()
            gaps.append(now - last)
            last = now

    th = threading.Thread(target=ticker)
    th.start()
    t0 = _t.perf_counter()
    with PT._TILE_LOCK:
        PT.render_tile(str(path), 1, 20000, 14000, 2, 0, 0)
    took = _t.perf_counter() - t0
    stop.set()
    th.join()
    assert took > 0.5, f"the tile drew too fast to show anything ({took:.2f} s)"
    assert max(gaps) < 0.2, (max(gaps), took)
