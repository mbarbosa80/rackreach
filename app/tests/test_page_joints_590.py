"""5.9.0-rc7 - match lines between two sheets of one level (page joints).

Marcelo, 22 Nov 2026: on a 400-series book a NER at the top right of sheet 15
can serve spaces at the top left of sheet 16, a few metres away across the
cut; the engine measured only within a sheet. A match line marked on both
sheets lets it measure across: straight to the line on one sheet, the gap,
straight on from the line on the other.
"""
from __future__ import annotations

import math
import os

import pytest

from tests.test_ner_register import TMP, client, samples  # noqa: F401  (sets the env)

from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.joints import PageJoint, shortest_across, stand_in
from app.domain.pdf_ingest import ElementKind
from app.domain.units import Scale

RULES = DistanceRules()
M = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
#: sheet 1's right edge (x = 1000) is sheet 2's left edge (x = 0)
SEAM = PageJoint(key="J1", a_page=1, a1=(1000, 0), a2=(1000, 1000),
                 b_page=2, b1=(0, 0), b2=(0, 1000), gap_m=0.0, label="J1")


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


# --- the measurement ----------------------------------------------------------

def test_straight_across_the_seam():
    p = shortest_across(Point(100, 500, 2), M, Point(900, 500, 1), M, [SEAM])
    assert p is not None
    assert p.length_m == pytest.approx((100 + 100) * 0.1)
    assert p.here_m == pytest.approx(10.0) and p.there_m == pytest.approx(10.0)


def test_diagonal_is_the_stitched_straight_line():
    p = shortest_across(Point(100, 800, 2), M, Point(900, 200, 1), M, [SEAM])
    assert p.length_m == pytest.approx(math.hypot(200, 600) * 0.1, abs=1e-6)


def test_the_gap_is_added():
    j = PageJoint(**{**SEAM.__dict__, "gap_m": 2.5})
    p = shortest_across(Point(100, 500, 2), M, Point(900, 500, 1), M, [j])
    assert p.length_m == pytest.approx(22.5)


def test_works_from_either_side_and_ignores_other_sheets():
    a = shortest_across(Point(900, 500, 1), M, Point(100, 500, 2), M, [SEAM])
    assert a.length_m == pytest.approx(20.0)
    assert shortest_across(Point(100, 500, 3), M, Point(900, 500, 1), M, [SEAM]) is None


def test_each_side_on_its_own_scale():
    half = Scale(metres_per_unit=0.05, source="manual", label="x", confirmed=True)
    p = shortest_across(Point(100, 500, 2), half, Point(900, 500, 1), M, [SEAM])
    assert p.length_m == pytest.approx(100 * 0.05 + 100 * 0.1)


def test_a_short_line_bends_the_path_at_its_end():
    short = PageJoint(key="J2", a_page=1, a1=(1000, 0), a2=(1000, 100),
                      b_page=2, b1=(0, 0), b2=(0, 100))
    p = shortest_across(Point(100, 500, 2), M, Point(900, 500, 1), M, [short])
    want = 2 * math.hypot(100, 400) * 0.1
    assert p.length_m == pytest.approx(want, abs=1e-6)
    assert p.t == pytest.approx(1.0)


def test_the_nearest_of_two_joints():
    far = PageJoint(key="J9", a_page=1, a1=(1000, 900), a2=(1000, 1000),
                    b_page=2, b1=(0, 900), b2=(0, 1000), label="far")
    p = shortest_across(Point(100, 500, 2), M, Point(900, 500, 1), M, [far, SEAM])
    assert p.joint.key == "J1"


def test_the_stand_in_is_the_length_away():
    here = Point(100, 500, 2)
    p = shortest_across(here, M, Point(900, 500, 1), M, [SEAM])
    s = stand_in(here, p, M)
    assert s.page == 2
    assert math.hypot(s.x - here.x, s.y - here.y) * 0.1 == pytest.approx(p.length_m)


# --- the engine -----------------------------------------------------------------

