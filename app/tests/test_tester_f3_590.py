"""Independent tester F3, RackReach 5.9.0-rc2 (tag v590-rc2), 21 Nov 2026.

Synthetic data only. Two sections:

* SECTION A - every test there FAILS on 5.9.0-rc2 and reproduces a finding
  (T4-n, see /home/claude/tester590/FINDINGS.md). The docstring says what is
  wrong and what the expected behaviour is.
* SECTION B - checks that PASS on rc2: they document what was attacked and
  found right (the reach-circle switch through every route, background and
  multi-book; the undo of a merge in several orders; the level rule; the
  marker size at the cap / floor; the failed TEAP re-import put back).
"""
from __future__ import annotations

import os
import time

import pymupdf
import pytest
from sqlalchemy import select

from tests.test_ner_register import TMP, client, samples  # noqa: F401  (sets the env)
from tests.test_tester_570 import _full_project, _live_ners
from tests.test_tester_580 import _merge, _ner, _patch, _teap_two_venues

from app.domain.consolidation import (Candidate, checked_level, compare, level_key,
                                      normalise_level)
from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceRules
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.reports import (MARKER_LABEL_SIZE, MARKER_MIN_LABEL_SIZE, MARKER_MIN_RADIUS,
                                MARKER_RADIUS, export_marked_pdf, marker_factor)
from app.domain.units import POINTS_PER_METRE, Scale

RULES = DistanceRules()


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


def at(denominator: float, confirmed: bool = True) -> Scale:
    return Scale(metres_per_unit=denominator / POINTS_PER_METRE, source="ratio",
                 label=f"1:{denominator:g}", confirmed=confirmed)


