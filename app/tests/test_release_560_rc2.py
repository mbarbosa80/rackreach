"""5.6.0-rc2: the owner's decision on criticality ("only alert", 6 Nov 2026)
and the independent test of rc1 (T1-1 .. T1-10).

The repro tests of the tester (``tester-560/repro/test_t560.py``) asserted
rc1's behaviour; the ones that describe a defect are inverted here.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="ner-560rc2-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/rc2.sqlite3")

import pymupdf                                                     # noqa: E402
from fastapi.testclient import TestClient                          # noqa: E402

from app import migrate                                            # noqa: E402
from app.db import SessionLocal, init_db                           # noqa: E402
from app.domain.coverage import NER, compute_coverage              # noqa: E402
from app.domain.distance import (ALERT_LABEL, DemandType,          # noqa: E402
                                 DistanceRules, Status)
from app.domain.geometry import Area, Point                        # noqa: E402
from app.domain.pdf_ingest import ElementKind                      # noqa: E402
from app.domain.reports import export_csv                          # noqa: E402
from app.domain.routes import RouteSegment, build_network, natural_key  # noqa: E402
from app.domain.units import Scale                                 # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
P = {1: DECI}


def _n(key, x, y=0.0, crit=True, ports=None, used=0):
    return NER(key=key, label=key,
               kind=ElementKind.NER_CRITICAL if crit else ElementKind.NER_NONCRITICAL,
               point=Point(x, y, 1), critical=crit, total_ports=ports, used_ports=used)


def _seg(x0, y0, x1, y1, label=""):
    return RouteSegment(Point(x0, y0, 1), Point(x1, y1, 1), 1, label=label)


def _run(segs, ners, crit=True, demand=DemandType.GENERAL, pinned=None, poly=None,
         enforce=True, ports=2.0, points=()):
    net = build_network(segs, [], P) if segs else None
    a = Area(key="S", name="S", page=1, label_point=Point(0.0, 0.0, 1), polygon=poly)
    areas = [(a, ports, demand, crit, True)] + list(points)
    res = compute_coverage(areas=areas, ners=ners, scale_for_page=P, rules=DistanceRules(),
                           network=net, pinned=pinned, enforce_criticality=enforce)
    return res, next(x for x in res.assignments if x.space_key == "S")


def _t(a):
    return " | ".join(a.reasons)


# =============================================================================
# T1-1 - the criticality alert (the default of every project)
# =============================================================================

def test_alert_labels_are_the_owners_words():
    assert ALERT_LABEL == {"no_critical_ner_in_limit": "No critical NER in limit",
                           "critical_ner_in_limit": "Critical NER in limit"}


def test_alert_critical_ner_within_the_limit_not_the_nearest():
    """The nearest NER serves, as for every space; the critical one within
    the limit is the suggestion; the space is served and counted served."""
    res, a = _run([], [_n("NC", 100, crit=False), _n("C", 400)], enforce=False)
    assert a.ner_key == "NC" and a.status is Status.SERVED
    assert a.criticality_alert == "critical_ner_in_limit"
    assert a.critical_suggestion == {"ner_key": "C", "ner_label": "C", "distance_m": 40.0,
                                     "method": "straight_line", "within_limit": True,
                                     "full": False, "free_ports": None}
    assert ("Critical NER in limit: marked critical, served by NC, not critical-rated; the "
            "nearest critical NER is C at 40.0 m in a straight line, within the 90 m limit - "
            "choosing one in Served by settles it") in _t(a)
    assert ("C", pytest.approx(40.0)) in a.alternatives
    s = res.summary()
    assert s["served"] == 1 and s["criticality_alerts"] == 1
    assert s["criticality_alerts_by_kind"] == {"critical_ner_in_limit": 1}
    assert res.alerts and res.alerts[0]["alert"] == "critical_ner_in_limit"
    assert not res.exceptions, "an alert is not an exception"
    d = a.to_dict()
    assert d["criticality_alert_label"] == "Critical NER in limit" and d["space_critical"] is True


def test_alert_no_critical_ner_in_limit_names_the_nearest_beyond_it():
    _res, a = _run([], [_n("NC", 100, crit=False), _n("C", 1200)], enforce=False)
    assert a.ner_key == "NC" and a.status is Status.SERVED
    assert a.criticality_alert == "no_critical_ner_in_limit"
    assert a.critical_suggestion["ner_key"] == "C" and a.critical_suggestion["distance_m"] == 120.0
    assert not a.critical_suggestion["within_limit"]
    assert "the nearest critical NER is C at 120.0 m in a straight line, over the 90 m limit" in _t(a)


def test_alert_photographers_use_the_70_m_limit():
    _res, a = _run([], [_n("NC", 100, crit=False), _n("C", 800)], enforce=False,
                   demand=DemandType.PHOTOGRAPHER)
    assert a.criticality_alert == "no_critical_ner_in_limit"
    assert "C at 80.0 m in a straight line, over the 70 m limit" in _t(a)


def test_alert_with_no_critical_ner_measurable():
    res, a = _run([], [_n("NC", 100, crit=False)], enforce=False)
    assert a.criticality_alert == "no_critical_ner_in_limit" and a.critical_suggestion is None
    assert "no critical NER is measurable from this space" in _t(a)
    assert res.summary()["criticality_alerts"] == 1


def test_alert_never_changes_the_ner_the_status_or_the_distance():
    """The owner's decision: criticality never changes which NER serves a
    space by default - the row is the row of a space whose criticality is
    not stated, plus the alert."""
    ners = [_n("NC", 100, crit=False, ports=4, used=3), _n("C", 400), _n("NC2", 300, crit=False)]
    for crit in (True, False):
        _r1, a = _run([], ners, crit=crit, enforce=False)
        _r2, b = _run([], ners, crit=None, enforce=False)
        assert (a.ner_key, a.status, a.evaluation.effective_distance_m) == \
            (b.ner_key, b.status, b.evaluation.effective_distance_m)


def test_a_full_suggested_critical_ner_is_said_full():
    """T1-8: a suggested NER out of ports says so."""
    _res, a = _run([], [_n("NC", 100, crit=False), _n("C", 400, ports=4, used=4)], enforce=False)
    assert a.critical_suggestion["full"] is True and a.critical_suggestion["free_ports"] == 0.0
    assert "within the 90 m limit, out of ports (0 free against 2 required)" in _t(a)


def test_a_choice_in_served_by_settles_the_alert():
    _res, a = _run([], [_n("NC", 100, crit=False), _n("C", 400)], enforce=False,
                   pinned={"S": "NC"})
    assert a.ner_key == "NC" and a.pinned and a.criticality_alert is None


@pytest.mark.parametrize("crit", [False, None])
def test_no_alert_for_a_space_not_marked_critical(crit):
    _res, a = _run([], [_n("NC", 100, crit=False), _n("C", 400)], crit=crit, enforce=False)
    assert a.criticality_alert is None


def test_no_alert_on_a_critical_ner():
    _res, a = _run([], [_n("C", 100), _n("NC", 50, crit=False, ports=2, used=2)], enforce=False)
    assert a.ner_key in ("C", "NC")
    if a.ner_key == "C":
        assert a.criticality_alert is None


def test_a_critical_ner_set_aside_by_the_route_rule_counts_as_within_the_limit():
    """T1-4 in the alert: a critical NER 24 m away in a straight line, a
    tray reaching only a non-critical NER: the alert is *Critical NER in
    limit* with C as the suggestion."""
    segs = [_seg(-50, 10, 400, 10, "R1")]
    _res, a = _run(segs, [_n("C", 0, -240), _n("NC", 300, 10, crit=False)], enforce=False)
    assert a.ner_key == "NC"
    assert a.criticality_alert == "critical_ner_in_limit"
    assert a.critical_suggestion["ner_key"] == "C" and a.critical_suggestion["distance_m"] == 24.0


def test_the_note_criticality_not_stated_only_when_it_is_not():
    _r, a = _run([], [_n("NC", 100, crit=False)], crit=None, enforce=False)
    assert "criticality not stated in the TEAP" in _t(a)
    _r, b = _run([], [_n("NC", 100, crit=False)], crit=False, enforce=False)
    assert "criticality not stated in the TEAP" not in _t(b)


def test_a_wifi_point_carries_no_alert():
    wp = Area(key="W", name="AP", page=1, label_point=Point(5.0, 0.0, 1),
              meta={"kind": "wireless_point", "space_key": "S", "space_name": "S"})
    res, _a = _run([], [_n("NC", 100, crit=False), _n("C", 400)], enforce=False,
                   points=[(wp, 0.0, DemandType.GENERAL, None, True)])
    w = next(x for x in res.assignments if x.space_key == "W")
    assert w.criticality_alert is None and res.summary()["criticality_alerts"] == 1
    assert w.to_dict()["of_space"] == "S"


def test_the_workbook_and_the_csv_carry_the_alert(tmp_path):
    res, _a = _run([], [_n("NC", 100, crit=False), _n("C", 400)], enforce=False)
    path = export_csv(str(tmp_path / "c.csv"), res)
    import csv
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert rows[0]["status"] == "served"
    assert rows[0]["criticality_alert"] == "critical_ner_in_limit"
    assert rows[0]["critical_ner_suggested"] == "C at 40.0 m (in a straight line), within the limit"


# =============================================================================
# T1-1 - through the application: on by default as an alert, enforced on demand
# =============================================================================

def _sign_in(client: TestClient) -> None:
    from app import auth as A
    with SessionLocal() as db:
        try:
            A.create_user(db, username="tester", password="a-long-test-phrase",
                          display_name="Test User", is_admin=True, actor="tests")
        except A.AuthError:
            pass
    assert client.post("/api/auth/login", json={"username": "tester",
                                                 "password": "a-long-test-phrase"}).status_code == 200


@pytest.fixture(scope="module")
def client():
    init_db()
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    _sign_in(c)
    return c


def _project(c_x: float) -> int:
    from app.models import (Drawing, DrawingSpace, Element, Page, Project, TeapFile,
                            TeapSpace)
    with SessionLocal() as db:
        p = Project(name=f"alert-{c_x}", venue_code="TST")
        db.add(p)
        db.flush()
        path = f"{TMP}/alert-{c_x}.pdf"
        doc = pymupdf.open()
        doc.new_page(width=800, height=600)
        doc.save(path)
        doc.close()
        d = Drawing(project_id=p.id, filename="d.pdf", stored_path=path, sha256="x" * 64,
                    page_count=1)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=800, height=600, metres_per_unit=1.0,
                    scale_label="1u=1m", scale_source="manual", scale_confirmed=True,
                    sheet_number=""))
        for kind, label, x in (("NER_NONCRITICAL", "NC1", 60.0), ("NER_CRITICAL", "C1", c_x)):
            db.add(Element(project_id=p.id, drawing_id=d.id, page=1, kind=kind, label=label,
                           x=x, y=50, origin="extracted", confidence=0.9, confirmed=True,
                           evidence=[label], total_ports=48))
        tf = TeapFile(project_id=p.id, filename="t.xlsx", stored_path=f"{TMP}/t.xlsx",
                      sha256="y" * 64)
        db.add(tf)
        db.flush()
        db.add(TeapSpace(project_id=p.id, teap_file_id=tf.id, space_key="TST|CRT.001",
                         space_code="CRT.001", space_name="Results", total_ports=4.0,
                         criticality="critical", criticality_confirmed=True))
        db.add(DrawingSpace(project_id=p.id, drawing_id=d.id, page=1, space_code="CRT.001",
                            name="Results", x=50, y=50, confidence=0.9))
        db.commit()
        migrate.backfill_assets(db, db.get(Project, p.id))
        return p.id


def test_the_alert_through_the_application_and_enforcement_on_demand(client):
    pid = _project(130.0)                     # NC1 10 m away, C1 80 m away
    assert client.post(f"/api/projects/{pid}/matching/run").status_code == 200
    body = client.post(f"/api/projects/{pid}/analyse").json()
    a = next(x for x in body["assignments"] if x["space_code"] == "CRT.001")
    assert a["ner_label"] == "NC1" and a["status"] == "served"
    assert a["criticality_alert"] == "critical_ner_in_limit" and a["space_critical"] is True
    assert a["critical_suggestion"]["ner_label"] == "C1"
    assert body["summary"]["served"] == 1 and body["summary"]["criticality_alerts"] == 1
    assert body["alerts"] and not body["exceptions"]
    # the Demand rows (and the Exceptions step, which reads them)
    rows = client.get(f"/api/projects/{pid}/spaces/assignments").json()
    row = next(s for s in rows["spaces"] if s["space_code"] == "CRT.001")
    assert row["criticality_alert"] == "critical_ner_in_limit"
    assert row["group"] == "served" and rows["criticality_alerts"] == 1
    # the workbook
    import openpyxl
    r = client.post(f"/api/projects/{pid}/reports/xlsx").json()
    wb = openpyxl.load_workbook(client.get(r["download"]).content and
                                __import__("io").BytesIO(client.get(r["download"]).content))
    ws = wb["TEAP and coverage"]
    head = [c.value for c in ws[1]]
    line = dict(zip(head, [c.value for c in ws[2]]))
    assert line["Status detail"] == "within the limit"
    assert line["Criticality alert"] == "Critical NER in limit"
    assert line["Critical NER suggested"].startswith("C1 at 80.0 m")

    # enforcement, API only: the rc1 behaviour, and the corrected note
    r = client.post(f"/api/projects/{pid}/criticality/enforce",
                    json={"value": True, "justification": "test"}).json()
    assert "No critical NER in limit" in r["note"] and "out of coverage" not in r["note"]
    body = client.post(f"/api/projects/{pid}/analyse").json()
    a = next(x for x in body["assignments"] if x["space_code"] == "CRT.001")
    assert a["ner_label"] == "C1" and a["status"] == "served" and a["criticality_alert"] is None
    r = client.post(f"/api/projects/{pid}/criticality/enforce",
                    json={"value": False, "justification": "test"}).json()
    assert "criticality alert" in r["note"]


def test_a_choice_on_the_demand_row_settles_the_alert_before_the_next_run(client):
    pid = _project(400.0)                     # C1 340 m away: No critical NER in limit
    assert client.post(f"/api/projects/{pid}/matching/run").status_code == 200
    body = client.post(f"/api/projects/{pid}/analyse").json()
    a = next(x for x in body["assignments"] if x["space_code"] == "CRT.001")
    assert a["criticality_alert"] == "no_critical_ner_in_limit"
    from app.models import TeapSpace
    with SessionLocal() as db:
        t = db.query(TeapSpace).filter_by(project_id=pid).one()
        t.assigned_ner_asset_id = int(a["ner_key"][1:])
        db.commit()
    rows = client.get(f"/api/projects/{pid}/spaces/assignments").json()
    row = next(s for s in rows["spaces"] if s["space_code"] == "CRT.001")
    assert row["criticality_alert"] is None and rows["criticality_alerts"] == 0


def test_enforcement_is_read_by_the_analysis():
    import inspect as _inspect
    from app import services
    assert "enforce_criticality=bool(project.criticality_enforced)" in \
        _inspect.getsource(services.analyse)
    assert "project.criticality_enforced and" not in _inspect.getsource(services.collect_areas)


# =============================================================================
# Enforced mode (API only): rc1's status, T1-4 fixed, T1-8, T1-9
# =============================================================================

def test_t1_4_a_critical_ner_within_the_limit_in_a_straight_line_serves():
    """Inverts the tester's T1_1: C 24 m away in a straight line, a tray
    reaching only NC. rc1 gave *No critical NER in limit*."""
    segs = [_seg(-50, 10, 400, 10, "R1")]
    _res, a = _run(segs, [_n("C", 0, -240), _n("NC", 300, 10, crit=False)])
    assert a.ner_key == "C" and a.status is Status.SERVED, _t(a)
    assert a.evaluation.method.value == "straight_line"


def test_t1_2_repro_a_tie_reaching_only_a_non_critical_ner_keeps_the_critical_one():
    """Inverts the tester's T1_2 (the T1-3 gate case): served by C, as 5.5."""
    segs = [_seg(-100, -10, 100, -10, "A1"), _seg(-100, 10, 400, 10, "B1")]
    _res, a = _run(segs, [_n("C", 0, -250), _n("NC", 300, 10, crit=False)])
    assert a.ner_key == "C" and a.status is Status.SERVED, _t(a)