def _ner(key, x, y, page, critical=True):
    return NER(key=key, label=key, kind=ElementKind.NER_CRITICAL if critical
               else ElementKind.NER_NONCRITICAL, point=Point(x, y, page),
               critical=critical, total_ports=48, used_ports=0)


def _run(ners, joints, scales=None):
    area = Area(key="S", name="Space", page=2, label_point=Point(100, 500, 2))
    return compute_coverage(areas=[(area, 2.0, DemandType.GENERAL, None, True)], ners=ners,
                            scale_for_page=scales or {1: M, 2: M}, rules=RULES,
                            joints=joints)


def test_without_a_joint_a_ner_on_the_next_sheet_is_no_candidate():
    res = _run([_ner("ACROSS", 900, 500, 1)], None)
    a = res.assignments[0]
    assert a.ner_key is None


def test_with_a_joint_it_serves_and_is_measured():
    res = _run([_ner("ACROSS", 900, 500, 1)], [SEAM])
    a = res.assignments[0]
    assert a.ner_key == "ACROSS"
    assert a.status is Status.SERVED
    assert a.evaluation.effective_distance_m == pytest.approx(20.0, abs=0.05)
    assert any("match line J1" in s for s in a.evaluation.assumptions), a.evaluation.assumptions
    assert a.evaluation.detail.get("joint") == "J1"


def test_it_competes_with_the_sheet_s_own_ners():
    near_here = _ner("HERE", 250, 500, 2)            # 15 m on the space's sheet
    across = _ner("ACROSS", 900, 500, 1)             # 20 m across the line
    assert _run([near_here, across], [SEAM]).assignments[0].ner_key == "HERE"
    far_here = _ner("HERE", 400, 500, 2)              # 30 m
    assert _run([far_here, across], [SEAM]).assignments[0].ner_key == "ACROSS"


def test_beyond_the_limit_across_is_out_of_limit_not_hidden():
    j = PageJoint(**{**SEAM.__dict__, "gap_m": 95.0})
    a = _run([_ner("ACROSS", 900, 500, 1)], [j]).assignments[0]
    assert a.status is Status.OUT_OF_LIMIT
    assert a.nearest_label == "ACROSS"
    assert a.nearest_distance_m == pytest.approx(115.0, abs=0.05)


def test_an_unconfirmed_scale_on_the_ner_s_sheet_keeps_it_provisional():
    loose = Scale(metres_per_unit=0.1, source="detected", label="1u=0.1m", confirmed=False)
    a = _run([_ner("ACROSS", 900, 500, 1)], [SEAM], {1: loose, 2: M}).assignments[0]
    assert a.status is Status.SCALE_NOT_CONFIRMED
    assert a.nearest_label == "ACROSS"


def test_no_scale_on_the_ner_s_sheet_means_no_measurement():
    a = _run([_ner("ACROSS", 900, 500, 1)], [SEAM], {2: M}).assignments[0]
    assert a.ner_key is None


# --- the API --------------------------------------------------------------------

def _project_with_pages(levels=("LEVEL 01", "LEVEL 01", "LEVEL 02")):
    from app.db import SessionLocal, init_db
    from app.models import Drawing, Page, Project
    init_db()
    with SessionLocal() as db:
        p = Project(name=_uid("joints"), venue_code="VEN")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="b.pdf", stored_path="b.pdf",
                    sha256=os.urandom(32).hex(), page_count=len(levels))
        db.add(d)
        db.flush()
        for i, lv in enumerate(levels, 1):
            db.add(Page(drawing_id=d.id, number=i, width=1000, height=1000, level=lv,
                        sheet_number=f"VEN-40{i}-X"))
        db.commit()
        return p.id


def _body(a=1, b=2, **kw):
    return {"a_page": a, "a_x1": 1000, "a_y1": 0, "a_x2": 1000, "a_y2": 1000,
            "b_page": b, "b_x1": 0, "b_y1": 0, "b_x2": 0, "b_y2": 1000, **kw}