def _export(scale, *, uu=None, rot=0, label="NER-CRIT-01", kind=ElementKind.NER_CRITICAL,
            w=2000, h=1400, pt=(1000, 700)):
    """One NER on a synthetic sheet, exported; the output page."""
    tag = os.urandom(4).hex()
    src = os.path.join(TMP, f"f3-src-{tag}.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    if rot:
        page.set_rotation(rot)
    if uu:
        doc.xref_set_key(page.xref, "UserUnit", str(uu))
    doc.save(src)
    doc.close()
    rooms = [NER(key="K", label=label, kind=kind, point=Point(*pt, 1), critical=True,
                 total_ports=1, used_ports=0)]
    sfp = {1: scale} if scale else {}
    res = compute_coverage(areas=[(Area(key="S", name="S", page=1,
                                        label_point=Point(pt[0] + 60, pt[1] + 60, 1)),
                                   1.0, DemandType.GENERAL, None, True)],
                           ners=rooms, scale_for_page=sfp, rules=RULES)
    out = os.path.join(TMP, f"f3-out-{tag}.pdf")
    export_marked_pdf(src, out, coverage=res, ners=rooms, scale_for_page=sfp, rules=RULES,
                      draw_radii=False, draw_links=False, draw_legend=False)
    return pymupdf.open(out)[0]


def _marker_dot(page, colour=(0.8, 0.0, 0.0)):
    """The filled, black-ringed room dot (the largest filled circle)."""
    dots = [d["rect"] for d in page.get_drawings()
            if d.get("fill") is not None and d.get("fill") != (1.0, 1.0, 1.0)
            and abs(d["rect"].width - d["rect"].height) < 0.05
            and d.get("color") == (0.0, 0.0, 0.0)]
    return max(dots, key=lambda r: r.width) if dots else None


def _span(page, text):
    hits = [s for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
            for s in ln.get("spans", []) if s["text"] == text]
    return hits[0] if hits else None


# ===========================================================================
# SECTION A - FAIL on 5.9.0-rc2
# ===========================================================================

# ---------------------------------------------------------------------------
# T4-1  A sheet whose scale is NOT confirmed (the scale the import detected,
#       stored with scale_confirmed = False on every new book) gets the
#       shrunken marker. The manual (Reports, "What the marked-up PDF
#       draws"), the CHANGELOG and the comment in reports.py all say "a sheet
#       with no confirmed scale ... keeps the full size"; marker_factor only
#       looks at "is there a Scale", and services.scale_map hands the export
#       unconfirmed scales too. A detected scale that is wrong (a 1:5000
#       inset read as the sheet's scale) shrinks every NER to the 3 pt floor.
# ---------------------------------------------------------------------------

def test_t4_1_an_unconfirmed_scale_keeps_the_full_marker():
    """Expected: an unconfirmed 1:500 sheet draws the 24 pt dot and the 9 pt
    name, as documented. rc2: 11.3 pt dot, 4.25 pt name."""
    page = _export(at(500, confirmed=False))
    dot = _marker_dot(page)
    assert dot is not None
    assert round(dot.width, 1) == 2 * MARKER_RADIUS, dot
    assert _span(page, "NER-CRIT-01")["size"] == pytest.approx(MARKER_LABEL_SIZE)


def _orm_project_one_sheet(mpu, confirmed, kind="NER_CRITICAL"):
    """A one-book project built in the database: one 2000 x 1400 sheet with
    the given scale, one NER at its centre."""
    from app import migrate
    from app.db import SessionLocal, init_db
    from app.models import Drawing, Element, Page, Project
    init_db()
    path = os.path.join(TMP, f"f3-orm-{os.urandom(4).hex()}.pdf")
    doc = pymupdf.open()
    doc.new_page(width=2000, height=1400)
    doc.save(path)
    doc.close()
    with SessionLocal() as db:
        p = Project(name=_uid("F3 orm"), venue_code="VEN")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="VEN_900_X.pdf", stored_path=path,
                    sha256=os.urandom(32).hex(), page_count=1)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=2000, height=1400,
                    metres_per_unit=mpu, scale_label="1:500", scale_source="detected",
                    scale_confirmed=confirmed, sheet_number="VEN-901-X"))
        db.add(Element(project_id=p.id, drawing_id=d.id, page=1, kind=kind,
                       label="NER 90", x=1000, y=700, origin="manual", confidence=1.0,
                       confirmed=True))
        db.commit()
        migrate.backfill_assets(db, db.get(Project, p.id))
        return p.id


def test_t4_1b_through_the_report_route_an_unconfirmed_scale_shrinks_the_ner(client):
    """The same through POST /reports/pdf: the detected, unconfirmed 1:500
    scale of a freshly imported master plan. Expected: the full 24 pt dot."""
    pid = _orm_project_one_sheet(500 / POINTS_PER_METRE, confirmed=False)
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false")
    assert r.status_code == 200, r.text
    doc = pymupdf.open(stream=client.get(r.json()["download"]).content, filetype="pdf")
    dot = _marker_dot(doc[0])
    assert dot is not None
    assert round(dot.width, 1) == 2 * MARKER_RADIUS, dot


# ---------------------------------------------------------------------------
# T4-2  On a sheet with /UserUnit (every sheet over 200 in - the 340-inch
#       900-series master plan this release was made for has to carry one,
#       UserUnit >= 1.7) the dot is sized right but its name and tag are not:
#       PyMuPDF multiplies the font size and the rectangle's size by the unit
#       (draw_note was corrected for this in 5.8.0; the room label never
#       was). At 1:500 with UserUnit 1.7 the name is 7.2 pt instead of
#       4.25 pt, and it is no longer centred under its dot (the tag grows to
#       the right). The "name shrinks in step with the dot" rule does not
#       hold on exactly the sheets it is for. (At 1:100 the full-size label
#       is 15.3 pt instead of 9 pt - pre-existing since 4.8.0.)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uu", [1.19, 1.7])
def test_t4_2_on_a_user_unit_sheet_the_name_shrinks_with_the_dot(uu):
    """Expected: the same name size and the same name/dot proportion as on a
    sheet without /UserUnit, and the name centred under the dot."""
    plain = _export(at(500))
    big = _export(at(500), uu=uu)
    s0, s1 = _span(plain, "NER-CRIT-01"), _span(big, "NER-CRIT-01")
    d0, d1 = _marker_dot(plain), _marker_dot(big)
    assert d1.width == pytest.approx(d0.width, abs=0.05)       # the dot is right
    assert s1["size"] == pytest.approx(s0["size"], abs=0.05), (s0["size"], s1["size"])
    cx = (s1["bbox"][0] + s1["bbox"][2]) / 2
    assert cx == pytest.approx((d1.x0 + d1.x1) / 2, abs=1.0), (cx, d1)


