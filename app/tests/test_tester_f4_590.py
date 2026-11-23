"""Independent tester F4, RackReach 5.9.0-rc3 (tag v590-rc3), 21 Nov 2026.

Synthetic data only. The rc2 findings T4-1..T4-8 are re-checked by
tests/test_tester_f3_590.py (unchanged); this file attacks what rc3 added.

* SECTION A - every test FAILS on 5.9.0-rc3 (finding IDs T5-n).
* SECTION B - checks that PASS on rc3 (documented, not findings).
"""
from __future__ import annotations

import os
import time

import numpy as np
import pymupdf
import pytest
from sqlalchemy import select

from tests.test_ner_register import TMP, client, samples  # noqa: F401  (sets the env)
from tests.test_tester_570 import _full_project
from tests.test_tester_580 import _teap_two_venues

from app.domain.consolidation import Candidate, checked_level, compare, level_key
from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceRules
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.reports import (MARKER_MIN_LABEL_SIZE, MIN_SMALL_TEXT_SIZE, export_marked_pdf)
from app.domain.units import POINTS_PER_METRE, Scale

RULES = DistanceRules()
W, H = 2000.0, 1400.0


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


def at(den, confirmed=True):
    return Scale(den / POINTS_PER_METRE, "ratio", f"1:{den:g}", confirmed)


def _export(den, *, rot=0, uu=1.0, legend=True, notes=True, ref=True, radii=False):
    """A sheet whose DISPLAYED size is W x H whatever its /UserUnit (the
    MediaBox is W/uu x H/uu), with a critical NER carrying a note, an
    unconfirmed-match space with its line and distance, a *NER reference and
    a named area. Everything is placed in display coordinates, so a sheet
    with /UserUnit must come out exactly like one without."""
    tag = os.urandom(4).hex()
    src = os.path.join(TMP, f"f4-src-{tag}.pdf")
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
    sc = at(den)
    rooms = [NER(key="K", label="NER-CRIT-01", kind=ElementKind.NER_CRITICAL,
                 point=Point(dw * 0.5, dh * 0.5, 1), critical=True, total_ports=1, used_ports=0,
                 user_note="moved here because the other room has no power")]
    if ref:
        rooms.append(NER(key="R", label="*NER-REF", kind=ElementKind.NER_CRITICAL_REF,
                         point=Point(dw * 0.7, dh * 0.6, 1), critical=True, total_ports=1,
                         used_ports=0))
    res = compute_coverage(areas=[(Area(key="S", name="SPACE-A", page=1,
                                        label_point=Point(dw * 0.55, dh * 0.55, 1)),
                                   1.0, DemandType.GENERAL, None, False)],
                           ners=rooms[:1], scale_for_page={1: sc}, rules=RULES)
    areas = [{"id": 1, "page": 1, "label": "BTOC AREA",
              "polygon": [(dw * .2, dh * .2), (dw * .35, dh * .2), (dw * .35, dh * .35),
                          (dw * .2, dh * .35)]}]
    out = os.path.join(TMP, f"f4-out-{tag}.pdf")
    export_marked_pdf(src, out, coverage=res, ners=rooms, scale_for_page={1: sc}, rules=RULES,
                      draw_legend=legend, draw_notes=notes, areas=areas, draw_radii=radii)
    return out, (dw, dh)


def _spans(path):
    pg = pymupdf.open(path)[0]
    return sorted((s["text"], round(s["size"], 2), tuple(round(v, 1) for v in s["bbox"]))
                  for b in pg.get_text("dict")["blocks"] for ln in b.get("lines", [])
                  for s in ln["spans"])


def _render(path):
    pix = pymupdf.open(path)[0].get_pixmap(dpi=72)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]


def _red_dot_centre(path):
    a = _render(path).astype(int)
    m = (a[:, :, 0] > 170) & (a[:, :, 1] < 40) & (a[:, :, 2] < 40)
    ys, xs = np.nonzero(m)
    return float(xs.mean()), float(ys.mean())


