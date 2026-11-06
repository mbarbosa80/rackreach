"""5.6.0: the owner's decisions of 6 Nov 2026, applied to every project.

1. A room reached by a traced route is judged on its farthest vertex along
   the route (vertex, straight, to where the route joins the sheet, then
   along the route), with shafts or risers in the project or not.
2. A traced route on a sheet whose scale is not confirmed never gives a
   definitive result: *scale not confirmed*.
3. "Every NER within the distance limit is out of ports" is written only
   when it is true.
4. A space as far from two trays: every tray in the tie is tried, the
   shortest route kept, the smallest label on an exact tie - whatever the
   order the trays were registered in.
5. A critical space whose only NERs within the limit are non-critical:
   status *No critical NER in limit*, not served, both options offered.
6. A NER over capacity prints "0 free (over by N)", never a negative count.

Fixtures: 1 unit = 0.1 m (DECI). The space S is at (0, 0) on page 2.
"""

from __future__ import annotations

import csv

import pytest

from app.domain.coverage import NER, compute_coverage, free_ports_text
from app.domain.distance import (GROUP_OF, STATUS_LABEL, DemandType, DistanceMethod,
                                 DistanceRules, Group, Status, group_of)
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteSegment, VerticalLink, build_network
from app.domain.units import Scale

DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
DECI_U = Scale(metres_per_unit=0.1, source="auto", label="1u=0.1m", confirmed=False)
P = {1: DECI, 2: DECI}


def _n(key, x, page=2, crit=True, ports=None, used=0, y=0.0):
    return NER(key=key, label=key,
               kind=ElementKind.NER_CRITICAL if crit else ElementKind.NER_NONCRITICAL,
               point=Point(x, y, page), critical=crit, total_ports=ports, used_ports=used)


def _seg(x0, y0, x1, y1, page=2, label=""):
    return RouteSegment(Point(x0, y0, page), Point(x1, y1, page), page, label=label)


def _shaft(key, x, length_m, lower=1, upper=2, y=0.0):
    return VerticalLink(key, Point(x, y, lower), Point(x, y, upper), length_m)


def _space(poly=None, key="S", x=0.0, y=0.0):
    return Area(key=key, name=key, page=2, label_point=Point(x, y, 2), polygon=poly)


def _result(net, ners, areas, pages=P, pinned=None, respect_capacity=True):
    """``areas``: (area, ports, demand, critical)."""
    return compute_coverage(areas=[(a, p, d, c, True) for a, p, d, c in areas],
                            ners=ners, scale_for_page=pages, rules=DistanceRules(),
                            network=net, pinned=pinned, respect_capacity=respect_capacity)


def _run(net, ners, area=None, pages=P, demand=DemandType.GENERAL, crit=None,
         pinned=None, ports=2.0):
    res = _result(net, ners, [(area or _space(), ports, demand, crit)], pages, pinned)
    return {a.space_key: a for a in res.assignments}["S"]


def _t(x):
    return " | ".join(x.reasons + (x.evaluation.assumptions if x.evaluation else []))


def _with_shaft(segs, has_shaft):
    return build_network(segs, [_shaft("Z", 5000, 4.0)] if has_shaft else [], P)


# =============================================================================
# 1. The far corner along the route, on every project
# =============================================================================

#: A tray from the space's centre: up 40 m, then right 40 m, to the NER: 80 m.
#: The room's far vertex is on the other side, ``corner`` units from the
#: joint (0, 0): its straight line to the NER (about 64 m) is shorter than the
#: route, so the old rule judged 80 m; along the route it is 80 + corner.
GENERAL_TRAY = [_seg(0, 0, 0, 400), _seg(0, 400, 400, 400)]


def _room(corner_units):
    """A thin room centred on the space (where the tray starts), its four
    corners ``corner_units`` from the centre. Its representative point is the
    centre, so the route joins the sheet there with no lead-in."""
    a = (corner_units ** 2 - 1.0) ** 0.5
    return [(-a, -1.0), (a, -1.0), (a, 1.0), (-a, 1.0)]


@pytest.mark.parametrize("has_shaft", [False, True])
@pytest.mark.parametrize("corner, expected, status", [
    (99.9, 89.99, Status.NEAR_LIMIT),      # just inside the 90 m limit
    (100.1, 90.01, Status.OUT_OF_LIMIT),   # just over
])
def test_1_far_corner_along_the_route_at_the_90_m_limit(has_shaft, corner, expected, status):
    x = _run(_with_shaft(GENERAL_TRAY, has_shaft), [_n("N", 400, y=400)],
             area=_space(_room(corner)))
    ev = x.evaluation
    assert ev.method is DistanceMethod.ROUTE
    assert ev.effective_distance_m == pytest.approx(expected, abs=1e-6), _t(x)
    assert ev.farthest_point_distance_m == pytest.approx(expected, abs=1e-6)
    assert x.status is status, (x.status, _t(x))
    assert "then along the route" in _t(x)
    assert (x.ner_key == "N") == (status is not Status.OUT_OF_LIMIT)