# ---------------------------------------------------------------------------
# T4-3  F2-2 is fixed for "LEVEL2" but not for a level with a letter:
#       "LEVELB1", "LEVELP1", "FLOORB2" (word touching a basement / parking
#       level - the sheet reader keeps them as printed, its pattern is
#       (LEVEL|LVL|FLOOR)\s*[-:]?\s*[A-Z0-9]{1,6}). normalise_level writes
#       them "LEVEL B1" (its separator is optional), level_key keeps
#       "LEVELB1". The composite map groups a "LEVELB1" sheet with a
#       "LEVEL B1" sheet; the merge calls the two different floors and blocks
#       the pair. "LEVEL-B1" is worse: normalise_level makes it "LEVEL -B1"
#       (a minus B1). A missed merge, not a false one.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["LEVELB1", "LEVELP1", "FLOORB2", "LVLB1", "LEVEL-B1"])
def test_t4_3_level_key_agrees_with_normalise_level_on_lettered_levels(raw):
    assert level_key(raw) == level_key(normalise_level(raw)), \
        (raw, level_key(raw), normalise_level(raw))


def test_t4_3b_the_same_ner_on_levelb1_and_level_b1_is_not_blocked_as_another_floor():
    a = Candidate(ref="a", label="NER 4", page=1, level="LEVELB1", building=None)
    b = Candidate(ref="b", label="NER 4", page=2, level="LEVEL B1", building=None)
    assert compare(a, b).blocking != "different_level", compare(a, b).to_dict()


# ---------------------------------------------------------------------------
# T4-4  checked_level refuses Cc / Cf / Co / Cs, but several characters that
#       render as nothing are other categories and pass: U+3164 HANGUL
#       FILLER and U+115F / U+1160 (Lo), U+2800 BRAILLE PATTERN BLANK (So),
#       U+034F COMBINING GRAPHEME JOINER, U+FE0F VARIATION SELECTOR-16 and
#       U+17B4 (Mn). "ㅤ" alone is stored as a level that looks empty;
#       "02" + U+FE0F looks like "02" but is not "LEVEL 02" and level_key
#       calls it another floor. The rule F2-1 promised ("no invisible
#       character") does not hold for these.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["ㅤ", "02ㅤ", "02⠀", "02͏", "02️",
                                 "ᅟ", "02឴"])
def test_t4_4_a_level_with_an_invisible_character_is_refused(bad):
    with pytest.raises(ValueError):
        checked_level(bad)


# ---------------------------------------------------------------------------
# T4-5  F2-4 in background mode puts the old sheet / mapping / venue filter
#       back on ANY exception, including JobCancelled. A cancel pressed while
#       the job is storing the rows (after report(0.8), before report(1.0) -
#       the next progress call comes after db.commit()) lets the import
#       commit, then report(1.0) raises JobCancelled, _put_back restores the
#       OLD venue filter and mapping over the NEW spaces, and the job says
#       "cancelled by the user". 5.8.0 left the file consistent with its
#       spaces (both new). Expected: a cancelled import either changed
#       nothing, or the file states the filter its spaces were imported with.
# ---------------------------------------------------------------------------

