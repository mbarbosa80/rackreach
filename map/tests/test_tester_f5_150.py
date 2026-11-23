"""Independent tester F5, RackReach Map 1.5.0-rc5 (tag v150-rc5), 22 Nov 2026.

Focused re-check of the rc5 fixes T5-1 (ordinals kept as typed) and T5-2
(stroke widths on /UserUnit sheets). Synthetic data only.

* SECTION A - every test FAILS on rc5 (finding IDs T6-n).
* SECTION B - checks that PASS on rc5 (documented, not findings).
"""
from __future__ import annotations

import os
import unicodedata

import pymupdf
import pytest

from tests.test_multi_book_410 import TMP  # noqa: F401  (sets the env)
from app.domain import reports as RP
from app.domain.consolidation import Candidate, checked_level, compare, level_key
from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceRules
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.units import POINTS_PER_METRE, Scale

RULES = DistanceRules()
W, H = 2000.0, 1400.0


def _export(rot=0, uu=1.0, den=500):
    """NER with note and reach circles, unconfirmed-match space with its
    leader line, *NER reference, named area, legend: displayed W x H
    whatever the /UserUnit (MediaBox W/uu x H/uu)."""
    tag = os.urandom(4).hex()
    src = os.path.join(TMP, f"f5-src-{tag}.pdf")
    d = pymupdf.open()
    p = d.new_page(width=W / uu, height=H / uu)
    if uu != 1.0:
        d.xref_set_key(p.xref, "UserUnit", str(uu))
    if rot:
        p.set_rotation(rot)
    d.save(src)
    d.close()
    d = pymupdf.open(src)
    dw, dh = d[0].rect.width, d[0].rect.height
    d.close()
    sc = Scale(den / POINTS_PER_METRE, "ratio", "x", True)
    rooms = [NER(key="K", label="NER-CRIT-01", kind=ElementKind.NER_CRITICAL,
                 point=Point(dw * .5, dh * .5, 1), critical=True, total_ports=1, used_ports=0,
                 user_note="moved here because the other room has no power"),
             NER(key="R", label="*NER-REF", kind=ElementKind.NER_CRITICAL_REF,
                 point=Point(dw * .7, dh * .6, 1), critical=True, total_ports=1, used_ports=0)]
    res = compute_coverage(areas=[(Area(key="S", name="SPACE-A", page=1,
                                        label_point=Point(dw * .55, dh * .55, 1)),
                                   1.0, DemandType.GENERAL, None, False)],
                           ners=rooms[:1], scale_for_page={1: sc}, rules=RULES)
    areas = [{"id": 1, "page": 1, "label": "BTOC AREA",
              "polygon": [(dw * .2, dh * .2), (dw * .35, dh * .2), (dw * .35, dh * .35),
                          (dw * .2, dh * .35)]}]
    out = os.path.join(TMP, f"f5-out-{tag}.pdf")
    RP.export_marked_pdf(src, out, coverage=res, ners=rooms, scale_for_page={1: sc},
                         rules=RULES, draw_legend=True, draw_notes=True, areas=areas,
                         draw_radii=True)
    return out


def _strokes(path):
    return sorted((round(d.get("width") or 0, 3), str(d.get("dashes")),
                   tuple(round(v) for v in d["rect"]))
                  for d in pymupdf.open(path)[0].get_drawings())


# ===========================================================================
# SECTION A - FAIL on rc5
# ===========================================================================

# ---------------------------------------------------------------------------
# T6-1  rc5 applies NFKC one character at a time. A level typed or pasted in
#       decomposed form (NFD: "i" + U+0301, as text copied from a macOS file
#       name or some PDF extractions arrives) is no longer composed:
#       "Nível 2" in NFD is stored "NÍVEL 2" (decomposed) instead of
#       "LEVEL 02", and an NFD "Térreo" is a different string - a different
#       level for level_key - from the NFC "TÉRREO" the next person types.
#       rc3 (whole-string NFKC) composed both; rc2 behaved like rc5.
#       Expected: the same result as for the composed text (NFC first,
#       then the per-character NFKC).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("typed", ["Nível 2", "NÍVEL 2", "Térreo"])
def test_t6_1_a_decomposed_level_is_stored_as_the_composed_one(typed):
    nfd = unicodedata.normalize("NFD", typed)
    assert nfd != typed
    assert checked_level(nfd) == checked_level(typed), (checked_level(nfd), checked_level(typed))
    assert level_key(checked_level(nfd)) == level_key(checked_level(typed))