@pytest.mark.parametrize("has_shaft", [False, True])
@pytest.mark.parametrize("corner, status", [
    (99.9, Status.NEAR_LIMIT),             # 69.99 m
    (100.1, Status.OUT_OF_LIMIT),          # 70.01 m
])
def test_1_photographers_far_corner_along_the_route_at_the_70_m_limit(has_shaft, corner, status):
    tray = [_seg(0, 0, 0, 300), _seg(0, 300, 300, 300)]          # 60 m
    x = _run(_with_shaft(tray, has_shaft), [_n("N", 300, y=300)],
             area=_space(_room(corner)), demand=DemandType.PHOTOGRAPHER)
    assert x.evaluation.limit_m == 70
    assert x.evaluation.effective_distance_m == pytest.approx(60 + corner / 10, abs=1e-6)
    assert x.status is status, (x.status, _t(x))


def test_1_a_room_without_polygon_is_judged_on_its_centre_as_before():
    x = _run(_with_shaft(GENERAL_TRAY, False), [_n("N", 400, y=400)])
    assert x.evaluation.effective_distance_m == pytest.approx(80.0)
    assert x.evaluation.farthest_point_distance_m is None
    assert x.status is Status.SERVED


def test_1_a_far_corner_nearer_than_the_centre_route_does_not_shorten_it():
    """The far corner only lengthens a route: a tiny room stays at 80 m."""
    x = _run(_with_shaft(GENERAL_TRAY, False), [_n("N", 400, y=400)],
             area=_space(_room(5.0)))
    assert x.evaluation.effective_distance_m == pytest.approx(80.5)
    assert x.status is Status.SERVED


def test_1_the_far_corner_can_put_every_routed_ner_out_of_limit():
    """Two NERs by route: A 80 m from the centre, B 85 m. The room's far
    corner is 12.01 m from where the route joins the sheet: along the routes
    A is 92.01 m, B 97.01 m - both out. The old straight line from the corner
    to A (about 49 m) kept A at 80 m."""
    segs = [_seg(0, 0, 0, 400), _seg(0, 400, 400, 400),           # to A, 80 m
            _seg(0, 0, 0, -425), _seg(0, -425, 425, -425)]          # to B, 85 m
    poly = [(-5, -120), (5, -120), (5, 120), (-5, 120)]
    x = _run(_with_shaft(segs, False), [_n("A", 400, y=400), _n("B", 425, y=-425)],
             area=_space(poly))
    assert x.ner_key is None and x.status is Status.OUT_OF_LIMIT, _t(x)
    assert x.nearest_label == "A"
    assert x.nearest_distance_m == pytest.approx(92.01, abs=0.01)
    assert [k for k, _ in x.alternatives] == ["A", "B"]


# =============================================================================
# 2. Unconfirmed scale on a traced route, on every project
# =============================================================================

@pytest.mark.parametrize("has_shaft", [False, True])
def test_2_a_route_on_an_unconfirmed_space_sheet_is_not_definitive(has_shaft):
    pages = {1: DECI, 2: DECI_U}
    net = build_network(GENERAL_TRAY, [_shaft("Z", 5000, 4.0)] if has_shaft else [], pages)
    x = _run(net, [_n("N", 400, y=400)], pages=pages)
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.status is Status.SCALE_NOT_CONFIRMED, _t(x)
    assert x.ner_key is None and x.nearest_label == "N"
    assert x.evaluation.requires_human_validation
    # confirmed: served, definitive
    y = _run(build_network(GENERAL_TRAY, [], P), [_n("N", 400, y=400)])
    assert y.status is Status.SERVED and y.ner_key == "N"


def test_2_a_pinned_ner_by_route_on_an_unconfirmed_sheet_keeps_the_space():
    pages = {1: DECI, 2: DECI_U}
    x = _run(build_network(GENERAL_TRAY, [], pages), [_n("N", 400, y=400)], pages=pages,
             pinned={"S": "N"})
    assert x.ner_key == "N" and x.pinned
    assert x.status is Status.SCALE_NOT_CONFIRMED