def _wait(client, job_id, timeout=120.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if not last["running"]:
            return last
        time.sleep(0.15)
    return last


def test_t4_5_a_teap_import_cancelled_while_storing_leaves_file_and_spaces_consistent(
        client, samples, monkeypatch):
    from app import services as S
    from app.api import routes as R
    from app.db import SessionLocal
    from app.models import TeapFile, TeapSpace
    pid = _full_project(client, samples, _uid("F3 cancel import"))
    path = _teap_two_venues(samples["teap"], os.path.join(TMP, f"f3_two_{pid}.xlsx"))
    with open(path, "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap-all.xlsx", fh, "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")})
    tid, proposed = r.json()["id"], r.json()["proposed"]
    body = {"sheet": proposed["sheet"], "header_row": proposed["header_row"],
            "mapping": proposed["mapping"]}
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={**body, "allow_all_venues": True})
    assert r.status_code == 200 and r.json()["venue_filter"] is None, r.text
    with SessionLocal() as db:
        all_venues = {t.venue_code for t in db.execute(select(TeapSpace).where(
            TeapSpace.project_id == pid)).scalars()}
    assert "OTH" in all_venues

    # The person presses Cancel while the rows are being stored.
    real = S._lay_decisions

    def lay_and_cancel(*a, **k):
        out = real(*a, **k)
        for ev in list(R.job_manager._cancels.values()):
            ev.set()
        return out
    monkeypatch.setattr(S, "_lay_decisions", lay_and_cancel)
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import?background=true",
                    json={**body, "venue_filter": "SYN"})
    assert r.status_code == 200, r.text
    j = _wait(client, r.json()["job_id"])
    monkeypatch.setattr(S, "_lay_decisions", real)
    with SessionLocal() as db:
        tf = db.get(TeapFile, tid)
        venues = {t.venue_code for t in db.execute(select(TeapSpace).where(
            TeapSpace.project_id == pid)).scalars()}
        filt = tf.venue_filter
    imported_with_filter = venues == {"SYN"}
    # Consistent: either the old all-venue import is still there (nothing
    # changed), or the new spaces and the file both say SYN.
    assert (not imported_with_filter and filt is None) or (imported_with_filter and filt == "SYN"), \
        (j["state"], j.get("stage"), sorted(venues), filt)


# ---------------------------------------------------------------------------
# T4-6  F2-4, synchronous path: only ValidationError / ValueError are put
#       back. A header row past the last row of the sheet makes read_sheet
#       call next() on an empty iterator: StopIteration escapes, the request
#       is a bare 500 ("coroutine raised StopIteration"), and the new sheet /
#       header row / mapping / venue filter stay committed on the TEAP file.
#       In background mode (the screen's path) they are put back, but the
#       job error reads "StopIteration: " - no sentence for the person.
# ---------------------------------------------------------------------------

def _teap_file(pid):
    from app.db import SessionLocal
    from app.models import TeapFile
    with SessionLocal() as db:
        tf = db.execute(select(TeapFile).where(TeapFile.project_id == pid)).scalars().first()
        return tf.id, (tf.sheet, tf.header_row, dict(tf.mapping or {}), tf.venue_filter)


def test_t4_6_a_header_row_past_the_sheet_is_refused_and_put_back(client, samples):
    """Expected: 422 with a sentence, and the file as it was."""
    from app.db import SessionLocal
    from app.models import TeapFile
    pid = _full_project(client, samples, _uid("F3 sync failed reimport"))
    tid, before = _teap_file(pid)
    try:
        r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                        json={"sheet": before[0], "header_row": 900, "mapping": before[2],
                              "venue_filter": "NOPE"})
        status = r.status_code
    except Exception as exc:                                   # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    with SessionLocal() as db:
        tf = db.get(TeapFile, tid)
        after = (tf.sheet, tf.header_row, dict(tf.mapping or {}), tf.venue_filter)
    assert status == 422 and after == before, (status, after[1], after[3])