def test_t1_4_a_routed_critical_ner_within_the_limit_keeps_the_route_rule():
    segs = [_seg(-50, 10, 400, 10, "R1")]
    _res, a = _run(segs, [_n("C2", 300, 10), _n("C", 0, -240), _n("NC", 200, 10, crit=False)])
    assert a.ner_key == "C2" and a.evaluation.method.value == "route", _t(a)


def test_t1_8_a_full_non_critical_ner_is_said_full():
    """Inverts the tester's T1_3."""
    _res, a = _run([], [_n("C", 1200), _n("NC", 100, crit=False, ports=4, used=4)])
    assert a.status is Status.NO_CRITICAL_NER_IN_LIMIT
    assert ("the nearest NER within the limit is NC at 10.0 m in a straight line, not "
            "critical-rated, out of ports (0 free against 2 required) - choose one of the two"
            in a.reasons[-1])


def test_t1_9_one_wording_marked_critical():
    """Inverts the tester's T1_6."""
    _res, a = _run([], [_n("C", 1200), _n("NC", 100, crit=False)], pinned={"S": "NC"})
    assert any("and it is not critical-rated for a space marked critical" in r for r in a.reasons)
    _res, b = _run([], [_n("C", 1200), _n("NC", 100, crit=False)])
    assert "for a space marked critical:" in b.reasons[-1]
    src = (ROOT / "backend" / "app" / "domain" / "coverage.py").read_text(encoding="utf-8")
    assert "matrix marks critical" not in src and "TEAP marks critical" not in src