# ---------------------------------------------------------------------------
# T6-2  T5-2 divides the stroke WIDTH by /UserUnit, but the dash pattern is
#       still in user units and is multiplied by the unit: the reach circles
#       "[4 3]" come out "[6.8 5.1]" at UserUnit 1.7, the photographer circle
#       "[2 3]" -> "[3.4 5.1]", the leader line "[1 2]" -> "[1.7 3.4]", the
#       reference ring and the legend swatches the same. Cosmetic (the
#       circles read a little coarser on the large master plans).
#       Expected: the same dash pattern as on a sheet without /UserUnit.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uu", [1.19, 1.7])
def test_t6_2_dash_patterns_on_a_user_unit_sheet(uu):
    a, b = _strokes(_export()), _strokes(_export(uu=uu))
    assert len(a) == len(b)
    # Compared as numbers (1.5.0-rc6): the pattern goes through 1/unit and
    # back, so "[ 4 3 ] 0" reads "[ 3.9999948 3.000002 ] 0" - float noise.
    def nums(s):
        import re
        return [float(v) for v in re.findall(r"-?\d*\.?\d+", str(s or ""))]
    bad = [(x[1], y[1]) for x, y in zip(a, b)
           if len(nums(x[1])) != len(nums(y[1]))
           or any(abs(p - q) > 1e-3 for p, q in zip(nums(x[1]), nums(y[1])))]
    assert not bad, bad[:4]


# ===========================================================================
# SECTION B - PASS on rc5
# ===========================================================================

# --- T5-1 -------------------------------------------------------------------

@pytest.mark.parametrize("typed,stored", [("2ª", "2ª"), ("2º", "2º"), ("1º andar", "1º ANDAR"),
                                          ("Level ²", "LEVEL ²"), ("B¹", "B¹"), ("㎡", "㎡")])
def test_b_ordinals_and_superscripts_are_kept_as_typed(typed, stored):
    assert checked_level(typed) == stored


def test_b_2a_ordinal_is_not_level_2a_and_is_not_blocked_against_an_unknown_level():
    lv = checked_level("2ª")
    assert level_key(lv) != level_key("LEVEL 2A")
    a = Candidate(ref="a", label="NER 4", page=1, level=lv, building=None)
    for other, blocked in (("LEVEL 2A", True), (None, False), ("", False)):
        b = Candidate(ref="b", label="NER 4", page=2, level=other, building=None)
        assert (compare(a, b).blocking == "different_level") is blocked, (other, compare(a, b).to_dict())


@pytest.mark.parametrize("typed,stored", [
    ("０２", "LEVEL 02"), ("Ｌ２", "LEVEL 02"), ("B１", "LEVEL B1"), ("𝟐", "LEVEL 02"),
    ("Nível 2", "LEVEL 02"), ("NÍVEL 2", "LEVEL 02"), ("ﬁrst floor", "FIRST FLOOR"),
    ("Ⅱ", "II"), ("LEVEL 2", "LEVEL 02"), ("Térreo", "TÉRREO"),
])
def test_b_compatibility_forms_still_written_plainly(typed, stored):
    assert checked_level(typed) == stored


@pytest.mark.parametrize("bad", ["ㅤ", "ﾠ", "02️", "02⠀", "​",
                                 "ß" * 21])
def test_b_refusals_unchanged(bad):
    with pytest.raises(ValueError):
        checked_level(bad)


# --- T5-2 -------------------------------------------------------------------

@pytest.mark.parametrize("uu", [1.19, 1.7, 2.38])
def test_b_every_stroke_width_is_the_same_on_a_user_unit_sheet(uu):
    """Rings, tags, leader line, legend frame and swatches, area outline,
    reach circles, note border: same width and same place."""
    a, b = _strokes(_export()), _strokes(_export(uu=uu))
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert abs(x[0] - y[0]) <= 0.01, (x, y)
        assert max(abs(p - q) for p, q in zip(x[2], y[2])) <= 1.5, (x, y)


@pytest.mark.parametrize("uu", [1.19, 1.7])
def test_b_the_wifi_arcs_keep_their_width_minimum(uu):
    def arcs(u):
        d = pymupdf.open()
        p = d.new_page(width=W / u, height=H / u)
        if u != 1.0:
            d.xref_set_key(p.xref, "UserUnit", str(u))
        sp = RP.PageSpace(p)
        for k, size in enumerate((16.0, 4.0, 2.0)):
            RP._draw_wifi(sp, 500 + 60 * k, 500, size, (0, 0.6, 0.9))
        sp.commit()
        pg = pymupdf.open("pdf", d.tobytes())[0]
        return sorted((round(x.get("width"), 3), tuple(round(v, 1) for v in x["rect"]))
                      for x in pg.get_drawings())
    assert arcs(uu) == arcs(1.0)


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_b_a_rotated_sheet_without_user_unit_is_unchanged(rot):
    a = _strokes(_export(rot=rot))
    assert all(w > 0 for w, _, _ in a if w)
    # same widths and dash patterns as the flat sheet (positions are rotated)
    flat = _strokes(_export())
    assert sorted((w, ds) for w, ds, _ in a) == sorted((w, ds) for w, ds, _ in flat)
