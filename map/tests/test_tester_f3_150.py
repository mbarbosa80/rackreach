"""Independent tester F3, RackReach Map 1.5.0-rc2 (tag v150-rc2), 21 Nov 2026.

The Map edition shares the marked-up PDF, the level rule and the merge undo
with RackReach 5.9.0 (team); it has no TEAP, so T4-5 / T4-6 / T4-8 of the
team edition do not apply. Synthetic data only.

* SECTION A - every test FAILS on Map 1.5.0-rc2 (finding IDs as in
  /home/claude/tester590/FINDINGS.md).
* SECTION B - checks that PASS on rc2 (documented, not findings).
"""
from __future__ import annotations

import os
import time

import pymupdf
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.domain.consolidation import Candidate, checked_level, compare, level_key, normalise_level
from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceRules
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.reports import MARKER_LABEL_SIZE, MARKER_MIN_RADIUS, MARKER_RADIUS, \
    export_marked_pdf, marker_factor
from app.domain.units import POINTS_PER_METRE, Scale
from app.models import Drawing, Page
from tests.test_multi_book_410 import TMP, _ner as _orm_ner, _two_books
from tests.test_ner_register import client  # noqa: F401
from tests.test_tester_140 import _merge_case, _patch, _row

RULES = DistanceRules()


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


def at(denominator: float, confirmed: bool = True) -> Scale:
    return Scale(metres_per_unit=denominator / POINTS_PER_METRE, source="ratio",
                 label=f"1:{denominator:g}", confirmed=confirmed)


def _export(scale, *, uu=None, rot=0, label="NER-CRIT-01", kind=ElementKind.NER_CRITICAL,
            radii=False, legend=False, pt=(1000, 700)):
    tag = os.urandom(4).hex()
    src = os.path.join(TMP, f"f3m-src-{tag}.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=2000, height=1400)
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
    out = os.path.join(TMP, f"f3m-out-{tag}.pdf")
    export_marked_pdf(src, out, coverage=res, ners=rooms, scale_for_page=sfp, rules=RULES,
                      draw_radii=radii, draw_links=False, draw_legend=legend)
    return pymupdf.open(out)[0]


def _marker_dot(page):
    dots = [d["rect"] for d in page.get_drawings()
            if d.get("fill") is not None and d.get("fill") != (1.0, 1.0, 1.0)
            and abs(d["rect"].width - d["rect"].height) < 0.05
            and d.get("color") == (0.0, 0.0, 0.0)]
    return max(dots, key=lambda r: r.width) if dots else None


def _span(page, text):
    hits = [s for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
            for s in ln.get("spans", []) if s["text"] == text]
    return hits[0] if hits else None


def _wait(client, job_id, timeout=120.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if not last["running"]:
            return last
        time.sleep(0.15)
    return last


def _one_sheet_project(mpu, confirmed):
    """One book, one 2000 x 1400 sheet with the given scale, one NER."""
    from app import migrate
    from app.models import Element, Project
    path = os.path.join(TMP, f"f3m-orm-{os.urandom(4).hex()}.pdf")
    doc = pymupdf.open()
    doc.new_page(width=2000, height=1400)
    doc.save(path)
    doc.close()
    with SessionLocal() as db:
        p = Project(name=_uid("F3 map orm"), venue_code="VEN")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="VEN_900_X.pdf", stored_path=path,
                    sha256=os.urandom(32).hex(), page_count=1)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=2000, height=1400,
                    metres_per_unit=mpu, scale_label="1:500", scale_source="detected",
                    scale_confirmed=confirmed, sheet_number="VEN-901-X"))
        db.add(Element(project_id=p.id, drawing_id=d.id, page=1, kind="NER_CRITICAL",
                       label="NER 90", x=1000, y=700, origin="manual", confidence=1.0,
                       confirmed=True))
        db.commit()
        migrate.backfill_assets(db, db.get(Project, p.id))
        return p.id


# ===========================================================================
# SECTION A - FAIL on Map 1.5.0-rc2
# ===========================================================================

# T4-1  An unconfirmed (detected) scale shrinks the marker; the manual and the
#       CHANGELOG say "No confirmed scale ...: the full size".

def test_t4_1_an_unconfirmed_scale_keeps_the_full_marker():
    page = _export(at(500, confirmed=False))
    assert round(_marker_dot(page).width, 1) == 2 * MARKER_RADIUS
    assert _span(page, "NER-CRIT-01")["size"] == pytest.approx(MARKER_LABEL_SIZE)


