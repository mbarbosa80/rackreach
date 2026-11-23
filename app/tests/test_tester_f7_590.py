"""Independent tester F7, RackReach 5.9.0-rc7 (tag v590-rc7), 22 Nov 2026.

Adversarial pass on the new match lines (page joints, rc7) and a regression
pass on what 5.9.0 changes against 5.8.0. Synthetic data only.

* SECTION A - every test FAILS on rc7: one finding each (T7-n in the name).
* SECTION B - checks that PASS on rc7 (what was found correct).
* SECTION C - browser checks of the viewer's match-line tool (Playwright on a
  private server with a data folder of its own); skipped when Playwright or
  Chromium is missing. T7-5 and T7-7 there FAIL on rc7.
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
from pathlib import Path

import pymupdf
import pytest

from tests.test_ner_register import TMP, client, samples  # noqa: F401  (sets the env)

from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.joints import PageJoint, shortest_across, stand_in
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteNetwork, RouteSegment, VerticalLink
from app.domain.units import POINTS_PER_METRE, Scale

RULES = DistanceRules()
M = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
#: sheet 1's right edge (x = 1000) is sheet 2's left edge (x = 0)
SEAM = PageJoint(key="J1", a_page=1, a1=(1000, 0), a2=(1000, 1000),
                 b_page=2, b1=(0, 0), b2=(0, 1000), gap_m=0.0, label="J1")
REPO = Path(__file__).resolve().parent.parent


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


def _seam(**kw):
    return PageJoint(**{**SEAM.__dict__, **kw})


def _ner(key, x, y, page, critical=True, ports=48, used=0):
    return NER(key=key, label=key, kind=ElementKind.NER_CRITICAL if critical
               else ElementKind.NER_NONCRITICAL, point=Point(x, y, page),
               critical=critical, total_ports=ports, used_ports=used)


def _one(area, ners, joints, scales=None, **kw):
    res = compute_coverage(areas=[(area, 1.0, kw.pop("demand", DemandType.GENERAL),
                                   kw.pop("crit", None), True)],
                           ners=ners, scale_for_page=scales or {1: M, 2: M, 3: M, 4: M},
                           rules=RULES, joints=joints, **kw)
    return res.assignments[0]


def _brute(p, hm, q, tm, j, n=4001):
    best = math.inf
    for i in range(n):
        t = i / (n - 1)
        a = (j.a1[0] + (j.a2[0] - j.a1[0]) * t, j.a1[1] + (j.a2[1] - j.a1[1]) * t)
        b = (j.b1[0] + (j.b2[0] - j.b1[0]) * t, j.b1[1] + (j.b2[1] - j.b1[1]) * t)
        best = min(best, math.hypot(p[0] - a[0], p[1] - a[1]) * hm + j.gap_m
                   + math.hypot(b[0] - q[0], b[1] - q[1]) * tm)
    return best


# --- API helpers -------------------------------------------------------------

def _pages_project(levels=("LEVEL 01", "LEVEL 01", "LEVEL 02"), owner=None):
    from app.db import SessionLocal, init_db
    from app.models import Drawing, Page, Project
    init_db()
    with SessionLocal() as db:
        p = Project(name=_uid("f7 joints"), venue_code="TST", owner_id=owner)
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="b.pdf", stored_path="b.pdf",
                    sha256=os.urandom(32).hex(), page_count=len(levels))
        db.add(d)
        db.flush()
        for i, lv in enumerate(levels, 1):
            db.add(Page(drawing_id=d.id, number=i, width=1000, height=1000, level=lv,
                        sheet_number=f"TST-40{i}-X"))
        db.commit()
        return p.id


def _body(a=1, b=2, **kw):
    return {"a_page": a, "a_x1": 1000, "a_y1": 0, "a_x2": 1000, "a_y2": 1000,
            "b_page": b, "b_x1": 0, "b_y1": 0, "b_x2": 0, "b_y2": 1000, **kw}


def _sample_project(client, samples, levels=("LEVEL 01", "LEVEL 01", "LEVEL 01")):
    """The synthetic book and TEAP, the three pages on the given levels, the
    NERs of page 2 removed: page 2's spaces can only be served across a
    match line from page 1."""
    r = client.post("/api/projects", json={"name": _uid("f7 e2e"), "venue_code": "SYN"})
    pid = r.json()["id"]
    with open(samples["pdf"], "rb") as fh:
        r = client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                        files={"file": ("plan.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    with open(samples["teap"], "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap.xlsx", fh, "application/octet-stream")})
    tid, pr = r.json()["id"], r.json()["proposed"]
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={"sheet": pr["sheet"], "header_row": pr["header_row"],
                          "mapping": pr["mapping"]})
    assert r.status_code == 200, r.text
    for page, lv in zip((1, 2, 3), levels):
        if lv:
            assert client.post(f"/api/projects/{pid}/pages/{page}/level",
                               json={"level": lv}).status_code == 200
        client.post(f"/api/projects/{pid}/pages/{page}/scale",
                    json={"mode": "ratio", "denominator": 200.0, "confirm": True})
    assert client.post(f"/api/projects/{pid}/matching/run").status_code == 200
    for e in client.get(f"/api/projects/{pid}/elements").json():
        if e["page"] == 2 and e["kind"].startswith("NER"):
            assert client.delete(f"/api/projects/{pid}/elements/{e['id']}").status_code == 204
    return pid


#: page 1's right border (x = 1640) continues on page 2's left border (x = 40)
SAMPLE_CUT = {"a_page": 1, "a_x1": 1640, "a_y1": 40, "a_x2": 1640, "a_y2": 1150,
              "b_page": 2, "b_x1": 40, "b_y1": 40, "b_x2": 40, "b_y2": 1150}


def _served_on_p2(res):
    return {a["space_key"]: a for a in res["assignments"]
            if a["page"] == 2 and a.get("kind") != "wireless_point" and a.get("ner_label")}


# ===========================================================================
# SECTION A - defects (FAIL on rc7)
# ===========================================================================

# ---------------------------------------------------------------------------
# T7-1  A room with a polygon, served across a match line with a gap: the
#       farthest vertex is measured to the "stand-in" point (joints.stand_in),
#       which sits on the ray centre -> crossing. With gap > 0 that point is
#       nearer to the room's side corners than the real path (corner -> line
#       -> gap -> NER), so the verdict uses a SHORTER distance than the one
#       the manual describes. Here: real farthest corner 90.71 m (out of the
#       90 m limit), reported 86.02 m "near limit" - served.
# ---------------------------------------------------------------------------

def test_t7_1_polygon_far_corner_across_a_gap_is_not_underestimated():
    poly = [(0, 0), (400, 0), (400, 1000), (0, 1000)]       # 40 m x 100 m on sheet 2
    area = Area(key="S", name="Room", page=2, label_point=Point(200, 500, 2), polygon=poly)
    j = _seam(gap_m=20.0)
    a = _one(area, [_ner("ACROSS", 900, 500, 1)], [j])
    true_far = max(shortest_across(Point(x, y, 2), M, Point(900, 500, 1), M, [j]).length_m
                   for x, y in poly)
    assert true_far == pytest.approx(90.71, abs=0.01)
    got = a.evaluation.effective_distance_m
    assert got >= true_far - 0.05, (got, true_far, a.status)
    assert a.status is Status.OUT_OF_LIMIT


# ---------------------------------------------------------------------------
# T7-2  When the project has shafts, a counting route "through another level"
#       to the NER on the other sheet of the joint (down a shaft on sheet A,
#       along the lower level, up a shaft on sheet B) pre-empts the match
#       line: the joint is only tried when route_len is None
#       (coverage.compute_coverage). The NER, on the SAME level 20 m away
#       across the line, is then treated as "another level" (58 m) and set
#       aside by "same floor first" for a 30 m NER on the space's sheet; its
#       notes even say "a NER on another level".
# ---------------------------------------------------------------------------

def _two_shafts():
    net = RouteNetwork(page_scales={1: M, 2: M, 3: M})
    net.add_segment(RouteSegment(Point(500, 500, 3), Point(1000, 500, 3), 3), M)
    assert net.add_vertical(VerticalLink("S1", Point(500, 500, 3), Point(900, 500, 1), 4.0))
    assert net.add_vertical(VerticalLink("S2", Point(1000, 500, 3), Point(100, 500, 2), 4.0))
    return net


def test_t7_2_a_shaft_route_does_not_hide_the_match_line():
    area = Area(key="S", name="Space", page=2, label_point=Point(100, 500, 2))
    a = _one(area, [_ner("ACROSS", 900, 500, 1), _ner("HERE", 400, 500, 2)], [SEAM],
             network=_two_shafts())
    assert a.ner_key == "ACROSS", (a.ner_key, a.reasons)
    assert a.evaluation.effective_distance_m == pytest.approx(20.0, abs=0.05)


def test_t7_2b_alone_the_joint_ner_is_measured_across_not_round_another_level():
    area = Area(key="S", name="Space", page=2, label_point=Point(100, 500, 2))
    a = _one(area, [_ner("ACROSS", 900, 500, 1)], [SEAM], network=_two_shafts())
    assert a.ner_key == "ACROSS"
    assert a.evaluation.effective_distance_m == pytest.approx(20.0, abs=0.05), \
        (a.evaluation.effective_distance_m, a.evaluation.method)


# ---------------------------------------------------------------------------
# T7-3  A match line is refused between sheets of two levels at creation, but
#       nothing checks again: change one sheet's level afterwards (Scale
#       screen) and the next analysis still serves that sheet's spaces from
#       the other level across the line, as same-level, with no note. The
#       manual: "Two different levels are never joined by one."
# ---------------------------------------------------------------------------

def test_t7_3_a_joint_between_sheets_that_became_two_levels_is_not_used(client, samples):
    pid = _sample_project(client, samples)
    assert client.post(f"/api/projects/{pid}/page-joints", json=SAMPLE_CUT).status_code == 201
    before = _served_on_p2(client.post(f"/api/projects/{pid}/analyse").json())
    assert before, "the joint serves page 2 from page 1 while both are LEVEL 01"
    r = client.post(f"/api/projects/{pid}/pages/2/level", json={"level": "LEVEL 02"})
    assert r.status_code == 200, r.text
    after = _served_on_p2(client.post(f"/api/projects/{pid}/analyse").json())
    leaked = {k: (a["ner_label"], (a["evaluation"] or {}).get("detail", {}).get("joint"))
              for k, a in after.items() if (a["evaluation"] or {}).get("detail", {}).get("joint")}
    assert not leaked, f"spaces of LEVEL 02 served across the line from LEVEL 01: {leaked}"


# ---------------------------------------------------------------------------
# T7-4  A space served across a match line comes back with cross_level=True
#       (Assignment.cross_level = the NER has no marker on the space's sheet)
#       and a distance; Demand per space renders that pair as "via vertical
#       route · page N" (panels.tsx), though no shaft or riser exists.
# ---------------------------------------------------------------------------

def test_t7_4_demand_row_of_a_joint_served_space_does_not_claim_a_vertical_route(client, samples):
    pid = _sample_project(client, samples)
    assert client.post(f"/api/projects/{pid}/page-joints", json=SAMPLE_CUT).status_code == 201
    client.post(f"/api/projects/{pid}/analyse")
    assert client.get(f"/api/projects/{pid}/verticals").json() == []
    rows = [s for s in client.get(f"/api/projects/{pid}/spaces/assignments").json()["spaces"]
            if s.get("distance_m") is not None and s.get("serving_ner_page") == 1
            and s.get("page") == 2]
    assert rows, "page 2 spaces served across the line"
    # cross_level + distance is exactly what the screen prints as "via vertical route"
    wrong = [s.get("space_name") or s.get("space_key") for s in rows if s.get("cross_level")]
    assert not wrong, f"rows shown as 'via vertical route' with no vertical: {wrong[:3]}"


# ---------------------------------------------------------------------------
# T7-6  The same line posted twice (a double click on the last end, a retry)
#       is stored twice: J1 and J2 on the same ends. Routes refuse exactly
#       that since 4.7.0 ("the same path twice on the same sheet is one
#       route"). Two identical lines with different gaps: the smaller gap
#       silently wins, and the Inspector can only reach the top one.
# ---------------------------------------------------------------------------

def test_t7_6_the_same_match_line_twice_is_one(client):
    pid = _pages_project()
    r1 = client.post(f"/api/projects/{pid}/page-joints", json=_body(gap_m=3))
    r2 = client.post(f"/api/projects/{pid}/page-joints", json=_body(gap_m=3))
    assert r1.status_code == 201
    assert len(client.get(f"/api/projects/{pid}/page-joints").json()) == 1, r2.json()


# ---------------------------------------------------------------------------
# T7-8  Two equally short joints: the tie-break compares the keys as text,
#       so J10 wins over J2 (joints.shortest_across). The manual's own rule
#       for routes reads names with numbers as numbers (R2 before R10). The
#       label printed in the assumptions depends on it.
# ---------------------------------------------------------------------------

def test_t7_8_equal_joints_tie_by_number_not_text():
    j2, j10 = _seam(key="J2", label="J2"), _seam(key="J10", label="J10")
    p = shortest_across(Point(100, 500, 2), M, Point(900, 500, 1), M, [j10, j2])
    assert p.joint.key == "J2", p.joint.key


# ---------------------------------------------------------------------------
# T7-9  Joints are not chained. A level cut into four sheets (2 x 2) with the
#       four lines the manual asks for ("a level cut into four sheets takes
#       up to four lines"): a NER at the inner corner of sheet 1 and a space
#       at the inner corner of sheet 4, 3 m apart in reality, diagonal - the
#       space gets no NER, and the row says "no NER on this level", which is
#       false (and the manual promises the four lines are enough).
# ---------------------------------------------------------------------------

def test_t7_9_a_level_in_four_sheets_reaches_the_diagonal_sheet():
    # 1 | 2      each sheet 0..1000 x 0..1000, 0.1 m per unit
    # --+--
    # 3 | 4
    j12 = PageJoint(key="J1", a_page=1, a1=(1000, 0), a2=(1000, 1000), b_page=2, b1=(0, 0), b2=(0, 1000))
    j34 = PageJoint(key="J2", a_page=3, a1=(1000, 0), a2=(1000, 1000), b_page=4, b1=(0, 0), b2=(0, 1000))
    j13 = PageJoint(key="J3", a_page=1, a1=(0, 1000), a2=(1000, 1000), b_page=3, b1=(0, 0), b2=(1000, 0))
    j24 = PageJoint(key="J4", a_page=2, a1=(0, 1000), a2=(1000, 1000), b_page=4, b1=(0, 0), b2=(1000, 0))
    area = Area(key="S", name="Space", page=4, label_point=Point(10, 10, 4))
    a = _one(area, [_ner("CORNER", 990, 990, 1)], [j12, j34, j13, j24])
    assert a.ner_key == "CORNER", a.reasons
    assert not any("no NER on this level" in r for r in a.reasons), a.reasons


# ---------------------------------------------------------------------------
# T7-10 The assumption sentence does not read as the manual and the QA notes
#       say: the manual quotes "12.40 m on this sheet + 0.00 m between the
#       sheets + 8.10 m on page 15"; the code writes one decimal and the gap
#       with :g ("12.4 m ... + 0 m ..."). Cosmetic.
# ---------------------------------------------------------------------------

def test_t7_10_the_assumption_sentence_reads_as_documented():
    area = Area(key="S", name="Space", page=2, label_point=Point(124, 500, 2))
    a = _one(area, [_ner("ACROSS", 919, 500, 1)], [SEAM])
    line = next(s for s in a.evaluation.assumptions if "match line" in s)
    assert "12.40 m on this sheet + 0.00 m between the sheets + 8.10 m on page 1" in line, line


# ---------------------------------------------------------------------------
# T7-11 A label of blanks is stored as typed: the line is listed and drawn
#       with no name ("" in the list, "   ↔ p.2" on the sheet). PATCH strips
#       and ignores a blank label; POST does not.
# ---------------------------------------------------------------------------

def test_t7_11_a_blank_label_gets_the_default_name(client):
    pid = _pages_project()
    r = client.post(f"/api/projects/{pid}/page-joints", json=_body(label="   "))
    assert r.status_code == 201
    assert r.json()["label"] == f"J{r.json()['id']}", repr(r.json()["label"])


# ===========================================================================
# SECTION B - checks that PASS on rc7
# ===========================================================================

def test_b_shortest_across_matches_dense_sampling():
    """Random segments, scales, gaps (reversed and crossing segments
    included): the golden-section answer is never worse than a 4001-point
    sampling (the sum is convex in t - two norms of affine maps)."""
    rnd = random.Random(7)
    worst = 0.0
    for _ in range(300):
        r = lambda: rnd.uniform(-3000, 3000)  # noqa: E731
        j = PageJoint(key="J1", a_page=1, a1=(r(), r()), a2=(r(), r()), b_page=2,
                      b1=(r(), r()), b2=(r(), r()), gap_m=rnd.choice([0, 3.3, 50]))
        hm, tm = rnd.choice([0.001, 0.0254, 0.1, 1.0]), rnd.choice([0.001, 0.0254, 0.1, 1.0])
        p, q = (r(), r()), (r(), r())
        got = shortest_across(Point(*p, 1), Scale(hm, "m", "x", True), Point(*q, 2),
                              Scale(tm, "m", "x", True), [j]).length_m
        worst = max(worst, got - _brute(p, hm, q, tm, j))
    assert worst < 1e-6


def test_b_gap_zero_same_scale_polygon_is_the_one_sheet_answer():
    """Gap 0, one scale: the far corner across the line is what one stitched
    sheet gives (the stand-in is the NER's unfolded position)."""
    poly = [(0, 0), (400, 0), (400, 1000), (0, 1000)]
    area = Area(key="S", name="Room", page=2, label_point=Point(200, 500, 2), polygon=poly)
    a = _one(area, [_ner("ACROSS", 900, 500, 1)], [SEAM])
    one = _one(area, [_ner("X", -100, 500, 2)], None)
    assert a.evaluation.effective_distance_m == pytest.approx(
        one.evaluation.effective_distance_m, abs=1e-3)


def test_b_reversed_ends_are_measured_as_marked():
    """End order is the person's statement: swapped ends on one sheet give a
    different (wrong) distance and nothing can tell - the viewer numbers the
    ends; documented ("the same two ends in the same order")."""
    rev = _seam(b1=(0, 1000), b2=(0, 0))
    p = shortest_across(Point(100, 0, 2), M, Point(900, 1000, 1), M, [rev])
    assert p.length_m == pytest.approx(20.0, abs=1e-6)        # really ~102 m


def test_b_pin_photographer_capacity_and_criticality_across():
    area = Area(key="S", name="Space", page=2, label_point=Point(100, 500, 2))
    # a pin on the other sheet is measured, not "other level - not measured"
    a = _one(area, [_ner("HERE", 300, 500, 2), _ner("ACROSS", 900, 500, 1)], [SEAM],
             pinned={"S": "ACROSS"})
    assert a.ner_key == "ACROSS" and a.status is Status.SERVED
    assert a.evaluation.effective_distance_m == pytest.approx(20.0, abs=0.05)
    # photographers: 70 m
    a = _one(area, [_ner("ACROSS", 350, 500, 1)], [SEAM], demand=DemandType.PHOTOGRAPHER)
    assert a.status is Status.OUT_OF_LIMIT and a.nearest_distance_m == pytest.approx(75.0, abs=0.05)
    # a full NER on the sheet gives way to the free one across
    a = _one(area, [_ner("HERE", 150, 500, 2, ports=2, used=2), _ner("ACROSS", 900, 500, 1)], [SEAM])
    assert a.ner_key == "ACROSS"
    # criticality enforced: the critical NER across wins over a non-critical one here
    a = _one(area, [_ner("HERE", 300, 500, 2, critical=False), _ner("ACROSS", 900, 500, 1)],
             [SEAM], crit=True, enforce_criticality=True)
    assert a.ner_key == "ACROSS"


def test_b_scales_across():
    area = Area(key="S", name="Space", page=2, label_point=Point(100, 500, 2))
    loose = Scale(0.1, "detected", "x", False)
    a = _one(area, [_ner("ACROSS", 900, 500, 1)], [SEAM], scales={1: M, 2: loose})
    assert a.status is Status.SCALE_NOT_CONFIRMED
    a = _one(area, [_ner("ACROSS", 900, 500, 1)], [SEAM], scales={1: M})
    assert a.ner_key is None


def test_b_a_route_on_the_space_sheet_sets_the_joint_ner_aside_as_documented():
    net = RouteNetwork(page_scales={1: M, 2: M})
    net.add_segment(RouteSegment(Point(100, 500, 2), Point(100, 900, 2), 2), M)
    net.add_segment(RouteSegment(Point(100, 900, 2), Point(400, 900, 2), 2), M)
    area = Area(key="S", name="Space", page=2, label_point=Point(100, 500, 2))
    a = _one(area, [_ner("ROUTED", 400, 900, 2), _ner("ACROSS", 900, 500, 1)], [SEAM],
             network=net)
    assert a.ner_key == "ROUTED" and a.evaluation.method.value == "route"


@pytest.mark.parametrize("bad,code", [
    ({"a_x1": float("nan")}, 422),
    ({"b_y2": float("inf")}, 422),
    ({"gap_m": float("nan")}, 422),
    ({"b_page": 9}, 404),
    ({"label": "x" * 201}, 422),
    ({"a_page": 0}, 422),
])
def test_b_api_refusals(client, bad, code):
    import json as _json
    pid = _pages_project()
    raw = _json.dumps(_body(**bad), allow_nan=True)
    r = client.post(f"/api/projects/{pid}/page-joints", content=raw,
                    headers={"content-type": "application/json"})
    assert r.status_code == code, r.text


def test_b_level_spellings_and_no_level(client):
    pid = _pages_project(levels=("Level 1", "LEVEL 01", None))
    assert client.post(f"/api/projects/{pid}/page-joints", json=_body(1, 2)).status_code == 201
    assert client.post(f"/api/projects/{pid}/page-joints", json=_body(1, 3)).status_code == 201


def test_b_patch_refusals_and_other_project(client):
    pid, other = _pages_project(), _pages_project()
    j = client.post(f"/api/projects/{pid}/page-joints", json=_body()).json()
    assert client.patch(f"/api/projects/{pid}/page-joints/{j['id']}",
                        json={"gap_m": 201}).status_code == 422
    assert client.patch(f"/api/projects/{other}/page-joints/{j['id']}",
                        json={"gap_m": 1}).status_code == 404
    assert client.delete(f"/api/projects/{other}/page-joints/{j['id']}").status_code == 404
    r = client.patch(f"/api/projects/{pid}/page-joints/{j['id']}", json={"label": "  "})
    assert r.status_code == 200 and r.json()["label"] == j["label"]


def test_b_read_only_member_cannot_write(client):
    from fastapi.testclient import TestClient
    from app import auth as A
    from app.db import SessionLocal
    from app.main import app
    from app.models import Project
    pid = _pages_project()
    j = client.post(f"/api/projects/{pid}/page-joints", json=_body()).json()
    assert client.post(f"/api/projects/{pid}/visibility", json={"visibility": "shared"}).status_code == 200
    with SessionLocal() as db:
        owner = db.get(Project, pid).owner_id
        try:
            A.create_user(db, username="f7reader", password="another-long-phrase",
                          display_name="Reader", is_admin=False, actor="tests")
        except A.AuthError:
            pass
        if owner is None:      # a project with no owner is everyone's
            from app.models import User
            from sqlalchemy import select
            db.get(Project, pid).owner_id = db.execute(
                select(User).where(User.username == "tester")).scalar_one().id
            db.commit()
    reader = TestClient(app)
    assert reader.post("/api/auth/login", json={"username": "f7reader",
                                                "password": "another-long-phrase"}).status_code == 200
    assert reader.get(f"/api/projects/{pid}/page-joints").status_code == 200
    assert reader.post(f"/api/projects/{pid}/page-joints", json=_body()).status_code == 403
    assert reader.patch(f"/api/projects/{pid}/page-joints/{j['id']}", json={"gap_m": 9}).status_code == 403
    assert reader.delete(f"/api/projects/{pid}/page-joints/{j['id']}").status_code == 403


def test_b_stale_flag_and_audit(client, samples):
    pid = _sample_project(client, samples)
    client.post(f"/api/projects/{pid}/analyse")
    assert client.get(f"/api/projects/{pid}/analysis").json()["stale"] is None
    j = client.post(f"/api/projects/{pid}/page-joints", json=SAMPLE_CUT).json()
    st = client.get(f"/api/projects/{pid}/analysis").json()["stale"]
    assert st and "page_joint.created" in st["kinds"]
    res = client.post(f"/api/projects/{pid}/analyse").json()
    served = _served_on_p2(res)
    assert served and all(a["evaluation"]["detail"].get("joint") == j["label"]
                          for a in served.values())
    client.patch(f"/api/projects/{pid}/page-joints/{j['id']}", json={"gap_m": 2})
    assert "page_joint.updated" in client.get(f"/api/projects/{pid}/analysis").json()["stale"]["kinds"]
    res2 = client.post(f"/api/projects/{pid}/analyse").json()
    for k, a in _served_on_p2(res2).items():
        assert a["evaluation"]["effective_distance_m"] == pytest.approx(
            served[k]["evaluation"]["effective_distance_m"] + 2.0, abs=0.05)
    assert client.delete(f"/api/projects/{pid}/page-joints/{j['id']}").status_code == 204
    assert "page_joint.deleted" in client.get(f"/api/projects/{pid}/analysis").json()["stale"]["kinds"]
    assert not _served_on_p2(client.post(f"/api/projects/{pid}/analyse").json())


def _small_book(tag: str) -> bytes:
    d = pymupdf.open()
    for n in (1, 2):
        p = d.new_page(width=1000, height=800)
        p.insert_text((50, 50), f"F7 BOOK {tag} SHEET {n}", fontsize=20)
    return d.tobytes()


def test_b_two_books_keys_delete_and_project_delete(client):
    r = client.post("/api/projects", json={"name": _uid("f7 books"), "venue_code": "TST"})
    pid = r.json()["id"]
    for tag in ("A", "B"):
        r = client.post(f"/api/projects/{pid}/drawings",
                        files={"file": (f"{tag}.pdf", _small_book(tag + os.urandom(3).hex()),
                                        "application/pdf")})
        assert r.status_code == 201, r.text
    proj = client.get(f"/api/projects/{pid}").json()
    keys = [pg["number"] for pg in proj["pages"]]
    assert len(keys) == 4 and keys[0] != 1, keys            # sheet keys, not bare pages
    for k in keys:
        assert client.post(f"/api/projects/{pid}/pages/{k}/level",
                           json={"level": "LEVEL 01"}).status_code == 200
    body = _body(keys[1], keys[2])
    r = client.post(f"/api/projects/{pid}/page-joints", json=body)
    assert r.status_code == 201, r.text
    assert (r.json()["a_page"], r.json()["b_page"]) == (keys[1], keys[2])
    assert client.post(f"/api/projects/{pid}/page-joints", json=_body(2, keys[2])).status_code == 404
    # the other level across books is refused too
    assert client.post(f"/api/projects/{pid}/pages/{keys[3]}/level",
                       json={"level": "LEVEL 02"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/page-joints",
                       json=_body(keys[0], keys[3])).status_code == 422
    did_b = proj["drawings"][1]["id"]
    assert client.delete(f"/api/projects/{pid}/drawings/{did_b}").status_code in (200, 204)
    assert client.get(f"/api/projects/{pid}/page-joints").json() == []
    # a project with a joint is deleted whole
    pid2 = _pages_project()
    client.post(f"/api/projects/{pid2}/page-joints", json=_body())
    assert client.delete(f"/api/projects/{pid2}").status_code in (200, 204)
    from app.db import SessionLocal
    from app.models import PageJointRow
    from sqlalchemy import select
    with SessionLocal() as db:
        assert not db.execute(select(PageJointRow).where(PageJointRow.project_id == pid2)).all()


def test_b_i18n_strings_of_the_tool_are_translated():
    import re
    src = (REPO / "frontend/src/pages/Viewer.tsx").read_text(encoding="utf-8")
    pt = (REPO / "frontend/src/lib/strings.pt.ts").read_text(encoding="utf-8")
    block = src[src.index('if (tool === "match")'):]
    block = block[:block.index('if (tool === "vertical")')]
    keys = set(re.findall(r't\("((?:[^"\\]|\\.)*)"\)', block))
    keys |= {"Match line", "Gap between the sheets (m)", "the match line",
             "Same level, next sheet? Mark a match line between the two sheets (match line): "
             "the distance is measured across it."}
    missing = [k for k in keys if f'"{k}"' not in pt]
    assert not missing, missing


# --- regression of 5.9.0 vs 5.8.0 -------------------------------------------

def test_b_marker_factor_and_scaled():
    from app.domain import reports as RP
    sc = lambda den, ok=True: Scale(den / POINTS_PER_METRE, "ratio", "x", ok)  # noqa: E731
    assert RP.marker_factor(sc(100)) == 1.0 and RP.marker_factor(sc(200)) == 1.0
    assert RP.marker_factor(sc(500)) == pytest.approx(POINTS_PER_METRE / 500 / 12, rel=1e-6)
    assert RP.marker_factor(sc(5000)) == pytest.approx(3 / 12)
    assert RP.marker_factor(sc(5000, ok=False)) == 1.0
    assert RP.marker_factor(None) == 1.0
    assert RP.scaled(8, 0.25, 4) == 4 and RP.scaled(8, 2.0, 4) == 8


def _marked(den, radii, tmp):
    from app.domain import reports as RP
    src = os.path.join(tmp, f"f7-src-{den}.pdf")
    d = pymupdf.open()
    d.new_page(width=2384, height=1684)
    d.save(src)
    d.close()
    s = Scale(den / POINTS_PER_METRE, "ratio", "x", True)
    ners = [_ner("K", 600, 500, 1), _ner("Y", 1400, 900, 1, critical=False)]
    areas = [(Area(key=f"S{k}", name=f"SP{k}", page=1, label_point=Point(500 + 60 * k, 450, 1)),
              1.0, DemandType.GENERAL, None, True) for k in range(6)]
    res = compute_coverage(areas=areas, ners=ners, scale_for_page={1: s}, rules=RULES)
    out = os.path.join(tmp, f"f7-out-{den}-{radii}.pdf")
    RP.export_marked_pdf(src, out, coverage=res, ners=ners, scale_for_page={1: s},
                         rules=RULES, draw_radii=radii, draw_legend=True)
    return out


def _circles(path, r):
    """Dashed strokes about ``r`` points in radius (the reach circles)."""
    n = 0
    for dr in pymupdf.open(path)[0].get_drawings():
        rect = dr["rect"]
        if dr.get("dashes") and abs(rect.width / 2 - r) < 2 and abs(rect.height / 2 - r) < 2:
            n += 1
    return n


@pytest.mark.parametrize("den", [100, 2000])
def test_b_reach_circles_on_and_off(den):
    tmp = tempfile.mkdtemp(prefix="f7-pdf-")
    r90 = 90.0 * POINTS_PER_METRE / den
    on, off = _marked(den, True, tmp), _marked(den, False, tmp)
    assert _circles(on, r90) == 2 and _circles(off, r90) == 0
    txt = pymupdf.open(off)[0].get_text()
    assert "90" in txt                     # the limit stays written in the legend


# ===========================================================================
# SECTION C - the viewer in a browser
# ===========================================================================

try:                                        # pragma: no cover - environment
    import playwright.sync_api  # noqa: F401
    _HAVE_PW = True
except Exception:                           # noqa: BLE001
    _HAVE_PW = False


@pytest.fixture(scope="module")
def viewer():
    if not _HAVE_PW or os.environ.get("F7_NO_BROWSER"):
        pytest.skip("Playwright not available")
    if not (REPO / "qa" / "viewer_match_rc7.py").exists():
        pytest.skip("the browser helpers of qa/ are not in this copy (an installed package)")
    sys.path.insert(0, str(REPO / "qa"))
    from viewer_match_rc7 import make_book, seed
    from viewer_tiles_rc5 import free_port, start_server
    from playwright.sync_api import sync_playwright
    if not (REPO / "backend/app/static/index.html").exists():
        pytest.skip("interface not built")
    tmp = Path(tempfile.mkdtemp(prefix="f7-ui-"))
    book = tmp / "match.pdf"
    make_book(book)
    port = free_port()
    proc = start_server(REPO, tmp / "data", port, tmp / "server.log")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel=os.environ.get("NER_PW_CHANNEL") or None)
    except Exception as exc:                # noqa: BLE001
        proc.terminate()
        pw.stop()
        pytest.skip(f"Chromium not available: {exc}")
    base = f"http://127.0.0.1:{port}"
    pid, c = seed(base, book)
    yield {"browser": browser, "base": base, "pid": pid, "c": c}
    browser.close()
    pw.stop()
    proc.terminate()


def _open(v):
    from viewer_tiles_rc5 import open_viewer
    page = open_viewer(v["browser"], v["base"], v["pid"], 1400, 900)
    page.wait_for_timeout(2500)
    answers: list = []

    def on_dialog(d):
        if d.type == "prompt":
            a = answers.pop(0) if answers else "0"
            d.dismiss() if a is None else d.accept(a)
        else:
            d.accept()
    page.on("dialog", on_dialog)
    return page, answers


def _draw(page, answers, gap, *more):
    from viewer_match_rc7 import to_screen
    answers.append(gap)
    answers.extend(more)   # rc8: a wrong gap is asked again (T7-7)
    page.get_by_role("button", name="match line", exact=True).click()
    for x, y in ((700, 150), (700, 650)):
        page.mouse.click(*to_screen(page, x, y))
        page.wait_for_timeout(250)
    page.get_by_title("Next page").click()
    page.wait_for_timeout(1500)
    for x, y in ((500, 150), (500, 650)):
        page.mouse.click(*to_screen(page, x, y))
        page.wait_for_timeout(300)
    page.wait_for_timeout(1000)
    text = page.inner_text("body")
    page.get_by_title("Previous page").click()
    page.wait_for_timeout(1500)
    return text


def _select_joint(page):
    from viewer_match_rc7 import to_screen
    page.get_by_role("button", name="select", exact=True).click()
    for _ in range(4):
        page.mouse.click(*to_screen(page, 700, 400))
        page.wait_for_timeout(800)
        if page.locator("h3", has_text="Match line").count():
            return True
    return False


def _clear(v):
    for j in v["c"].get(f"/projects/{v['pid']}/page-joints").json():
        v["c"].delete(f"/projects/{v['pid']}/page-joints/{j['id']}")


def test_c_prompt_answers_backspace_and_delete_key(viewer):
    from viewer_match_rc7 import to_screen
    _clear(viewer)
    c, pid = viewer["c"], viewer["pid"]
    page, answers = _open(viewer)
    got = {}
    for ans in (None, "abc", "-1", "1,5"):
        n0 = len(c.get(f"/projects/{pid}/page-joints").json())
        # rc8 (T7-7): a wrong answer is asked again; Cancel then drops it
        _draw(page, answers, ans, *([None] if ans in ("abc", "-1") else []))
        js = c.get(f"/projects/{pid}/page-joints").json()
        got[ans] = js[-1]["gap_m"] if len(js) > n0 else None
    assert got == {None: None, "abc": None, "-1": None, "1,5": 1.5}, got
    # Backspace takes back one end on each sheet
    _clear(viewer)
    answers.append("0")
    page.get_by_role("button", name="match line", exact=True).click()
    for x, y in ((700, 150), (700, 650)):
        page.mouse.click(*to_screen(page, x, y))
        page.wait_for_timeout(250)
    page.keyboard.press("Backspace")
    page.mouse.click(*to_screen(page, 700, 600))
    page.get_by_title("Next page").click()
    page.wait_for_timeout(1500)
    page.mouse.click(*to_screen(page, 500, 150))
    page.wait_for_timeout(250)
    page.keyboard.press("Backspace")
    page.mouse.click(*to_screen(page, 500, 200))
    page.wait_for_timeout(250)
    page.mouse.click(*to_screen(page, 500, 650))
    page.wait_for_timeout(1200)
    j = c.get(f"/projects/{pid}/page-joints").json()[-1]
    assert (j["a_y2"], j["b_y1"]) == (600.0, 200.0), j
    page.get_by_title("Previous page").click()
    page.wait_for_timeout(1500)
    # Delete key on the selected line; then the eraser on a new one
    assert _select_joint(page)
    page.keyboard.press("Delete")
    page.wait_for_timeout(1500)
    assert c.get(f"/projects/{pid}/page-joints").json() == []
    _draw(page, answers, "0")
    page.get_by_role("button", name="delete", exact=True).click()
    page.wait_for_timeout(300)
    page.mouse.click(*to_screen(page, 700, 400))
    page.wait_for_timeout(1500)
    assert c.get(f"/projects/{pid}/page-joints").json() == []
    page.close()


# ---------------------------------------------------------------------------
# T7-5  The Inspector's gap box: emptied (or holding text the number box
#       cannot read, which it reports as "") and left, it sets the gap to 0 -
#       Number("") is 0 - with the notice "J1: gap 0 m". A 10 m corridor
#       becomes 0 m without a word; the distances shrink at the next run.
# ---------------------------------------------------------------------------

def test_c_t7_5_an_emptied_gap_box_does_not_set_the_gap_to_zero(viewer):
    _clear(viewer)
    c, pid = viewer["c"], viewer["pid"]
    page, answers = _open(viewer)
    _draw(page, answers, "10")
    assert c.get(f"/projects/{pid}/page-joints").json()[-1]["gap_m"] == 10.0
    assert _select_joint(page)
    box = page.locator("input[type=number][max='200']")
    box.fill("")
    box.blur()
    page.wait_for_timeout(1200)
    gap = c.get(f"/projects/{pid}/page-joints").json()[-1]["gap_m"]
    page.close()
    assert gap == 10.0, f"the gap became {gap} m"


# ---------------------------------------------------------------------------
# T7-7  A gap over 200 m typed in the prompt is not checked in the browser;
#       the server's refusal reaches the person as raw validation JSON
#       ([{"type":"less_than_equal","loc":["body","gap_m"],...}]) and the four
#       clicks are lost. Cosmetic.
# ---------------------------------------------------------------------------

def test_c_t7_7_a_gap_over_the_bound_is_said_in_words(viewer):
    _clear(viewer)
    page, answers = _open(viewer)
    text = _draw(page, answers, "250", None)   # asked again (rc8), then Cancel
    page.close()
    assert '"loc"' not in text and "less_than_equal" not in text, text[:300]