def test_create_list_patch_delete(client):
    pid = _project_with_pages()
    r = client.post(f"/api/projects/{pid}/page-joints", json=_body(gap_m=1.5))
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["label"] == f"J{j['id']}" and j["gap_m"] == 1.5
    assert [x["id"] for x in client.get(f"/api/projects/{pid}/page-joints").json()] == [j["id"]]
    r = client.patch(f"/api/projects/{pid}/page-joints/{j['id']}", json={"gap_m": 3})
    assert r.status_code == 200 and r.json()["gap_m"] == 3
    assert client.delete(f"/api/projects/{pid}/page-joints/{j['id']}").status_code == 204
    assert client.get(f"/api/projects/{pid}/page-joints").json() == []
    actions = [a["action"] for a in client.get(f"/api/projects/{pid}/audit").json()]
    assert {"page_joint.created", "page_joint.updated", "page_joint.deleted"} <= set(actions)


@pytest.mark.parametrize("body,why", [
    (_body(a=1, b=1), "two sheets"),
    (_body(a=1, b=3), "different levels"),
    ({**_body(), "a_x2": 1000, "a_y2": 0}, "two ends"),
    (_body(gap_m=-1), ""),
    (_body(gap_m=500), ""),
])
def test_refusals(client, body, why):
    pid = _project_with_pages()
    r = client.post(f"/api/projects/{pid}/page-joints", json=body)
    assert r.status_code == 422, r.text
    assert why in r.text


def test_the_analysis_reads_the_project_s_joints(client, monkeypatch):
    from app import services as S
    pid = _project_with_pages()
    client.post(f"/api/projects/{pid}/page-joints", json=_body(gap_m=2))
    from app.db import SessionLocal
    from app.models import Project
    with SessionLocal() as db:
        joints = S.page_joints(db, db.get(Project, pid))
    assert len(joints) == 1
    assert (joints[0].a_page, joints[0].b_page, joints[0].gap_m) == (1, 2, 2.0)
    import inspect
    assert "joints=page_joints(db, project)" in inspect.getsource(S.analyse)


# --- packages and book removal ---------------------------------------------------

def _book_project(client, samples):
    r = client.post("/api/projects", json={"name": _uid("joints book"), "venue_code": "SYN"})
    pid = r.json()["id"]
    with open(samples["pdf"], "rb") as fh:
        r = client.post(f"/api/projects/{pid}/drawings",
                        files={"file": ("plan.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    for page in (1, 2):
        r = client.post(f"/api/projects/{pid}/pages/{page}/level", json={"level": "LEVEL 01"})
        assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{pid}/page-joints",
                    json=_body(gap_m=4, label="cut A") | {"a_x1": 500, "a_x2": 500})
    assert r.status_code == 201, r.text
    return pid


def test_a_package_carries_the_joint(client, samples):
    pid = _book_project(client, samples)
    r = client.post(f"/api/projects/{pid}/export?background=false")
    assert r.status_code == 200, r.text
    blob = client.get(f"/api/projects/{pid}/export/{r.json()['filename']}").content
    r = client.post("/api/projects/import?background=false&name=",
                    files={"file": ("p.zip", blob, "application/zip")})
    assert r.status_code in (200, 201), r.text
    new = r.json().get("project_id") or r.json().get("id")
    joints = client.get(f"/api/projects/{new}/page-joints").json()
    assert len(joints) == 1
    j = joints[0]
    assert (j["label"], j["gap_m"], j["a_page"], j["b_page"]) == ("cut A", 4.0, 1, 2)
    from app.db import SessionLocal
    from app.models import Drawing, PageJointRow
    with SessionLocal() as db:
        row = db.get(PageJointRow, j["id"])
        assert db.get(Drawing, row.a_drawing_id).project_id == new


def test_removing_the_book_removes_its_joints(client, samples):
    pid = _book_project(client, samples)
    did = client.get(f"/api/projects/{pid}").json()["drawings"][0]["id"]
    r = client.delete(f"/api/projects/{pid}/drawings/{did}")
    assert r.status_code in (200, 204), r.text
    assert client.get(f"/api/projects/{pid}/page-joints").json() == []