def test_t4_1b_through_the_report_route(client):
    pid = _one_sheet_project(500 / POINTS_PER_METRE, confirmed=False)
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false")
    assert r.status_code == 200, r.text
    doc = pymupdf.open(stream=client.get(r.json()["download"]).content, filetype="pdf")
    assert round(_marker_dot(doc[0]).width, 1) == 2 * MARKER_RADIUS


# T4-2  /UserUnit sheet: the name is UserUnit times larger than designed and
#       no longer centred under the dot (the dot itself is right).

@pytest.mark.parametrize("uu", [1.19, 1.7])
def test_t4_2_on_a_user_unit_sheet_the_name_shrinks_with_the_dot(uu):
    plain, big = _export(at(500)), _export(at(500), uu=uu)
    s0, s1 = _span(plain, "NER-CRIT-01"), _span(big, "NER-CRIT-01")
    d1 = _marker_dot(big)
    assert d1.width == pytest.approx(_marker_dot(plain).width, abs=0.05)
    assert s1["size"] == pytest.approx(s0["size"], abs=0.05), (s0["size"], s1["size"])
    cx = (s1["bbox"][0] + s1["bbox"][2]) / 2
    assert cx == pytest.approx((d1.x0 + d1.x1) / 2, abs=1.0)


# T4-3  level_key vs normalise_level on a lettered level touching the word.

@pytest.mark.parametrize("raw", ["LEVELB1", "LEVELP1", "FLOORB2", "LEVEL-B1"])
def test_t4_3_level_key_agrees_with_normalise_level_on_lettered_levels(raw):
    assert level_key(raw) == level_key(normalise_level(raw)), (raw, normalise_level(raw))


def test_t4_3b_levelb1_and_level_b1_are_one_floor():
    a = Candidate(ref="a", label="NER 4", page=1, level="LEVELB1", building=None)
    b = Candidate(ref="b", label="NER 4", page=2, level="LEVEL B1", building=None)
    assert compare(a, b).blocking != "different_level"


# T4-4  Invisible characters that are not Cc/Cf pass checked_level.

@pytest.mark.parametrize("bad", ["ㅤ", "02⠀", "02͏", "02️", "ᅟ"])
def test_t4_4_a_level_with_an_invisible_character_is_refused(bad):
    with pytest.raises(ValueError):
        checked_level(bad)


# T4-7  A refused page level is answered 404 Not Found.

@pytest.mark.parametrize("bad", ["​", "x" * 41])
def test_t4_7_a_refused_page_level_is_not_404(client, bad):
    pid = _one_sheet_project(100 / POINTS_PER_METRE, confirmed=True)
    r = client.post(f"/api/projects/{pid}/pages/1/level", json={"level": bad})
    assert r.status_code in (400, 422), (r.status_code, r.text)


# ---------------------------------------------------------------------------
# T4-9  (Map only) With "with reach circles" off the marked-up PDF's legend
#       still lists "Reach circle - general limit" and "Reach circle -
#       photographer limit", each with its dashed-circle swatch, although no
#       such circle is on any sheet. The composite map PDF drops its two
#       reach rows in the same case, and reports.py's own rule is "a key that
#       names a shape the reader cannot find is noise". Expected: the two
#       rows left out when the circles are (the limits can stay written).
# ---------------------------------------------------------------------------

def test_t4_9_the_marked_pdf_legend_drops_the_reach_rows_when_no_circle_is_drawn():
    on = _export(at(200), radii=True, legend=True).get_text()
    off = _export(at(200), radii=False, legend=True).get_text()
    assert "Reach circle - general limit" in on
    assert "Reach circle - general limit" not in off
    assert "Reach circle - photographer limit" not in off


# ===========================================================================
# SECTION B - PASS on rc2
# ===========================================================================

def test_b_marker_factor_cap_and_floor():
    d_cap = POINTS_PER_METRE / MARKER_RADIUS
    d_floor = POINTS_PER_METRE / MARKER_MIN_RADIUS
    assert marker_factor(at(d_cap)) == pytest.approx(1.0)
    assert marker_factor(at(d_cap * 1.001)) < 1.0
    assert marker_factor(at(d_floor * 10)) * MARKER_RADIUS == pytest.approx(MARKER_MIN_RADIUS)


@pytest.mark.parametrize("r", [float("nan"), float("inf"), -1.0, 0.0])
def test_b_an_absurd_scale_object_draws_full_size(r):
    class Odd:
        def to_pdf_units(self, m):
            return r
    assert marker_factor(Odd()) == 1.0


@pytest.mark.parametrize("rot", [90, 270])
def test_b_rotated_sheet_same_marker(rot):
    flat, turned = _export(at(500)), _export(at(500), rot=rot)
    assert _marker_dot(turned).width == pytest.approx(_marker_dot(flat).width, abs=0.05)