def test_2_a_route_through_a_shaft_waits_for_the_ner_sheet_scale():
    """The space's sheet confirmed, the NER's sheet (page 1) not."""
    pages = {1: DECI_U, 2: DECI}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, page=1)], pages=pages)
    assert x.status is Status.SCALE_NOT_CONFIRMED and x.ner_key is None, _t(x)
    y = _run(build_network([], [_shaft("S1", 20, 8.0)], P), [_n("UP", 0, page=1)])
    assert y.status is Status.SERVED and y.ner_key == "UP"


# =============================================================================
# 3. The capacity note is true
# =============================================================================

def test_3_a_ner_set_aside_by_the_route_rule_is_named_without_shafts():
    net = build_network([_seg(0, 10, 300, 10)], [], P)
    x = _run(net, [_n("R", 300, y=10, ports=4, used=4), _n("F", -200, ports=48)])
    assert x.ner_key == "R" and x.status is Status.INSUFFICIENT_CAPACITY
    t = _t(x)
    assert "every NER within the distance limit is out of ports" not in t, t
    assert ("every NER compared within the distance limit is out of ports — assigned to "
            "the nearest one, R, anyway; the overload is reported, not hidden (F, within "
            "the limit with free ports, was not compared: see above)") in t, t


def test_3_a_non_critical_ner_with_free_ports_is_named_for_a_critical_space():
    x = _run(None, [_n("C", 300, ports=4, used=4), _n("NC", -200, crit=False, ports=48)],
             crit=True)
    assert x.ner_key == "C" and x.status is Status.INSUFFICIENT_CAPACITY
    t = _t(x)
    assert "every NER within the distance limit is out of ports" not in t, t
    assert ("every critical-rated NER within the distance limit is out of ports — "
            "assigned to the nearest one, C, anyway; the overload is reported, not hidden "
            "(NC, within the limit with free ports, is not critical-rated)") in t, t


def test_3_when_every_ner_is_full_the_sentence_stands():
    x = _run(None, [_n("A", 300, ports=4, used=4), _n("B", -500, ports=4, used=4)])
    assert x.ner_key == "A" and x.status is Status.INSUFFICIENT_CAPACITY
    assert ("every NER within the distance limit is out of ports — assigned to the "
            "nearest one, A, anyway; the overload is reported, not hidden") in _t(x)


def test_3_a_far_ner_with_free_ports_outside_the_limit_is_not_named():
    x = _run(None, [_n("A", 300, ports=4, used=4), _n("FAR", -1000, ports=48)])
    assert x.ner_key == "A"
    t = _t(x)
    assert "every NER within the distance limit is out of ports" in t and "FAR," not in t


# =============================================================================
# 4. Equidistant trays: the shortest, then the smallest label, any order
# =============================================================================

def _trays(order, b_len_units=400, labels=("A", "B")):
    """Space 3 m from tray A (above) and tray B (below). Tray A leads to the
    NER at (-200, 30) in 20 m; tray B the long way round."""
    la, lb = labels
    a = [_seg(0, 30, -200, 30, label=la)]
    b = [_seg(0, -30, -b_len_units, -30, label=lb),
         _seg(-b_len_units, -30, -b_len_units, 30, label=lb + "2"),
         _seg(-b_len_units, 30, -200, 30, label=lb + "3")]
    return a + b if order == "AB" else b + a


@pytest.mark.parametrize("has_shaft", [False, True])
@pytest.mark.parametrize("order", ["AB", "BA"])
def test_4_equidistant_trays_shortest_route_whatever_the_order(order, has_shaft):
    x = _run(_with_shaft(_trays(order), has_shaft), [_n("HERE", -200, y=30)])
    assert x.evaluation.effective_distance_m == pytest.approx(23.0, abs=1e-6), (order, _t(x))


@pytest.mark.parametrize("order", ["AB", "BA"])
def test_4_photographers_status_does_not_follow_registration_order(order):
    """Tray B the long way is 3 + 50 + 6 + 30 = 89 m: out of 70 m; tray A
    is 23 m. 5.5 gave either, by registration order."""
    x = _run(_with_shaft(_trays(order, b_len_units=500), False), [_n("HERE", -200, y=30)],
             demand=DemandType.PHOTOGRAPHER)
    assert x.status is Status.SERVED and x.ner_key == "HERE", (order, _t(x))