# =============================================================================
# T1-6 - ties by label in natural order; T1-10 - the joints cached
# =============================================================================

def test_t1_6_r2_comes_before_r10():
    """Inverts the tester's T1_4: two equal routes R2 and R10 - R2 wins."""
    assert sorted(["R10", "R2", "R1", ""], key=natural_key) == ["", "R1", "R2", "R10"]
    segs = [_seg(0, 10, 250, 10, "R2"), _seg(250, 10, 250, 0, "R2"),
            _seg(0, -10, 250, -10, "R10"), _seg(250, -10, 250, 0, "R10")]
    for order in (segs, segs[::-1]):
        net = build_network(order, [], P)
        r = net.route_length_m(Point(0, 0, 1), Point(260, 0, 1), DECI)
        assert r["joint_a"] == (0.0, 10.0)          # R2's tray
        assert r["joint_ties"] == (2, 1)


def test_t1_10_the_joints_of_a_point_are_found_once_and_never_change():
    segs = [_seg(x, 0, x + 100, 0, f"T{x}") for x in range(0, 2000, 100)]
    net = build_network(segs, [], P)
    first = net._attach_all(Point(550, 30, 1), 150.0)
    assert net._attach_all(Point(550, 30, 1), 150.0) is first      # cached
    net._attach_cache.clear()
    again = net._attach_all(Point(550, 30, 1), 150.0)
    assert again == first                                             # same answer
    # a route asked for twice does not grow the cached list (the caller copies)
    n = len(first)
    net.route_options(Point(550, 30, 1), Point(1500, 0, 1), DECI)
    net.route_options(Point(550, 30, 1), Point(1500, 0, 1), DECI)
    assert len(net._attach_all(Point(550, 30, 1), 150.0)) == n
    # a change to the network forgets them
    net.add_segment(_seg(550, 20, 600, 20, "Z"), DECI)
    assert not net._attach_cache


