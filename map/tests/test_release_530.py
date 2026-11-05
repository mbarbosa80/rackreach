"""5.3.0 - 4 Nov 2026, the open list closed.

* Routes: "sempre medir pela rota desenhada". When a traced route reaches a
  space, only the NERs it reaches compete (on VKK a room with no route won
  because a route is never shorter than a straight line), and a route
  measured on any sheet the space appears on beats a straight line measured
  on another sheet.
* The venue guard: a TEAP line of another venue is never matched.
* Wi-Fi: a person's changes stand, and each one can be undone.
* The composite map: a sheet no method could place is set beside the map,
  marked rough, and a person can move any sheet.
* The workbook's Reconciliation sheet: the TEAP's totals against the
  analysis's, with the reason for every difference.
* The marked-up PDF is written in a child interpreter.
* OR-Tools is no longer shipped.
"""

from __future__ import annotations

import pathlib

import openpyxl
import pytest
from sqlalchemy import select

from tests.test_wireless_tie_250 import (DWISA, TMP, _line, _match,  # noqa: F401
                                         _ner, _project, _room, _teap, client)
from app.db import SessionLocal
from app import services as S
from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceMethod, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteSegment, build_network
from app.domain.units import Scale
from app.models import Element, Project, SpaceMatchRow, TeapSpace

ROOT = pathlib.Path(__file__).resolve().parents[1]
ONE = Scale(metres_per_unit=1.0, source="manual", label="1u=1m", confirmed=True)
RULES = DistanceRules()


def _seg(a, b, page=1):
    return RouteSegment(Point(*a, page), Point(*b, page), page)


def _n(key, x, y, page=1):
    return NER(key=key, label=key, kind=ElementKind.NER_CRITICAL,
               point=Point(x, y, page), critical=True, total_ports=None)


def _a(key, x, y, page=1):
    return Area(key=key, name=key, page=page, label_point=Point(x, y, page))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

#: 1 unit = 0.1 m, so the 15 m join reach is 150 units and a room 42 m off
#: the route is not on it.
DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)


def _bms_like():
    """The Conference Room case on VKK, 4 Nov 2026: NER A is 37 m away in a
    straight line and about 44 m along the route traced to it; NER B, with no
    route, is 42 m away. Before 5.3.0 B won: A's route against B's line."""
    a = _n("A", 370, 0)
    b = _n("B", -420, 0)
    net = build_network([_seg((0, 20), (200, 100)), _seg((200, 100), (370, 20))], [], {1: DECI})
    return a, b, net


def test_only_the_rooms_a_route_reaches_compete():
    a, b, net = _bms_like()
    res = compute_coverage(areas=[(_a("S", 0, 0), 4.0, DemandType.GENERAL, None, True)],
                           ners=[a, b], scale_for_page={1: DECI}, rules=RULES, network=net)
    (x,) = res.assignments
    assert x.ner_key == "A"
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.evaluation.effective_distance_m > 42.0          # longer than B's line
    assert any("not compared" in r and "B at 42.0 m" in r for r in x.reasons)


def test_without_a_route_the_straight_line_rule_is_unchanged():
    a, b, _ = _bms_like()
    res = compute_coverage(areas=[(_a("S", 0, 0), 4.0, DemandType.GENERAL, None, True)],
                           ners=[a, b], scale_for_page={1: DECI}, rules=RULES, network=None)
    x = res.assignments[0]
    assert x.ner_key == "A" and x.evaluation.method is DistanceMethod.STRAIGHT_LINE
    assert x.evaluation.effective_distance_m == pytest.approx(37.0)


def test_a_pinned_room_with_no_route_still_wins():
    a, b, net = _bms_like()
    res = compute_coverage(areas=[(_a("S", 0, 0), 4.0, DemandType.GENERAL, None, True)],
                           ners=[a, b], scale_for_page={1: DECI}, rules=RULES, network=net,
                           pinned={"S": "B"})
    assert res.assignments[0].ner_key == "B" and res.assignments[0].pinned