def test_t4_6b_in_background_the_error_is_a_sentence(client, samples):
    pid = _full_project(client, samples, _uid("F3 bg header row"))
    tid, before = _teap_file(pid)
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import?background=true",
                    json={"sheet": before[0], "header_row": 900, "mapping": before[2]})
    j = _wait(client, r.json()["job_id"])
    assert j["state"] == "error"
    assert _teap_file(pid)[1] == before                        # put back: right
    assert "StopIteration" not in (j.get("error") or ""), j.get("error")


# ---------------------------------------------------------------------------
# T4-7  POST /pages/{n}/level answers every refusal of the level rule with
#       404 Not Found (the route maps ValidationError to 404, a status meant
#       for "page not found"). The NER field answers the same rule with 400.
#       Since F2-1 the refusals are routine ("at most 40 characters",
#       "invisible characters"). Expected: 422 (or 400), as the NER field.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["\u200b", "x" * 41])
def test_t4_7_a_refused_page_level_is_not_404(client, samples, bad):
    pid = _full_project(client, samples, _uid("F3 page level status"))
    r = client.post(f"/api/projects/{pid}/pages/1/level", json={"level": bad})
    assert r.status_code in (400, 422), (r.status_code, r.text)


# ---------------------------------------------------------------------------
# T4-8  Only the room markers shrink. On a small-scale sheet the TEAP space
#       dot (5 pt radius, status colour), the "match not confirmed" box
#       (24 x 18 pt), the Wi-Fi sign and the named-area tag (9 pt) keep their
#       full size, so at 1:1000 and smaller a red "out of limit" space dot is
#       LARGER than the red critical NER that serves it (3 - 3.4 pt), and the
#       space's 24 pt box is wider than the whole NER. Design observation:
#       nothing documents the other symbols, but the reader is now told
#       "the dot stays inside its room".
# ---------------------------------------------------------------------------

def test_t4_8_on_a_master_plan_a_space_dot_is_not_larger_than_its_ner():
    page = _export(at(1000))
    ner = _marker_dot(page)
    space_dots = [d["rect"] for d in page.get_drawings()
                  if d.get("fill") is not None and d.get("color") != (0.0, 0.0, 0.0)
                  and d.get("fill") != (1.0, 1.0, 1.0)
                  and abs(d["rect"].width - d["rect"].height) < 0.05]
    assert space_dots, "the space is drawn"
    assert max(r.width for r in space_dots) <= ner.width + 0.01, \
        (ner.width, [r.width for r in space_dots])


# ===========================================================================
# SECTION B - checks that PASS on rc2 (documented, not findings)
# ===========================================================================

# --- the marker size -------------------------------------------------------

def test_b_marker_factor_at_the_cap_and_the_floor():
    cap = MARKER_RADIUS * POINTS_PER_METRE / MARKER_RADIUS ** 2   # r = 12 pt -> 1:236.2
    d_cap = POINTS_PER_METRE / MARKER_RADIUS
    d_floor = POINTS_PER_METRE / MARKER_MIN_RADIUS
    assert cap > 0
    assert marker_factor(at(d_cap)) == pytest.approx(1.0)
    assert marker_factor(at(d_cap * 1.001)) < 1.0
    assert marker_factor(at(d_floor)) * MARKER_RADIUS == pytest.approx(MARKER_MIN_RADIUS)
    assert marker_factor(at(d_floor * 10)) * MARKER_RADIUS == pytest.approx(MARKER_MIN_RADIUS)
    assert marker_factor(at(d_floor * 0.999)) * MARKER_RADIUS > MARKER_MIN_RADIUS


@pytest.mark.parametrize("r", [float("nan"), float("inf"), -1.0, 0.0, -float("inf")])
def test_b_an_absurd_scale_object_draws_full_size(r):
    class Odd:
        def to_pdf_units(self, m):
            return r
    assert marker_factor(Odd()) == 1.0