@pytest.mark.parametrize("order", ["fwd", "rev"])
def test_4_exact_tie_takes_the_smallest_tray_label(order):
    """Two trays 3 m above and 3 m below, each 20 m to its own end, the NER
    straight across at the same distance from both: an exact tie. The tray
    labelled "K" (below) comes before "M" (above), whatever the order."""
    up = [_seg(0, 30, -200, 30, label="M"), _seg(-200, 30, -200, 0, label="M2")]
    down = [_seg(0, -30, -200, -30, label="K"), _seg(-200, -30, -200, 0, label="K2")]
    segs = up + down if order == "fwd" else list(reversed(down + up))
    net = build_network(segs, [], P)
    opts = net.route_options(Point(0, 0, 2), Point(-200, 0, 2), DECI)
    assert opts[0]["length_m"] == pytest.approx(26.0)
    assert opts[0]["joint_a"] == (0.0, -30.0), opts[0]
    x = _run(net, [_n("HERE", -200)])
    assert x.evaluation.effective_distance_m == pytest.approx(26.0)


def test_4_a_corner_where_two_trays_meet_is_one_joint():
    """The space nearest the shared end of two trays: one joint, not three."""
    net = build_network([_seg(0, 30, 300, 30, label="A"), _seg(0, 30, 0, 400, label="B")], [], P)
    joints = net._joints(Point(-10, 20, 2), 600.0, False)
    assert len(joints) == 1 and joints[0]["edge"] is None and joints[0]["label"] == "A"


def test_4_a_nearer_tray_still_wins_alone():
    net = build_network([_seg(0, 20, -200, 20, label="Z"), _seg(0, -30, -200, -30, label="A")],
                        [], P)
    joints = net._joints(Point(0, 0, 2), 600.0, False)
    assert [j["label"] for j in joints] == ["Z"]


# =============================================================================
# 5. No critical NER in limit
# =============================================================================

def test_5_the_status_is_known_everywhere():
    s = Status.NO_CRITICAL_NER_IN_LIMIT
    assert s.value == "no_critical_ner_in_limit"
    assert GROUP_OF[s] is Group.PENDING and group_of(s.value) is Group.PENDING
    assert STATUS_LABEL[s] == "no critical NER in limit"


@pytest.mark.parametrize("crit_x, crit_d, beyond", [(-1200, 120.0, True), (-950, 95.0, True)])
def test_5_critical_space_only_non_critical_within_the_limit(crit_x, crit_d, beyond):
    ners = [_n("NC", 100, crit=False), _n("C", crit_x)]
    res = _result(None, ners, [(_space(), 2.0, DemandType.GENERAL, True)])
    x = res.assignments[0]
    assert x.status is Status.NO_CRITICAL_NER_IN_LIMIT
    assert x.ner_key is None
    assert x.nearest_label == "C" and x.nearest_distance_m == pytest.approx(crit_d)
    assert x.evaluation.effective_distance_m == pytest.approx(crit_d)
    assert x.evaluation.requires_human_validation
    # both options, nearest first, with their distances
    assert x.alternatives == [("NC", pytest.approx(10.0)), ("C", pytest.approx(crit_d))]
    t = _t(x)
    assert ("no critical NER within the 90 m limit for a space marked critical: "
            f"the nearest critical NER is C at {crit_d:.1f} m in a straight line, over the "
            "90 m limit; the nearest NER within the limit is NC at 10.0 m in a straight "
            "line, not critical-rated - choose one of the two in Served by") in t, t
    # not served, not within limits, in the exceptions, uncovered
    s = res.summary()
    assert s["served"] == 0
    assert s["by_status"] == {"no_critical_ner_in_limit": 1}
    assert s["by_group"] == {"pending": 1}
    assert "S" in res.uncovered and s["uncovered"] == 1
    assert [e["status"] for e in res.exceptions] == ["no_critical_ner_in_limit"]
    assert res.exceptions[0]["detail"].startswith("no critical NER within the 90 m limit")
    assert res.ner_load["NC"]["assigned_ports"] == 0 and res.ner_load["C"]["assigned_ports"] == 0


def test_5_photographers_use_the_70_m_limit():
    ners = [_n("NC", 650, crit=False), _n("C", -750)]          # 65 m, 75 m
    x = _run(None, ners, crit=True, demand=DemandType.PHOTOGRAPHER)
    assert x.status is Status.NO_CRITICAL_NER_IN_LIMIT
    assert "no critical NER within the 70 m limit" in _t(x)
    # at 69.9 m the critical NER serves a photographers' space
    y = _run(None, [_n("NC", 650, crit=False), _n("C", -699)], crit=True,
             demand=DemandType.PHOTOGRAPHER)
    assert y.ner_key == "C" and y.status in (Status.SERVED, Status.NEAR_LIMIT)


