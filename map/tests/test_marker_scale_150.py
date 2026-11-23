"""1.5.0 - the marked-up PDF: a room marker sized by the sheet's scale.

Marcelo, 21 Nov 2026, on a 900-series master plan (the whole venue on one
340-inch sheet): the NERs "ficam muito grandes" when he zooms in on the
exported PDF - a marker of a fixed 12 pt on the paper is several metres of
floor on a sheet drawn at a small scale, covers the rooms around it, and the
names run into one another. The marker is now about one metre of floor in
radius, by the sheet's own scale, never larger than before (detail sheets at
1:100-1:200 are drawn exactly as they were) nor smaller than 3 pt; the name
shrinks with it, to 4 pt at least. No scale, or a reference marker: as before.
"""
from __future__ import annotations

import os
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="ner-marker-scale-150-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/m.sqlite3")

import pymupdf                                                      # noqa: E402

from app.domain.coverage import NER, compute_coverage               # noqa: E402
from app.domain.distance import DemandType, DistanceRules           # noqa: E402
from app.domain.geometry import Area, Point                         # noqa: E402
from app.domain.pdf_ingest import ElementKind                       # noqa: E402
from app.domain.reports import (MARKER_LABEL_SIZE, MARKER_MIN_LABEL_SIZE,  # noqa: E402
                                MARKER_MIN_RADIUS, MARKER_RADIUS,
                                REFERENCE_LABEL_SIZE, export_marked_pdf,
                                marker_factor)
from app.domain.units import POINTS_PER_METRE, Scale                # noqa: E402

RULES = DistanceRules()


def at(denominator: float) -> Scale:
    return Scale(metres_per_unit=denominator / POINTS_PER_METRE, source="ratio",
                 label=f"1:{denominator:g}", confirmed=True)


def test_detail_sheets_keep_the_full_marker():
    assert marker_factor(None) == 1.0
    for d in (50, 100, 192, 200):
        assert marker_factor(at(d)) == 1.0, d


def test_a_small_scale_sheet_gets_about_a_metre_of_floor():
    # 1 m at 1:500 is 2 mm of paper: 5.67 pt
    assert marker_factor(at(500)) * MARKER_RADIUS == pytest.approx(POINTS_PER_METRE / 500, rel=1e-6)
    # never below the floor size
    assert marker_factor(at(20000)) * MARKER_RADIUS == pytest.approx(MARKER_MIN_RADIUS)


def test_an_unusable_scale_draws_as_before():
    class Bad:
        def to_pdf_units(self, m):
            raise ZeroDivisionError
    assert marker_factor(Bad()) == 1.0


def _export(tmp_path, scale, kinds):
    # A file name per call: on Windows a PDF still open cannot be overwritten.
    tag = os.urandom(4).hex()
    src = str(tmp_path / f"src-{tag}.pdf")
    doc = pymupdf.open()
    doc.new_page(width=2000, height=1400)
    doc.save(src)
    doc.close()
    rooms = [NER(key=f"K{i}", label=f"ROOM_{i}", kind=k, point=Point(300 + 400 * i, 400, 1),
                 critical=k == ElementKind.NER_CRITICAL, total_ports=10, used_ports=0)
             for i, k in enumerate(kinds)]
    res = compute_coverage(areas=[(Area(key="S", name="S", page=1,
                                        label_point=Point(1800, 1200, 1)),
                                   1.0, DemandType.GENERAL, None, True)],
                           ners=rooms, scale_for_page={1: scale} if scale else {},
                           rules=RULES)
    out = str(tmp_path / f"out-{tag}.pdf")
    export_marked_pdf(src, out, coverage=res, ners=rooms,
                      scale_for_page={1: scale} if scale else {}, rules=RULES,
                      draw_radii=False, draw_links=False, draw_legend=False)
    return pymupdf.open(out)[0]


def _spans(page, text):
    return [s for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
            for s in ln.get("spans", []) if s["text"] == text]


def _dot_widths(page):
    return [round(d["rect"].width, 1) for d in page.get_drawings()
            if d.get("fill") is not None and abs(d["rect"].width - d["rect"].height) < 0.01]


def test_on_a_master_plan_the_marker_and_its_name_shrink(tmp_path):
    page = _export(tmp_path, at(500), [ElementKind.NER_CRITICAL])
    r = POINTS_PER_METRE / 500
    assert round(2 * r, 1) in _dot_widths(page)
    assert 2 * MARKER_RADIUS not in _dot_widths(page)
    span = _spans(page, "ROOM_0")[0]
    assert span["size"] == pytest.approx(max(MARKER_MIN_LABEL_SIZE,
                                             MARKER_LABEL_SIZE * r / MARKER_RADIUS), abs=0.01)


def test_the_name_never_goes_below_the_floor_size(tmp_path):
    page = _export(tmp_path, at(20000), [ElementKind.NER_NONCRITICAL])
    assert _spans(page, "ROOM_0")[0]["size"] == pytest.approx(MARKER_MIN_LABEL_SIZE, abs=0.01)
    assert round(2 * MARKER_MIN_RADIUS, 1) in _dot_widths(page)