def test_b_scale_rejects_nan_zero_negative():
    from app.domain.units import ScaleError
    for v in (float("nan"), 0.0, -1.0, float("inf")):
        with pytest.raises(ScaleError):
            Scale(metres_per_unit=v, source="x", label="x")


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_b_a_rotated_sheet_gets_the_same_marker_and_name(rot):
    flat, turned = _export(at(500)), _export(at(500), rot=rot)
    assert _marker_dot(turned).width == pytest.approx(_marker_dot(flat).width, abs=0.05)
    assert _span(turned, "NER-CRIT-01")["size"] == pytest.approx(
        _span(flat, "NER-CRIT-01")["size"], abs=0.01)


def test_b_the_name_tag_contains_its_name_and_clears_the_dot_at_every_size():
    for d in (100, 300, 500, 1000, 3000, 20000):
        page = _export(at(d))
        dot, s = _marker_dot(page), _span(page, "NER-CRIT-01")
        tags = [x["rect"] for x in page.get_drawings() if x.get("fill") == (1.0, 1.0, 1.0)]
        assert tags, d
        tag = tags[0]
        x0, y0, x1, y1 = s["bbox"]
        assert tag.x0 <= x0 + 0.5 and x1 <= tag.x1 + 0.5, (d, tag, s["bbox"])
        assert tag.y0 >= dot.y1 - 0.01, (d, tag, dot)          # under the dot, not on it
        assert s["size"] >= MARKER_MIN_LABEL_SIZE - 0.01


# --- the reach-circle switch through the routes ----------------------------

def _reach_circles(page, radii_units):
    """Unfilled circles whose radius is one of the reach radii (in units)."""
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if d.get("fill") is None and abs(r.width - r.height) < 0.5:
            if any(abs(r.width / 2 - want) < 0.5 for want in radii_units):
                out.append(round(r.width / 2, 1))
    return out


def test_b_multi_book_background_export_honours_radii_false(client):
    from app.db import SessionLocal
    from app.models import AuditLog
    from tests.test_multi_book_410 import _ner as _orm_ner, _two_books
    pid, a, b = _two_books(_uid("F3 two books radii"))
    _orm_ner(pid, a, 1, "NER 31", 300, 300)
    _orm_ner(pid, b, 2, "NER 32", 400, 300)
    # 1 unit = 1 m on book a, 0.5 m on book b; limit / slack
    lim = [RULES.general_limit_m / RULES.routing_slack_factor,
           RULES.photographer_limit_m / RULES.routing_slack_factor]
    want = [x / 1.0 for x in lim] + [x / 0.5 for x in lim]
    found = {}
    for radii in (True, False):
        r = client.post(f"/api/projects/{pid}/reports/pdf?background=true&radii={str(radii).lower()}")
        assert r.status_code == 200 and r.json().get("background"), r.text
        j = _wait(client, r.json()["job_id"])
        assert j["state"] == "done", j
        doc = pymupdf.open(stream=client.get(j["result"]["download"]).content, filetype="pdf")
        found[radii] = [c for pg in doc for c in _reach_circles(pg, want)]
        doc.close()
    assert found[True], "the default draws the reach circles"
    assert found[False] == [], found[False]
    with SessionLocal() as db:
        logs = [x.payload for x in db.execute(select(AuditLog).where(
            AuditLog.project_id == pid, AuditLog.action == "report.generated")).scalars()]
    assert [lg.get("radii") for lg in logs if lg.get("format", "").startswith("pdf")
            or "pdf" in str(lg.get("format"))][-2:] == [True, False], logs


def test_b_radii_query_rejects_garbage(client):
    pid = _orm_project_one_sheet(500 / POINTS_PER_METRE, confirmed=True)
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false&radii=maybe")
    assert r.status_code == 422


# --- the merge undo in several orders --------------------------------------

def _project_pair(client, samples, tag):
    pid = _full_project(client, samples, _uid(tag))
    ns = {n["display_name"]: n for n in _live_ners(client, pid)}
    return pid, ns["NER_20.1"]["id"], ns["NER_22.1"]["id"]