def test_5_no_critical_ner_measurable_at_all():
    x = _run(None, [_n("NC", 100, crit=False), _n("NC2", 300, crit=False)], crit=True)
    assert x.status is Status.NO_CRITICAL_NER_IN_LIMIT
    assert x.nearest_label == "NC"
    assert x.alternatives == [("NC", pytest.approx(10.0))]
    assert ("no critical NER is measurable from this space; the nearest NER within the "
            "limit is NC at 10.0 m in a straight line, not critical-rated - choose the NER "
            "in Served by") in _t(x)


def test_5_a_critical_ner_within_the_limit_still_serves():
    x = _run(None, [_n("NC", 100, crit=False), _n("C", -800)], crit=True)
    assert x.ner_key == "C" and x.status is Status.SERVED


def test_5_a_full_critical_ner_within_the_limit_is_insufficient_capacity_not_the_new_status():
    x = _run(None, [_n("NC", 100, crit=False), _n("C", -800, ports=2, used=2)], crit=True)
    assert x.ner_key == "C" and x.status is Status.INSUFFICIENT_CAPACITY


@pytest.mark.parametrize("crit", [None, False])
def test_5_a_space_not_marked_critical_is_served_by_the_non_critical_ner(crit):
    x = _run(None, [_n("NC", 100, crit=False), _n("C", -1200)], crit=crit)
    assert x.ner_key == "NC" and x.status is Status.SERVED


def test_5_a_pick_of_the_non_critical_ner_settles_it():
    x = _run(None, [_n("NC", 100, crit=False), _n("C", -1200)], crit=True, pinned={"S": "NC"})
    assert x.ner_key == "NC" and x.pinned and x.status is Status.SERVED
    assert "not critical-rated" in _t(x)


def test_5_a_pick_of_the_critical_ner_beyond_the_limit_settles_it():
    x = _run(None, [_n("NC", 100, crit=False), _n("C", -1200)], crit=True, pinned={"S": "C"})
    assert x.ner_key == "C" and x.pinned and x.status is Status.OUT_OF_LIMIT


def test_5_a_critical_ner_waiting_on_its_scale_keeps_scale_not_confirmed():
    """The space is also on page 3 (unconfirmed), where a critical NER is
    within the limit; on page 2 only a non-critical NER is: the critical
    one would serve once the scale is confirmed."""
    pages = {2: DECI, 3: DECI_U}
    area = _space()
    area.occurrences = [Area(key="S", name="S", page=3, label_point=Point(0, 0, 3),
                             meta={"link_basis": "same code"})]
    x = _run(None, [_n("NC", 100, crit=False), _n("C", 300, page=3)], area=area,
             pages=pages, crit=True)
    assert x.status is Status.SCALE_NOT_CONFIRMED and x.nearest_label == "C", _t(x)