def _wait(client, job_id, timeout=120.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if not last["running"]:
            return last
        time.sleep(0.15)
    return last


# ===========================================================================
# SECTION A - FAIL on 5.9.0-rc3
# ===========================================================================

# ---------------------------------------------------------------------------
# T5-1  checked_level applies NFKC first (rc3). NFKC turns the Portuguese /
#       Spanish ordinal indicators into letters: "2ª" -> "2a" -> stored
#       "LEVEL 02A", which IS the level "LEVEL 2A" (a real lettered level, a
#       mezzanine); "2º" -> "LEVEL 02O", "1º andar" -> "1O ANDAR". rc2 kept
#       them as typed ("2º"), a level that matched nothing. A NER typed "2ª"
#       is now the same floor as the 2A sheets for the merge and the
#       cross-sheet link rule: a false pairing. Expected: an ordinal
#       indicator is not turned into a level letter (keep it as typed, or
#       read "2º" / "2ª" as level 2).
# ---------------------------------------------------------------------------

def test_t5_1_an_ordinal_indicator_is_not_a_level_letter():
    assert level_key(checked_level("2ª")) != level_key("LEVEL 2A"), checked_level("2ª")
    a = Candidate(ref="a", label="NER 4", page=1, level=checked_level("2ª"), building=None)
    b = Candidate(ref="b", label="NER 4", page=2, level="LEVEL 2A", building=None)
    assert compare(a, b).blocking == "different_level", compare(a, b).to_dict()


@pytest.mark.parametrize("typed", ["1º", "2º", "1º andar"])
def test_t5_1b_the_masculine_ordinal_is_not_stored_as_letter_o(typed):
    stored = checked_level(typed)
    assert "O" not in stored.replace("ANDAR", "").replace("LEVEL", ""), stored


# ---------------------------------------------------------------------------
# T5-2  The manual now says "Sheets carrying a /UserUnit (the large master
#       plans) are drawn at the same sizes as any other". Positions, fonts and
#       rectangles are (checked in Section B), but every stroke width is
#       still multiplied by the unit: the NER ring, the tag borders, the
#       leader line, the legend frame - 0.9 pt -> 1.53 pt at UserUnit 1.7.
#       Cosmetic. Expected: the same line widths as on a sheet without it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uu", [1.19, 1.7])
def test_t5_2_line_widths_on_a_user_unit_sheet(uu):
    def widths(path):
        return sorted(round(d.get("width") or 0, 2) for d in pymupdf.open(path)[0].get_drawings())
    w0, w1 = widths(_export(500)[0]), widths(_export(500, uu=uu)[0])
    assert w1 == pytest.approx(w0, abs=0.02), list(zip(w0, w1))[:6]


# ---------------------------------------------------------------------------
# T5-3  (pre-existing since 4.8.0; the owner's backlog item behind the strict
#       xfail T3-2, whose reason says "moves every label") On a sheet with
#       /Rotate 90, 180 or 270 AND /UserUnit, not only the labels: every
#       mark of the export - the NER dot, the named area, the lines - is
#       drawn at about UserUnit x its position (rendered by MuPDF, the NER
#       laid out at display (700, 1000) lands at (844, 1167) with UserUnit
#       1.19 and at (1195, 1653) with 1.7), and larger. The venue books seen
#       so far carry /Rotate 90 on every page and a sheet over 200 in carries
#       /UserUnit, so the 900-series master plan this release targets is the
#       sheet that would combine both. The rc3 manual sentence "Sheets
#       carrying a /UserUnit are drawn at the same sizes as any other" does
#       not hold for them. Expected: the NER drawn where it is.
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="T5-3 (as T3-2, since 4.8.0): a sheet with both "
                   "/Rotate and /UserUnit draws every mark away from its place. None of "
                   "the 39 real books checked on 21 Nov 2026 has both (the 900 and 700 "
                   "series with /UserUnit are unrotated); left for a later version.")
@pytest.mark.parametrize("rot,uu", [(90, 1.19), (180, 1.19), (270, 1.7)])
def test_t5_3_on_a_rotated_user_unit_sheet_the_ner_is_drawn_where_it_is(rot, uu):
    out, (dw, dh) = _export(100, rot=rot, uu=uu, legend=False, notes=False, ref=False)
    ref_out, _ = _export(100, rot=rot, uu=1.0, legend=False, notes=False, ref=False)
    want = _red_dot_centre(ref_out)
    got = _red_dot_centre(out)
    assert abs(got[0] - want[0]) < 3 and abs(got[1] - want[1]) < 3, (got, want)