def _undo(client, pid, cid):
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text


def test_b_undo_type_on_the_duplicate_before_merge_then_clear_on_the_primary(client, samples):
    pid, a, b = _project_pair(client, samples, "F3 undo clear")
    assert _patch(client, pid, b, "3").status_code == 200
    cid = _merge(client, pid, a, b)
    assert _ner(client, pid, a)["level"] == "LEVEL 03"
    assert _patch(client, pid, a, "").status_code == 200        # cleared after the merge
    _undo(client, pid, cid)
    assert _ner(client, pid, a)["level"] is None
    assert _ner(client, pid, b)["level"] == "LEVEL 03"


def test_b_undo_after_retyping_another_level_keeps_it(client, samples):
    pid, a, b = _project_pair(client, samples, "F3 undo retype")
    assert _patch(client, pid, b, "3").status_code == 200
    cid = _merge(client, pid, a, b)
    assert _patch(client, pid, a, "4").status_code == 200
    _undo(client, pid, cid)
    assert _ner(client, pid, a)["level"] == "LEVEL 04"


def test_b_undo_of_a_merge_recorded_without_primary_after(client, samples):
    """A merge recorded by 5.8.0 (no 'primary_after' in the snapshot)."""
    from app.db import SessionLocal
    from app.models import NerConsolidation
    pid, a, b = _project_pair(client, samples, "F3 undo old record")
    assert _patch(client, pid, b, "3").status_code == 200
    cid = _merge(client, pid, a, b)
    with SessionLocal() as db:
        rec = db.get(NerConsolidation, cid)
        snap = dict(rec.undo_snapshot)
        snap.pop("primary_after", None)
        rec.undo_snapshot = snap
        db.commit()
    _undo(client, pid, cid)
    assert _ner(client, pid, a)["level"] is None


def test_b_two_merges_undone_in_either_order(client, samples):
    pid = _full_project(client, samples, _uid("F3 two merges"))
    ns = {n["display_name"]: n for n in _live_ners(client, pid)}
    names = sorted(ns)
    assert len(names) >= 3
    a, b, c = (ns[x]["id"] for x in ("NER_20.1", "NER_22.1",
                                     next(x for x in names if x not in ("NER_20.1", "NER_22.1"))))
    assert _patch(client, pid, b, "3").status_code == 200
    c1 = _merge(client, pid, a, b)
    c2 = _merge(client, pid, a, c)
    _undo(client, pid, c1)
    _undo(client, pid, c2)
    assert _ner(client, pid, a)["level"] is None


# --- the level rule --------------------------------------------------------

def test_b_checked_level_edges():
    assert checked_level(None) is None and checked_level("") is None
    assert checked_level("    ") is None
    assert checked_level("\u00a0 2 \u2003") == "LEVEL 02"          # NBSP / em space collapse
    with pytest.raises(ValueError):
        checked_level("2\t")                                     # a tab is a control char
    assert checked_level("ß" * 20) == "SS" * 20                  # 40 as stored
    with pytest.raises(ValueError):
        checked_level("ß" * 20 + "a")                            # 41 as stored
    with pytest.raises(ValueError):
        checked_level("ﬃ" * 14)                                  # 14 -> 42
    for bad in (2, True, ["x"], b"2", 2.0):
        with pytest.raises(ValueError):
            checked_level(bad)