def test_5_with_a_shaft_a_non_critical_floor_and_upstairs():
    """Floor NER non-critical within the limit, UP non-critical through a
    shaft: no critical NER anywhere within reach."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("HERE", -149, crit=False), _n("UP", 0, page=1, crit=False)], crit=True)
    assert x.status is Status.NO_CRITICAL_NER_IN_LIMIT and x.ner_key is None, _t(x)
    y = _run(net, [_n("HERE", -149, crit=False), _n("UP", 0, page=1)], crit=True)
    assert y.ner_key == "UP" and y.status is Status.SERVED, _t(y)


def test_5_a_traced_route_to_a_non_critical_ner_keeps_the_critical_one_within_the_limit():
    """rc2, tester T1-4: a critical NER within the limit in a straight line is
    within the limit; the route reaching only a non-critical NER does not set
    it aside (it used to, and the row read "No critical NER in limit")."""
    net = build_network([_seg(0, 10, 300, 10)], [], P)
    x = _run(net, [_n("R", 300, y=10, crit=False), _n("C", -200)], crit=True)
    assert x.ner_key == "C" and x.status is Status.SERVED, _t(x)
    assert ("the critical-rated NER(s) within the limit in a straight line were compared "
            "too: C at 20.0 m in a straight line") in _t(x)


def test_5_the_workbook_and_the_csv_carry_the_status(tmp_path):
    import openpyxl

    from app.domain.reports import export_csv, export_workbook

    ners = [_n("NC", 100, crit=False), _n("C", -1200)]
    res = _result(None, ners, [(_space(), 2.0, DemandType.GENERAL, True)])

    class _D:
        space_key = space_name = space_code = "S"
        total_ports, warnings, lines = 2.0, [], []

        def breakdown(self):
            return ["TOTAL = 2 port(s)"]

    out = str(tmp_path / "wb.xlsx")
    export_workbook(out, coverage=res, ners=ners, demands=[_D()], rules=DistanceRules(),
                    project_name="t", scale_for_page=P)
    wb = openpyxl.load_workbook(out)
    ws = wb["TEAP and coverage"]
    headers = [c.value for c in ws[1]]
    row = {h: ws.cell(row=2, column=i + 1).value for i, h in enumerate(headers)}
    assert row["Status"] == "Pending — needs a room or a NER"
    assert row["Status detail"] == "no critical NER in limit"
    assert row["Serving NER"] in ("", None)
    assert row["Needs validation"] == "yes"
    text = "\n".join(str(c.value) for ws in wb.worksheets for r in ws.iter_rows()
                     for c in r if c.value is not None)
    assert "no critical NER within the 90 m limit" in text

    path = export_csv(str(tmp_path / "c.csv"), res)
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert rows[0]["status"] == "no_critical_ner_in_limit"
    assert rows[0]["ner"] == "" and rows[0]["needs_validation"] == "yes"


def test_5_the_marked_pdf_paints_it_in_its_own_colour():
    from app.domain.reports import STATUS_FILL

    c = STATUS_FILL[Status.NO_CRITICAL_NER_IN_LIMIT]
    assert c not in (STATUS_FILL[Status.SERVED], STATUS_FILL[Status.OUT_OF_LIMIT])


def test_5_the_interface_knows_the_status_in_both_languages():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib"
    notation = (root / "notation.ts").read_text(encoding="utf-8")
    pt = (root / "strings.pt.ts").read_text(encoding="utf-8")
    assert 'no_critical_ner_in_limit: "No critical NER in limit"' in notation
    assert 'no_critical_ner_in_limit: "pending"' in notation
    assert "no_critical_ner_in_limit: \"#" in notation
    assert '"No critical NER in limit": "Sem NER crítica no limite"' in pt
    assert '"no critical NER in limit": "sem NER crítica no limite"' in pt
    assert "VTCM" not in notation and "VTCM" not in pt


# =============================================================================
# 6. Free ports never negative
# =============================================================================

@pytest.mark.parametrize("free, noun, text", [
    (12, "free", "12 free"),
    (0, "free", "0 free"),
    (-9, "free", "0 free (over by 9)"),
    (-2.5, "free", "0 free (over by 2.5)"),
    (-0.01, "free", "0 free"),
    (-9, "port(s) free", "0 port(s) free (over by 9)"),
])
def test_6_free_ports_text(free, noun, text):
    assert free_ports_text(free, noun) == text


def test_6_a_pinned_ner_over_capacity_says_over_by():
    x = _run(None, [_n("A", 100, ports=4, used=13)], pinned={"S": "A"}, ports=8.0)
    t = _t(x)
    assert x.status is Status.INSUFFICIENT_CAPACITY
    assert "and it has 0 port(s) free (over by 9) against 8 required — over capacity" in t, t
    assert "-9" not in t


def test_6_a_floor_ner_over_capacity_says_over_by():
    """In the floor gate (a project with a shaft): HERE is 9 over capacity."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, page=1), _n("HERE", -149, ports=10, used=19)], ports=8.0)
    t = _t(x)
    assert "out of ports (0 free (over by 9) against 8 required)" in t, t
    assert "-9 free" not in t


def test_6_an_unplaced_space_on_a_ner_over_capacity_says_over_by():
    area = Area(key="S", name="S", page=0, label_point=Point(0, 0, 0), meta={"unplaced": True})
    res = _result(None, [_n("A", 100, ports=4, used=13)],
                  [(area, 8.0, DemandType.GENERAL, None)], pinned={"S": "A"})
    t = " | ".join(res.assignments[0].reasons)
    assert "and it has 0 port(s) free (over by 9) against 8 required — over capacity" in t, t


# =============================================================================
# The release gate: qa/expected/5.6.0.json explains these changes, and only these
# =============================================================================