def test_a_detail_sheet_and_a_sheet_with_no_scale_are_drawn_as_before(tmp_path):
    for scale in (at(100), None):
        page = _export(tmp_path, scale, [ElementKind.NER_CRITICAL])
        assert 2 * MARKER_RADIUS in _dot_widths(page)
        assert _spans(page, "ROOM_0")[0]["size"] == pytest.approx(MARKER_LABEL_SIZE)


def test_an_illustrative_ner_shrinks_like_a_room(tmp_path):
    """1.5.0-rc3 (Marcelo: "altera tbm a ilustração" - a large venue project drawn
    only with illustrative NERs came out with every NER still large): the
    references (*NER, *TER, ...) are sized by the scale too."""
    page = _export(tmp_path, at(700), [ElementKind.NER_CRITICAL,
                                        ElementKind.NER_CRITICAL_REF,
                                        ElementKind.NER_NONCRITICAL_REF,
                                        ElementKind.TER_REF])
    widths = _dot_widths(page)
    assert 2 * MARKER_RADIUS not in widths
    r = round(2 * POINTS_PER_METRE / 700, 1)
    assert widths.count(r) == 4, widths
    assert _spans(page, "ROOM_1")[0]["size"] == pytest.approx(
        max(MARKER_MIN_LABEL_SIZE, REFERENCE_LABEL_SIZE * (POINTS_PER_METRE / 700) / MARKER_RADIUS),
        abs=0.01)


def test_an_unconfirmed_scale_keeps_the_full_size(tmp_path):
    """1.5.0-rc3 (T4-1): the scale the import detects is stored unconfirmed;
    only a confirmed one shrinks the markers, as the manual says."""
    s = Scale(metres_per_unit=500 / POINTS_PER_METRE, source="detected", label="1:500",
              confirmed=False)
    assert marker_factor(s) == 1.0
    page = _export(tmp_path, s, [ElementKind.NER_CRITICAL])
    assert 2 * MARKER_RADIUS in _dot_widths(page)


# ---------------------------------------------------------------------------
# The reach circles can be left off (1.5.0, Marcelo: "radius opcional na
# exportação do PDF ... marcada como default")
# ---------------------------------------------------------------------------

def _circle_radii(page):
    """Radii of the unfilled circles drawn on the page (the reach circles)."""
    return [round(d["rect"].width / 2, 1) for d in page.get_drawings()
            if d.get("fill") is None and abs(d["rect"].width - d["rect"].height) < 0.01
            and d["rect"].width > 60]


def test_the_reach_circles_are_drawn_by_default_and_can_be_left_off(tmp_path):
    src = str(tmp_path / "src.pdf")
    doc = pymupdf.open()
    doc.new_page(width=2000, height=1400)
    doc.save(src)
    doc.close()
    scale = at(500)
    rooms = [NER(key="K", label="ROOM", kind=ElementKind.NER_CRITICAL,
                 point=Point(1000, 700, 1), critical=True, total_ports=10, used_ports=0)]
    res = compute_coverage(areas=[(Area(key="S", name="S", page=1,
                                        label_point=Point(1100, 700, 1)),
                                   1.0, DemandType.GENERAL, None, True)],
                           ners=rooms, scale_for_page={1: scale}, rules=RULES)
    pages = {}
    for on in (True, False):
        out = str(tmp_path / f"out-{on}.pdf")
        kw = {} if on else {"draw_radii": False}
        export_marked_pdf(src, out, coverage=res, ners=rooms, scale_for_page={1: scale},
                          rules=RULES, draw_legend=False, **kw)
        pages[on] = pymupdf.open(out)[0]
    want = round(scale.to_pdf_units(RULES.general_limit_m / RULES.routing_slack_factor), 1)
    assert any(abs(r - want) < 0.2 for r in _circle_radii(pages[True]))
    assert _circle_radii(pages[False]) == []
    # the rest of the sheet stays
    for page in pages.values():
        assert "ROOM" in page.get_text()


def test_the_composite_legend_lists_the_reach_only_when_it_is_drawn():
    from app.composite_service import _legend
    for reach in (True, False):
        doc = pymupdf.open()
        page = doc.new_page(width=2000, height=1400)
        _legend(page, 1.0, {"general_m": 90, "photographer_m": 70}, reach=reach)
        text = page.get_text()
        assert ("90 m reach" in text) is reach and ("70 m reach" in text) is reach
        assert "critical" in text
        doc.close()


def test_the_report_routes_take_the_switch():
    import inspect
    from app.api import composite_routes, routes
    assert "radii: bool = Query(True)" in inspect.getsource(routes.build_report)
    assert "radii: bool = Query(True)" in inspect.getsource(composite_routes.composite_report)
    src = inspect.getsource(routes._build_report_now)
    assert "draw_radii=draw_radii" in src


# ---------------------------------------------------------------------------
# 1.5.0-rc3: everything drawn on the sheet in step, and /UserUnit sheets
# ---------------------------------------------------------------------------