@pytest.mark.parametrize("a,b,same", [
    ("LEVEL-02", "2", True), ("Level: 2", "LEVEL 02", True), ("NIVEL 3", "3", True),
    ("LEVEL 2A", "LEVEL 2", False), ("B1", "1", False), ("LEVEL -1", "LEVEL 1", False),
    ("L-1", "LEVEL -1", True), ("LEVEL2", "LEVEL 02", True), ("FLOOR2", "2", True),
    ("LVL2", "2", True), ("LEVEL 0", "0", True), ("LEVEL 10", "LEVEL 1", False),
    ("L1M", "LEVEL 01M", True), ("MEZZANINE", "LEVEL 01", False),
])
def test_b_level_key_pairs(a, b, same):
    assert (level_key(a) == level_key(b)) is same, (a, b, level_key(a), level_key(b))
    # and the merge says the same
    ca = Candidate(ref="a", label="NER 4", page=1, level=a, building=None)
    cb = Candidate(ref="b", label="NER 4", page=2, level=b, building=None)
    assert (compare(ca, cb).blocking == "different_level") is (not same)


def test_b_page_level_and_ner_level_share_the_rule(client, samples):
    pid = _full_project(client, samples, _uid("F3 page level"))
    n = _live_ners(client, pid)[0]
    for bad in ("​", "x" * 41, "ß" * 21):
        r = client.post(f"/api/projects/{pid}/pages/1/level", json={"level": bad})
        assert 400 <= r.status_code < 500, (bad, r.status_code, r.text)   # see T4-7
        assert _patch(client, pid, n["id"], bad).status_code in (400, 422)


# --- the failed re-import, background (the UI's path) -----------------------

def test_b_a_failed_background_reimport_puts_the_filter_back(client, samples):
    from app.db import SessionLocal
    from app.models import TeapFile
    pid = _full_project(client, samples, _uid("F3 bg failed reimport"))
    with SessionLocal() as db:
        tf = db.execute(select(TeapFile).where(TeapFile.project_id == pid)).scalars().first()
        before = (tf.sheet, tf.header_row, dict(tf.mapping or {}), tf.venue_filter)
        tid = tf.id
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import?background=true",
                    json={"sheet": before[0], "header_row": before[1], "mapping": before[2],
                          "venue_filter": "TYPO"})
    j = _wait(client, r.json()["job_id"])
    assert j["state"] == "error" and "nothing to import" in (j.get("error") or ""), j
    with SessionLocal() as db:
        tf = db.get(TeapFile, tid)
        assert (tf.sheet, tf.header_row, dict(tf.mapping or {}), tf.venue_filter) == before


# --- the composite map PDF and the switch ----------------------------------

from tests.test_tester_570rc2 import composite_pid  # noqa: E402,F401


def _reach_strokes(page):
    return [d for d in page.get_drawings()
            if str(d.get("dashes") or "").replace(" ", "").startswith(("[128]", "[35]"))]


@pytest.mark.parametrize("legend", [True, False])
def test_b_composite_radii_false_draws_no_reach_and_no_reach_rows(client, composite_pid, legend):
    from app.db import SessionLocal
    from app.models import AuditLog
    pid = composite_pid
    pages = {}
    for radii in (True, False):
        r = client.post(f"/api/projects/{pid}/composite-report?legend={str(legend).lower()}"
                        f"&radii={str(radii).lower()}")
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job_id"])
        assert j["state"] == "done", j
        doc = pymupdf.open(stream=client.get(j["result"]["download"]).content, filetype="pdf")
        pages[radii] = (len(_reach_strokes(doc[0])), doc[0].get_text())
        doc.close()
    assert pages[True][0] >= 2 and pages[False][0] == 0, (pages[True][0], pages[False][0])
    if legend:
        assert "90 m reach" in pages[True][1] and "90 m reach" not in pages[False][1]
        assert "70 m reach" not in pages[False][1] and "Wi-Fi point" in pages[False][1]
    else:
        assert "90 m reach" not in pages[True][1]
    assert "NER_21.1" in pages[False][1]
    with SessionLocal() as db:
        logs = [x.payload for x in db.execute(select(AuditLog).where(
            AuditLog.project_id == pid, AuditLog.action == "report.generated")).scalars()
            if (x.payload or {}).get("format") == "composite pdf"]
    assert [lg.get("radii") for lg in logs][-2:] == [True, False]