def _gate():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    if not (root / "qa" / "compare.py").exists():
        # The package leaves qa/ out: the gate's own rules are tested in the
        # source tree, not in an installed copy.
        pytest.skip("qa/ is not shipped in the package")
    spec = importlib.util.spec_from_file_location("qa_compare", root / "qa" / "compare.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.load_rules(root / "qa" / "expected" / "5.6.0.json")


def _row(ner="N1", status="served", dist=50.0, method="route", far=None, sc=True,
         kind="space", ports=2.0, **cause):
    """A row as ``qa/scenario.assignment_row`` records it; ``cause`` sets the
    fields the causal rules read (rc2): traced_m, lead_in_m, ties, alts..."""
    r = {"ner": ner, "status": status, "ports": ports, "distance_m": dist, "name": "X",
         "method": method, "far_m": far, "scale_confirmed": sc, "limit_m": 90.0,
         "kind": kind, "ner_key": ner, "ner_critical": None, "pinned": False,
         "geo_m": dist, "traced_m": None, "lead_in_m": None, "nearest": None, "alts": {},
         "of_space": None, "ties": None, "alert": None, "space_critical": None,
         "sugg": None, "sugg_m": None, "sugg_within": None}
    r.update(cause)
    return r


LOAD = {"N1": {"label": "N1", "critical": False, "total_ports": None, "assigned_ports": 0.0},
        "N2": {"label": "N2", "critical": True, "total_ports": None, "assigned_ports": 0.0},
        "C": {"label": "C", "critical": True, "total_ports": None, "assigned_ports": 0.0}}


def _proj(rows, summary=None, live=3, load=None):
    return {"id": 1, "name": "P", "venue": "V", "ners": {"live": live, "names": []},
            "summary": summary or {}, "assignments": rows, "sheets": {},
            "ner_load": load if load is not None else LOAD}


def _explain(before_rows, after_rows, sb=None, sa=None, live_a=3):
    C, rules = _gate()
    b, a = [_proj(before_rows, sb)], [_proj(after_rows, sa, live_a)]
    return C.explain(C.compare_runs(b, a), rules, b, a)


R70 = dict(traced_m=70.0, lead_in_m=10.0)


@pytest.mark.parametrize("before, after", [
    # item 5 (criticality enforced): no NER, a non-critical NER's status before
    (_row(None, "served", 10.0, "straight_line", nearest="N1"),
     _row(None, "no_critical_ner_in_limit", 120.0, "straight_line", nearest="C",
          alts={"N1": 10.0, "C": 120.0})),
    # item 1: far corner along the route, NER kept, same route
    (_row("N1", "served", 80.0, far=64.0, **R70),
     _row("N1", "out_of_limit", 92.0, far=92.0, **R70)),
    # item 1: the NER changes - the old one is now over the limit
    (_row("N1", "served", 80.0, far=64.0, alts={"N2": 82.0}, **R70),
     _row("N2", "near_limit", 85.5, far=85.5, alts={"N1": 95.0}, **R70)),
    # item 2: unconfirmed scale on a traced route
    (_row("N1", "served", 30.0, sc=False), _row(None, "scale_not_confirmed", 30.0, sc=False,
                                               nearest="N1")),
    # item 4: the shorter of two tied trays
    (_row("N1", "near_limit", 69.0), _row("N1", "served", 23.0, ties=2)),
    # 5.5.1 F4: the route counts at the 5 m mark
    (_row("N1", "served", 30.0, "straight_line"),
     _row("N1", "served", 34.0, "route", traced_m=5.0, lead_in_m=3.0)),
    # 5.5's "served with no NER" critical space, now reached by a critical NER
    (_row(None, "served", 13.2), _row("N2", "served", 17.3, ner_critical=True)),
])
def test_gate_rules_explain_each_item(before, after):
    explained, unexplained = _explain({"S": before}, {"S": after},
                                      {"served": 1}, {"served": int(after["status"] in
                                                                  ("served", "near_limit"))})
    assert explained and not unexplained, unexplained


@pytest.mark.parametrize("before, after", [
    # a straight-line distance moves
    (_row("N1", "served", 30.0, "straight_line"), _row("N1", "served", 33.0, "straight_line")),
    # a straight-line NER changes with nothing else moving
    (_row("N1", "served", 30.0, "straight_line"), _row("N2", "served", 30.0, "straight_line")),
    # a straight-line status
    (_row("N1", "served", 30.0, "straight_line"), _row("N1", "out_of_limit", 30.0,
                                                        "straight_line")),
    # a route without polygon gets longer by metres, same NER
    (_row("N1", "served", 30.0), _row("N1", "served", 40.0)),
    # ports
    (_row(ports=2.0), _row(ports=3.0)),
    # a served space with a NER loses it for no stated reason
    (_row("N1", "served", 10.0, "straight_line"), _row(None, "no_critical_ner_in_limit", 10.0,
                                                       "straight_line")),
    # rc2 (T1-2): a shorter route with no tie (lead-ins measured differently, M3)
    (_row("N1", "near_limit", 69.0, traced_m=60.0, lead_in_m=9.0),
     _row("N1", "served", 64.5, traced_m=60.0, lead_in_m=4.5)),
    # a method flip far from the 5 m mark (routes ignored, M1)
    (_row("N1", "served", 30.0, traced_m=25.0, lead_in_m=5.0), _row("N1", "served", 26.0,
                                                                   "straight_line")),
    # 5.5's critical space taken by a NER that is not critical-rated (M17)
    (_row(None, "served", 13.2), _row("N1", "served", 10.0, ner_critical=False)),
    # item 1 with a NER change while the old NER still serves within the limit (M9)
    (_row("N1", "served", 60.0, far=50.0, alts={"N2": 70.0}, **R70),
     _row("N2", "served", 75.0, far=75.0, alts={"N1": 62.0}, **R70)),
    # item 2 with a NER that takes the space on an unconfirmed route (M16)
    (_row("N1", "served", 30.0, sc=False), _row("N1", "scale_not_confirmed", 30.0, sc=False)),
    # a criticality alert on a NER a person chose
    (_row("N1", "served", 30.0), _row("N1", "served", 30.0, alert="no_critical_ner_in_limit",
                                      space_critical=True, ner_critical=False, pinned=True)),
    # an alert of the wrong kind for its suggestion
    (_row("N1", "served", 30.0), _row("N1", "served", 30.0, alert="no_critical_ner_in_limit",
                                      space_critical=True, ner_critical=False, sugg="C",
                                      sugg_m=40.0, sugg_within=True)),
])
def test_gate_rules_still_fail_on_anything_else(before, after):
    _explained, unexplained = _explain({"S": before}, {"S": after})
    assert unexplained


def test_gate_a_summary_count_needs_a_space_that_moved():
    rows = {"S": _row()}
    _e, unexplained = _explain(rows, rows, {"served": 5}, {"served": 4})
    assert [d["kind"] for d in unexplained] == ["summary.served"]
    _e, unexplained = _explain(rows, rows, live_a=4)
    assert [d["kind"] for d in unexplained] == ["ners.live"]


def test_gate_a_summary_count_moves_by_what_the_rows_add_up_to():
    """rc2 (T1-2, M19): a space explained on its own is not enough - the
    count has to move by exactly what the rows add up to."""
    b = {"S": _row("N1", "served", 30.0, sc=False)}
    a = {"S": _row(None, "scale_not_confirmed", 30.0, sc=False, nearest="N1")}
    _e, unexplained = _explain(b, a, {"served": 1}, {"served": 0})
    assert not unexplained
    _e, unexplained = _explain(b, a, {"served": 3}, {"served": 1})     # -2, rows -1
    assert [d["kind"] for d in unexplained] == ["summary.served"]


def test_gate_a_rule_on_a_field_the_old_measurement_lacks_does_not_match():
    """A baseline measured before 5.6.0 has no ``method`` or ``far_m``: no
    rule with a condition on them can explain its differences."""
    old = {"ner": "N1", "status": "served", "ports": 2.0, "distance_m": 80.0, "name": "X"}
    _e, unexplained = _explain({"S": old}, {"S": _row("N1", "out_of_limit", 92.0, far=92.0)})
    assert sorted(d["kind"] for d in unexplained) == ["space.distance", "space.status"]


def test_gate_runs_on_a_baseline_measured_without_the_new_fields():
    """rc2: the real projects' 5.5.0 baseline was measured by rc1's qa/ (no
    cause fields, no alert, no new summary count). Nothing moved but the
    criticality alert: every difference is explained."""
    old = {"ner": "N1", "status": "served", "ports": 2.0, "distance_m": 30.0, "name": "X",
           "method": "straight_line", "far_m": None, "scale_confirmed": True, "limit_m": 90.0,
           "kind": "space"}
    new = _row("N1", "served", 30.0, "straight_line", alert="critical_ner_in_limit",
               space_critical=True, ner_critical=False, sugg="C", sugg_m=40.0, sugg_within=True)
    C, rules = _gate()
    b = [dict(_proj({"S": old}, {"served": 1}), sheets={"Summary": 40})]
    b[0].pop("ner_load")
    a = [dict(_proj({"S": new}, {"served": 1, "criticality_alerts": 1}), sheets={"Summary": 42})]
    diffs = C.compare_runs(b, a)
    assert sorted(d["kind"] for d in diffs) == ["sheet.rows", "space.alert",
                                                "summary.criticality_alerts"]
    explained, unexplained = C.explain(diffs, rules, b, a)
    assert not unexplained and len(explained) == 3