def test_scaled_keeps_base_and_floor():
    from app.domain.reports import scaled
    assert scaled(9, 1.0, 4) == 9
    assert scaled(9, 0.5, 4) == 4.5
    assert scaled(9, 0.1, 4) == 4
    assert scaled(3, 0.1, 4) == 3          # never over the base


def test_no_symbol_is_larger_than_the_ner_on_a_master_plan(tmp_path):
    """T4-8: the space dot is not larger than the NER that serves it."""
    page = _export(tmp_path, at(1000), [ElementKind.NER_CRITICAL])
    ner = max(_dot_widths(page))
    others = [round(d["rect"].width, 1) for d in page.get_drawings()
              if d.get("fill") is not None and d.get("color") != (0.0, 0.0, 0.0)
              and abs(d["rect"].width - d["rect"].height) < 0.01]
    assert others and max(others) <= ner


def _uu_export(tmp_path, uu, scale):
    tag = os.urandom(4).hex()
    src = str(tmp_path / f"uu-{tag}.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=2000, height=1400)
    if uu:
        doc.xref_set_key(page.xref, "UserUnit", str(uu))
    doc.save(src)
    doc.close()
    rooms = [NER(key="K", label="ROOM_UU", kind=ElementKind.NER_CRITICAL,
                 point=Point(1000, 700, 1), critical=True, total_ports=1, used_ports=0)]
    res = compute_coverage(areas=[(Area(key="S", name="S", page=1,
                                        label_point=Point(1100, 760, 1)),
                                   1.0, DemandType.GENERAL, None, True)],
                           ners=rooms, scale_for_page={1: scale}, rules=RULES)
    out = str(tmp_path / f"uu-out-{tag}.pdf")
    export_marked_pdf(src, out, coverage=res, ners=rooms, scale_for_page={1: scale},
                      rules=RULES, draw_radii=False, draw_links=False, draw_legend=False)
    return pymupdf.open(out)[0]


@pytest.mark.parametrize("uu", [1.19, 1.7, 2.0])
@pytest.mark.parametrize("den", [100, 500])
def test_on_a_user_unit_sheet_the_name_and_tag_match_a_plain_sheet(tmp_path, uu, den):
    """T4-2: PyMuPDF multiplies a font size and a rectangle by /UserUnit; the
    name is the size it is on a plain sheet, centred under its dot, inside its
    tag."""
    plain = _uu_export(tmp_path, None, at(den))
    big = _uu_export(tmp_path, uu, at(den))
    s0, s1 = _spans(plain, "ROOM_UU")[0], _spans(big, "ROOM_UU")[0]
    assert s1["size"] == pytest.approx(s0["size"], abs=0.05)
    dot = max(_dot_widths(big))
    dots = [d["rect"] for d in big.get_drawings() if round(d["rect"].width, 1) == dot]
    cx = (s1["bbox"][0] + s1["bbox"][2]) / 2
    assert cx == pytest.approx((dots[0].x0 + dots[0].x1) / 2, abs=1.0)
    tags = [d["rect"] for d in big.get_drawings() if d.get("fill") == (1.0, 1.0, 1.0)]
    assert any(t.x0 <= s1["bbox"][0] + 0.5 and t.x1 >= s1["bbox"][2] - 0.5 for t in tags), \
        (s1["bbox"], tags)


def test_a_full_width_level_is_written_plainly():
    """1.5.0-rc3 (the tester's risk): a full-width "０２" is LEVEL 02."""
    from app.domain.consolidation import checked_level
    assert checked_level("０２") == "LEVEL 02"
    assert checked_level("ＬＥＶＥＬ Ｂ１") == "LEVEL B1"


def test_a_hand_drawn_wifi_shrinks_with_the_ners(tmp_path):
    """1.5.0-rc3: the illustrative Wi-Fi point (drawn by hand on the map) is
    sized by the scale like the NERs."""
    def arcs(page):
        return [d for d in page.get_drawings() if d.get("fill") is None
                and d.get("color") is not None and d["rect"].width > 1]
    pages = {}
    for den in (100, 1000):
        tag = os.urandom(4).hex()
        src = str(tmp_path / f"w-{tag}.pdf")
        doc = pymupdf.open()
        doc.new_page(width=2000, height=1400)
        doc.save(src)
        doc.close()
        pts = [NER(key="W", label="", kind=ElementKind.WIRELESS, point=Point(1000, 700, 1),
                   critical=False, total_ports=0, used_ports=0)]
        out = str(tmp_path / f"w-out-{tag}.pdf")
        res = compute_coverage(areas=[], ners=[], scale_for_page={1: at(den)}, rules=RULES)
        export_marked_pdf(src, out, coverage=res, ners=pts, scale_for_page={1: at(den)},
                          rules=RULES, draw_radii=False, draw_links=False, draw_legend=False)
        pages[den] = pymupdf.open(out)[0]
    big = max(d["rect"].width for d in arcs(pages[100]))
    small = max(d["rect"].width for d in arcs(pages[1000]))
    assert small < big / 2, (small, big)