def test_a_route_on_another_sheet_of_the_space_beats_a_straight_line():
    """The space is labelled on sheets 1 and 2; the room is drawn on both.
    On sheet 2 (no route) it is 10 m away in a straight line; on sheet 1 the
    route is 30 m. The route is the measurement."""
    room = NER(key="A", label="A", kind=ElementKind.NER_CRITICAL, point=Point(30, 0, 1),
               critical=True, other_points=[Point(10, 0, 2)])
    space = _a("S", 0, 0, page=2)
    space.occurrences = [_a("S", 0, 0, page=1)]
    net = build_network([_seg((0, 0), (30, 0), page=1)], [], {1: ONE})
    res = compute_coverage(areas=[(space, 1.0, DemandType.GENERAL, None, True)],
                           ners=[room], scale_for_page={1: ONE, 2: ONE}, rules=RULES,
                           network=net)
    ev = res.assignments[0].evaluation
    assert ev.method is DistanceMethod.ROUTE and ev.effective_distance_m == pytest.approx(30.0)


def test_a_route_that_reaches_no_room_within_the_limit_says_so():
    """Always by the route: the room the route reaches is out of limit, the
    one with no route is not compared - the row is a problem, and says why."""
    far = _n("A", 1200, 0)
    near = _n("B", -300, 0)
    net = build_network([_seg((0, 20), (1200, 20))], [], {1: DECI})
    res = compute_coverage(areas=[(_a("S", 0, 0), 1.0, DemandType.GENERAL, None, True)],
                           ners=[far, near], scale_for_page={1: DECI}, rules=RULES, network=net)
    x = res.assignments[0]
    assert x.ner_key != "B"
    assert x.status in (Status.OUT_OF_LIMIT, Status.NOT_ANALYSABLE)
    assert any("B at 30.0 m" in r for r in x.reasons)


# ---------------------------------------------------------------------------
# Venue guard
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Wi-Fi: your changes stand, with undo
# ---------------------------------------------------------------------------

def _points(pid, key):
    with SessionLocal() as db:
        return list(db.execute(select(Element).where(
            Element.project_id == pid, Element.kind == "WIRELESS",
            Element.space_key == key).order_by(Element.id)).scalars())


def test_an_extra_point_taken_back_is_never_one_you_dragged():
    src = (ROOT / "backend" / "app" / "services.py").read_text(encoding="utf-8")
    assert 'e.origin == "teap" and e.id not in hand_moved' in src


# ---------------------------------------------------------------------------
# Composite map
# ---------------------------------------------------------------------------

def test_a_rough_or_moved_sheet_is_on_the_picture_and_in_the_frame():
    from app.domain import composite as C
    a = {"page": 1, "state": "confirmed", "clip": [0, 0, 100, 50], "tx": 0, "ty": 0, "s": 1.0}
    b = {"page": 2, "state": "rough", "clip": [0, 0, 100, 50], "tx": 0, "ty": 300, "s": 1.0}
    c = {"page": 3, "state": "not_aligned"}
    assert C.drawn(b) and not C.drawn(c)
    f = C.frame_of([a, b, c], margin=0)
    assert (f["x0"], f["y0"], f["width"], f["height"]) == (0, 0, 100, 350)


def test_moving_a_sheet_makes_it_yours_and_keeps_it(monkeypatch):
    from app import composite_service as CS
    from app.models import CompositeMap
    monkeypatch.setattr(CS, "_compose", lambda row, drawing: None)
    monkeypatch.setattr(CS, "draw_ahead", lambda row: 0)
    pid = _project("composite-530")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        sheets = [{"page": 1, "state": "rough", "mpu": 0.5, "clip": [0, 0, 100, 50],
                   "view": [0, 0, 100, 50], "tx": 0.0, "ty": 0.0, "s": 1.0}]
        m = CompositeMap(project_id=pid, drawing_id=p.drawings[0].id, group_key="g",
                         label="L01", level="LEVEL 01", mpu=0.5, frame=CS.C.frame_of(sheets),
                         sheets=sheets, refine={}, no_scale=[], base_file="", drawing_sha="x")
        db.add(m)
        db.commit()
        out = CS.move_sheet(db, p, m, 1, 10.0, -5.0, actor="tester")
        assert out["manual"] == 1 and out["rough"] == 0
        db.refresh(m)
        s = m.sheets[0]
        assert s["state"] == "manual" and s["tx"] == pytest.approx(20.0)
        assert s["ty"] == pytest.approx(-10.0)
        with pytest.raises(CS.CompositeError):
            CS.move_sheet(db, p, m, 9, 1.0, 1.0)