# =============================================================================
# T1-5, T1-7 - the manual and the interface
# =============================================================================

def test_t1_5_and_t1_1_the_manual():
    m = (ROOT / "docs" / "MANUAL.html").read_text(encoding="utf-8")
    # The public edition's manual says "rack" where the team's says NER.
    m = m.replace("Critical rack in limit", "Critical NER in limit").replace(
        "No critical rack in limit", "No critical NER in limit")
    assert "Critical NER in limit" in m and "No critical NER in limit" in m
    assert "criticality alert" in m.lower()
    assert "criticality/enforce" not in m, "the API switch is not advertised"
    i = m.index('id="exceptions"') if 'id="exceptions"' in m else 0
    assert "No critical NER in limit" in m[i:]


def test_t1_7_the_viewer_shows_status_labels_not_keys():
    v = (ROOT / "frontend" / "src" / "pages" / "Viewer.tsx").read_text(encoding="utf-8")
    assert "{t(a.status)}" not in v and "{selected.assignment.status}" not in v
    assert "t(STATUS_LABEL[a.status] || a.status)" in v
    assert "t(STATUS_LABEL[selected.assignment.status] || selected.assignment.status)" in v
    assert "none within the limit" not in v
    assert "ALERT_LABEL[selected.assignment.criticality_alert]" in v


def test_the_interface_knows_the_alert_in_both_languages():
    n = (ROOT / "frontend" / "src" / "lib" / "notation.ts").read_text(encoding="utf-8")
    pt = (ROOT / "frontend" / "src" / "lib" / "strings.pt.ts").read_text(encoding="utf-8")
    assert 'no_critical_ner_in_limit: "No critical NER in limit"' in n
    assert 'critical_ner_in_limit: "Critical NER in limit"' in n
    assert '"No critical NER in limit": "Sem NER crítica no limite"' in pt
    assert '"Critical NER in limit": "NER crítica no limite"' in pt
    panels = (ROOT / "frontend" / "src" / "pages" / "panels.tsx").read_text(encoding="utf-8")
    assert '"x:alert"' in panels and '"attention"' in panels and "AlertBadge" in panels