# ===========================================================================
# SECTION B - PASS on rc3
# ===========================================================================

# --- the central /UserUnit compensation, unrotated --------------------------

@pytest.mark.parametrize("den", [100, 500, 5000])
@pytest.mark.parametrize("legend", [True, False])
@pytest.mark.parametrize("uu", [1.19, 1.7, 2.0])
def test_b_a_user_unit_sheet_writes_every_text_at_the_same_size_and_place(den, legend, uu):
    """Legend, footnote, NER and reference names, note, area tag, distance:
    every span the same text, size and box as on a sheet without /UserUnit."""
    s0 = _spans(_export(den, legend=legend)[0])
    s1 = _spans(_export(den, legend=legend, uu=uu)[0])
    assert [s[0] for s in s0] == [s[0] for s in s1]
    for a, b in zip(s0, s1):
        assert abs(a[1] - b[1]) < 0.05, (a, b)
        assert max(abs(x - y) for x, y in zip(a[2], b[2])) < 1.5, (a, b)


@pytest.mark.parametrize("uu", [1.19, 1.7])
def test_b_a_user_unit_sheet_draws_every_shape_at_the_same_place(uu):
    def rects(path):
        return sorted(tuple(round(v) for v in d["rect"]) for d in pymupdf.open(path)[0].get_drawings())
    r0, r1 = rects(_export(500)[0]), rects(_export(500, uu=uu)[0])
    assert len(r0) == len(r1)
    for a, b in zip(r0, r1):
        assert max(abs(x - y) for x, y in zip(a, b)) <= 1.5, (a, b)


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_b_rotation_without_user_unit_is_unchanged_by_the_compensation(rot):
    """The rc3 PageSpace changes only act when /UserUnit != 1: a rotated
    sheet renders the NER where a flat one does (relative to the sheet)."""
    out, (dw, dh) = _export(500, rot=rot, legend=False, notes=False, ref=False)
    cx, cy = _red_dot_centre(out)
    assert abs(cx - dw * 0.5) < 6 and abs(cy - dh * 0.5) < 6, (cx, cy, dw, dh)


# --- references and the rest on a very small scale --------------------------

@pytest.mark.parametrize("den", [600, 2000, 10000, 50000])
@pytest.mark.parametrize("uu", [1.0, 1.7])
def test_b_names_are_never_under_4pt_and_stay_in_their_tags(den, uu):
    out, _ = _export(den, uu=uu, legend=False, notes=False)
    pg = pymupdf.open(out)[0]
    tags = [d["rect"] for d in pg.get_drawings() if d.get("fill") == (1.0, 1.0, 1.0)]
    seen = set()
    for b in pg.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                if s["text"] in ("NER-CRIT-01", "*NER-REF", "BTOC AREA"):
                    seen.add(s["text"])
                    assert s["size"] >= MARKER_MIN_LABEL_SIZE - 0.01, s
                    x0, y0, x1, y1 = s["bbox"]
                    h = y1 - y0
                    assert any(t.x0 - 0.6 <= x0 and x1 <= t.x1 + 0.6
                               and t.y0 - 0.8 <= y0 + 0.25 * h and y1 - 0.25 * h <= t.y1 + 0.8
                               for t in tags), (s["text"], s["bbox"], tags)
                elif s["text"].endswith(" m") and len(s["text"]) < 8:
                    assert s["size"] >= MIN_SMALL_TEXT_SIZE - 0.01, s
    assert seen == {"NER-CRIT-01", "*NER-REF", "BTOC AREA"}


def test_b_the_reference_shrinks_with_the_room():
    def dots(path):
        return sorted(round(d["rect"].width, 1) for d in pymupdf.open(path)[0].get_drawings()
                      if d.get("fill") not in (None, (1.0, 1.0, 1.0))
                      and abs(d["rect"].width - d["rect"].height) < 0.05
                      and d.get("color") == (0.0, 0.0, 0.0))
    assert dots(_export(2000, legend=False, notes=False)[0])[-2:] == [6.0, 6.0]