def test_a_rebuild_keeps_the_sheets_you_moved():
    src = (ROOT / "backend" / "app" / "composite_service.py").read_text(encoding="utf-8")
    assert 'if s.get("state") == "manual" and "tx" in s' in src


# ---------------------------------------------------------------------------
# Reconciliation sheet
# ---------------------------------------------------------------------------

class _D:
    def __init__(self, key, ports, name=None):
        self.space_key, self.space_name, self.space_code = key, name or key, key
        self.total_ports, self.warnings, self.lines = ports, [], []

    def breakdown(self):
        return []


def test_the_reconciliation_sheet_adds_up_and_names_the_pending(tmp_path):
    from app.domain.reports import export_workbook
    n = _n("A", 0, 0)
    res = compute_coverage(
        areas=[(_a("S1", 10, 0), 5.0, DemandType.GENERAL, None, True),
               (_a("S2", 500, 0), 3.0, DemandType.GENERAL, None, True)],
        ners=[n], scale_for_page={1: ONE}, rules=RULES)
    out = tmp_path / "w.xlsx"
    export_workbook(str(out), coverage=res, ners=[n], demands=[_D("S1", 5), _D("S2", 3)],
                    rules=RULES, scale_for_page={1: ONE})
    ws = openpyxl.load_workbook(out)["Reconciliation"]
    rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
    assert rows["Spaces that request ports"][3] == "OK"
    assert rows["Ports requested"][1] == 8 and rows["Ports requested"][3] == "OK"
    assert rows["Ports on a NER + ports pending"][3] == "OK"
    assert rows["Ports on a NER = sum of the NERs' load"][3] == "OK"
    pend = [r for k, r in rows.items() if k.startswith("  ")]
    assert pend and sum(r[2] for r in pend) == 3


def test_a_space_missing_from_the_analysis_is_named(tmp_path):
    from app.domain.reports import export_workbook
    n = _n("A", 0, 0)
    res = compute_coverage(areas=[(_a("S1", 10, 0), 5.0, DemandType.GENERAL, None, True)],
                           ners=[n], scale_for_page={1: ONE}, rules=RULES)
    out = tmp_path / "w.xlsx"
    export_workbook(str(out), coverage=res, ners=[n], demands=[_D("S1", 5), _D("S9", 2)],
                    rules=RULES, scale_for_page={1: ONE})
    ws = openpyxl.load_workbook(out)["Reconciliation"]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True)]
    assert next(r for r in rows if r[0] == "Ports requested")[3] == "differs by 2"
    assert any(r[0] == "In the TEAP, not in the analysis" and r[1] == "S9" for r in rows)


# ---------------------------------------------------------------------------
# Marked-up PDF in a child interpreter; OR-Tools gone; the manual
# ---------------------------------------------------------------------------

def test_the_marked_pdf_goes_through_the_child_interpreter(monkeypatch):
    from app.api import routes as R
    seen = {}

    def fake_run(target, *args, progress=None, **kw):
        seen["target"], seen["kw"] = target, kw
        return {"out": args[1], "off_page": [{"kind": "ner", "key": "A1"}]}
    monkeypatch.setattr(R.isolate, "run", fake_run)
    got = R._marked_pdf("in.pdf", "out.pdf", page_keys={1: 10001}, sheet_labels={10001: "x"})
    assert seen["target"] == "app.domain.reports.export_marked_pdf_job"
    assert "off_page" not in seen["kw"] and got == [{"kind": "ner", "key": "A1"}]


def test_the_job_entry_point_takes_only_plain_data():
    import inspect
    from app.domain.reports import export_marked_pdf_job
    sig = inspect.signature(export_marked_pdf_job)
    assert {"page_keys", "sheet_labels"} <= set(sig.parameters)


def test_or_tools_is_not_shipped():
    for name in ("requirements-lock-win.txt", "requirements-lock-mac.txt",
                 "requirements.txt", "requirements-win.txt"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "ortools" not in text and "protobuf" not in text, name
    assert "ortools" not in (ROOT / "install.bat").read_text(encoding="utf-8")


def test_a_saved_scenario_can_be_sent_to_the_child_interpreter():
    """The first build fell back to the server process on a real project: the
    replayed scenario carried a class made with type() on the fly."""
    import pickle
    ev = S._StoredEvaluation({"method": "route", "effective_distance_m": 12.0})
    back = pickle.loads(pickle.dumps(ev))
    assert back.method.value == "route" and back.effective_distance_m == 12.0