def _reach_circles(page, radii_units):
    out = []
    for d in page.get_drawings():
        r = d["rect"]
        if d.get("fill") is None and abs(r.width - r.height) < 0.5 \
                and any(abs(r.width / 2 - w) < 0.5 for w in radii_units):
            out.append(round(r.width / 2, 1))
    return out


def test_b_multi_book_background_export_honours_radii_false(client):
    from app.models import AuditLog
    pid, a, b = _two_books(_uid("F3 map two books radii"))
    _orm_ner(pid, a, 1, "NER 31", 300, 300)
    _orm_ner(pid, b, 2, "NER 32", 400, 300)
    lim = [RULES.general_limit_m / RULES.routing_slack_factor,
           RULES.photographer_limit_m / RULES.routing_slack_factor]
    want = lim + [x / 0.5 for x in lim]
    found = {}
    for radii in (True, False):
        r = client.post(f"/api/projects/{pid}/reports/pdf?background=true&radii={str(radii).lower()}")
        assert r.status_code == 200 and r.json().get("background"), r.text
        j = _wait(client, r.json()["job_id"])
        assert j["state"] == "done", j
        doc = pymupdf.open(stream=client.get(j["result"]["download"]).content, filetype="pdf")
        found[radii] = [c for pg in doc for c in _reach_circles(pg, want)]
        doc.close()
    assert found[True] and found[False] == [], found
    with SessionLocal() as db:
        logs = [x.payload for x in db.execute(select(AuditLog).where(
            AuditLog.project_id == pid, AuditLog.action == "report.generated")).scalars()]
    assert [lg.get("radii") for lg in logs][-2:] == [True, False], logs


@pytest.fixture(scope="module")
def composite_pid(client, tmp_path_factory):
    from tests.test_composite_map_500 import _place, _project_with_book
    tmp = tmp_path_factory.mktemp("f3m")
    pid, facts = _project_with_book(client, tmp, _uid("F3 map composite"),
                                    [("TST_400_TST_O.pdf", {})])
    did, f = next(iter(facts.items()))
    _place(client, pid, did, f, (2, 1), (250, 100), "NER_21.1")
    r = client.post(f"/api/projects/{pid}/composite/build", json={"drawing_id": did})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["state"] == "done"
    return pid


def _reach_strokes(page):
    return [d for d in page.get_drawings()
            if str(d.get("dashes") or "").replace(" ", "").startswith(("[128]", "[35]"))]


@pytest.mark.parametrize("legend", [True, False])
def test_b_composite_radii_false(client, composite_pid, legend):
    pages = {}
    for radii in (True, False):
        r = client.post(f"/api/projects/{composite_pid}/composite-report?"
                        f"legend={str(legend).lower()}&radii={str(radii).lower()}")
        assert r.status_code == 200, r.text
        j = _wait(client, r.json()["job_id"])
        assert j["state"] == "done", j
        doc = pymupdf.open(stream=client.get(j["result"]["download"]).content, filetype="pdf")
        pages[radii] = (len(_reach_strokes(doc[0])), doc[0].get_text())
        doc.close()
    assert pages[True][0] >= 2 and pages[False][0] == 0
    if legend:
        assert "90 m reach" in pages[True][1] and "90 m reach" not in pages[False][1]
    assert "NER_21.1" in pages[False][1]


def test_b_undo_after_retyping_keeps_the_typed_level(client):
    pid, primary, cid = _merge_case(client, "f3 retype")
    assert _patch(client, pid, primary, "4").status_code == 200
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text
    assert _row(client, pid, "NER 24")["level"] == "LEVEL 04"


def test_b_undo_after_clearing_gives_back_none(client):
    pid, primary, cid = _merge_case(client, "f3 clear")
    assert _patch(client, pid, primary, "").status_code == 200
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text
    assert _row(client, pid, "NER 24")["level"] is None


def test_b_undo_of_an_old_record_without_primary_after(client):
    from app.models import NerConsolidation
    pid, primary, cid = _merge_case(client, "f3 old")
    with SessionLocal() as db:
        rec = db.get(NerConsolidation, cid)
        snap = dict(rec.undo_snapshot)
        snap.pop("primary_after", None)
        rec.undo_snapshot = snap
        db.commit()
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text
    assert _row(client, pid, "NER 24")["level"] is None


def test_b_checked_level_edges():
    assert checked_level(None) is None and checked_level("") is None
    assert checked_level("    ") is None
    assert checked_level("ß" * 20) == "SS" * 20
    with pytest.raises(ValueError):
        checked_level("ß" * 20 + "a")
    for bad in (2, True, ["x"]):
        with pytest.raises(ValueError):
            checked_level(bad)