# --- NFKC: what it should do ------------------------------------------------

@pytest.mark.parametrize("typed,stored", [
    ("０２", "LEVEL 02"), ("Ｌ２", "LEVEL 02"), ("B１", "LEVEL B1"), ("Nível 2", "LEVEL 02"),
    ("NÍVEL 2", "LEVEL 02"), ("nível 2", "LEVEL 02"), ("LEVEL 2", "LEVEL 02"),
    ("Térreo", "TÉRREO"), ("ﬁrst floor", "FIRST FLOOR"), ("𝟐", "LEVEL 02"),
])
def test_b_nfkc_writes_compatibility_forms_plainly(typed, stored):
    assert checked_level(typed) == stored


@pytest.mark.parametrize("bad", ["ㅤ", "ﾠ", "02️", "02︀", "02\U000e0100",
                                 "02᠋", "02⠀", "02͏", "ᅟ", "02឵"])
def test_b_blank_looking_characters_are_refused(bad):
    with pytest.raises(ValueError):
        checked_level(bad)


def test_b_page_level_status_codes(client, samples):
    pid = _full_project(client, samples, _uid("F4 page level"))
    r = client.post(f"/api/projects/{pid}/pages/1/level", json={"level": "x" * 41})
    assert r.status_code == 422, r.text
    r = client.post(f"/api/projects/{pid}/pages/999/level", json={"level": "2"})
    assert r.status_code == 404, r.text


# --- the cancel path of a background TEAP import ---------------------------

def _two_venue_project(client, samples, tag):
    from app.db import SessionLocal
    from app.models import TeapSpace
    pid = _full_project(client, samples, _uid(tag))
    path = _teap_two_venues(samples["teap"], os.path.join(TMP, f"f4_two_{pid}.xlsx"))
    with open(path, "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap-all.xlsx", fh, "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")})
    tid, proposed = r.json()["id"], r.json()["proposed"]
    body = {"sheet": proposed["sheet"], "header_row": proposed["header_row"],
            "mapping": proposed["mapping"]}
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import", json={**body, "allow_all_venues": True})
    assert r.status_code == 200 and r.json()["venue_filter"] is None, r.text

    def state():
        from app.models import TeapFile
        with SessionLocal() as db:
            return ({t.venue_code for t in db.execute(select(TeapSpace).where(
                TeapSpace.project_id == pid)).scalars()}, db.get(TeapFile, tid).venue_filter)
    return pid, tid, body, state


def test_b_a_cancel_before_storing_puts_everything_back(client, samples, monkeypatch):
    from app import services as S
    from app.api import routes as R
    pid, tid, body, state = _two_venue_project(client, samples, "F4 cancel early")
    before = state()
    real = S.compute_demand

    def demand_and_cancel(*a, **k):            # runs between report(0.6) and report(0.8)
        out = real(*a, **k)
        for ev in list(R.job_manager._cancels.values()):
            ev.set()
        return out
    monkeypatch.setattr(S, "compute_demand", demand_and_cancel)
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import?background=true",
                    json={**body, "venue_filter": "SYN"})
    j = _wait(client, r.json()["job_id"])
    assert j["state"] == "cancelled", j
    assert state() == before and before[1] is None and "OTH" in before[0]


def test_b_a_cancel_after_the_commit_ends_done_and_consistent(client, samples, monkeypatch):
    from app import services as S
    from app.api import routes as R
    pid, tid, body, state = _two_venue_project(client, samples, "F4 cancel late")
    real = S.audit

    def audit_and_cancel(db, project_id, action, *a, **k):   # teap.imported, before commit
        out = real(db, project_id, action, *a, **k)
        if action == "teap.imported":
            for ev in list(R.job_manager._cancels.values()):
                ev.set()
        return out
    monkeypatch.setattr(S, "audit", audit_and_cancel)
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import?background=true",
                    json={**body, "venue_filter": "SYN"})
    j = _wait(client, r.json()["job_id"])
    assert j["state"] == "done", j
    assert state() == ({"SYN"}, "SYN")
