"""5.5.1 - the five route defects the independent tester found on 5.3.0-rc7.

R7-F1  the 5 m rule was judged on one path only: a long shaft drawn over a
       short one at the same feet, or a shaft foot nearer the space than a
       short shaft's, sent the space "by route" the long way (47.8 m) and the
       same-floor NER 14.9 m away was no longer compared.
R7-F2  a NER on the space's own floor was measured through another floor
       when two risers and a tray joined them: out of limit at 120 m against
       30 m in a straight line (the tester's case: 108 m against 14.9 m).
R7-F3  a shaft route that does not count was dropped without a word; the row
       then said "no vertical route registered".
R7-F4  5.01 m traced did not count: the 2-unit snap grid shortened the traced
       part (the working threshold through a tray was about 5.1 m).
R7-F5  a vertical link with both feet on one sheet was accepted and acted as a
       teleport.

Every test here failed on 5.5.0-rc4 (NOTES-551.md has the failure lines),
except four guards that pass on both: the 5.01 m tray at the two offsets
the snap grid happens not to shorten, a same-floor route with a riser far
away, and the old sentence where no shaft exists.
"""

from __future__ import annotations

import pytest

from app.domain.coverage import NER, compute_coverage, route_counts
from app.domain.distance import DemandType, DistanceMethod, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteSegment, VerticalLink, build_network
from app.domain.units import Scale
from tests.test_ner_register import client, samples  # noqa: F401
from tests.test_tester_findings_530 import _project_with_drawing

#: 1 unit = 0.1 m: the 15 m join reach is 150 units, the snap grid 0.2 m.
DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
RULES = DistanceRules()


def _n(key, x, page, y=0.0):
    return NER(key=key, label=key, kind=ElementKind.NER_CRITICAL,
               point=Point(x, y, page), critical=True, total_ports=None)


def _a(key, x, y, page):
    return Area(key=key, name=key, page=page, label_point=Point(x, y, page))


def _seg(a, b, page):
    return RouteSegment(Point(*a, page), Point(*b, page), page)


def _shaft(key, x, length_m, lower=1, upper=2, y=0.0):
    return VerticalLink(key, Point(x, y, lower), Point(x, y, upper), length_m)


def _run(net, ners, space=None):
    space = space or _a("S", 0, 0, 2)
    pages = {p: DECI for p in (1, 2, 3)}
    res = compute_coverage(areas=[(space, 2.0, DemandType.GENERAL, None, True)],
                           ners=ners, scale_for_page=pages, rules=RULES, network=net)
    return res.assignments[0], res


def _text(x):
    return " | ".join(x.reasons + (x.evaluation.assumptions if x.evaluation else []))


# The rc7 fixture (tests/test_tester_findings_rc6_530.py::_n11): the space at
# (0, 0) on level 2, UP straight above it on level 1, HERE 14.9 m away on the
# space's own floor.
def _up_here():
    return _n("UP", 0, 1), _n("HERE", -149, 2)


# ---- R7-F1 -------------------------------------------------------------------

@pytest.mark.parametrize("order", ["short_first", "long_first"])
def test_f1_a_long_shaft_over_a_short_one_never_wins_by_its_length(order):
    """Two shafts on the same feet, 2 m from the space and from UP: 4 m (does
    not count: 4 m traced, 4 m lead-in) and 43.8 m. The graph kept whichever
    was registered last, so the space went by route to UP at 47.8 m and HERE
    was set aside."""
    short, long_ = _shaft("SHORT", 20, 4.0), _shaft("LONG", 20, 43.8)
    links = [short, long_] if order == "short_first" else [long_, short]
    net = build_network([], links, {1: DECI, 2: DECI})
    up, here = _up_here()
    x, _ = _run(net, [up, here])
    assert x.ner_key == "HERE", (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(14.9, abs=0.05)
    # the choice is explained on the row
    assert "UP" in _text(x) and "does not count" in _text(x), x.reasons


def test_f1_a_shorter_connection_that_does_not_count_is_named_not_obeyed():
    """A long shaft whose foot is nearer the space (1 m) than a short shaft's
    (2 m): only the nearest foot was tried. Since rc2 (tester N1) the rule is
    judged on the shortest connection that counts: the 47.8 m one through the
    long shaft counts, so UP is measured through it, and the shorter 8 m
    connection through the short shaft - mostly lead-in - is named in the
    notes, not obeyed. (With HERE on the space's floor within the limit, HERE
    serves: "same floor first", rc3 - tests/test_same_floor_first_551.py.)"""
    net = build_network([], [_shaft("SHORT", 20, 4.0), _shaft("LONG", 10, 45.8)],
                        {1: DECI, 2: DECI})
    up, _ = _up_here()
    x, _ = _run(net, [up])
    assert x.ner_key == "UP", (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(47.8, abs=0.01)
    assert "SHORT" in _text(x) and "does not count" in _text(x), _text(x)


def test_f1_a_counting_short_shaft_is_measured_not_the_long_way():
    """The same two feet, the short shaft 8 m long (it counts): UP is measured
    through it, 12 m - not 47.8 m through the long one."""
    net = build_network([], [_shaft("SHORT", 20, 8.0), _shaft("LONG", 10, 45.8)],
                        {1: DECI, 2: DECI})
    up, _ = _up_here()
    x, _ = _run(net, [up])
    assert x.ner_key == "UP", (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(12.0, abs=0.01)
    assert x.evaluation.method is DistanceMethod.ROUTE


def test_f1_a_non_counting_route_never_removes_a_straight_line_candidate():
    """Whatever the shafts, a NER on another level reached only by a route that
    does not count leaves the same-floor candidates exactly as they were."""
    up, here = _up_here()
    alone, _ = _run(None, [here])
    for links in ([_shaft("A", 20, 4.0)], [_shaft("A", 20, 4.0), _shaft("B", 20, 43.8)],
                  [_shaft("A", 120, 4.0)]):
        x, _ = _run(build_network([], links, {1: DECI, 2: DECI}), [up, here])
        assert x.ner_key == alone.ner_key == "HERE", ([l.key for l in links], x.reasons)
        assert x.evaluation.effective_distance_m == alone.evaluation.effective_distance_m


# ---- R7-F2 -------------------------------------------------------------------

def _detour(extra=()):
    """Two 4 m risers, one 1 m from the space, one 0.9 m from FAR (30 m away on
    the space's floor), joined on level 1 by a 110.1 m tray that goes round:
    through level 1 FAR is 120 m away, in a straight line 30 m."""
    tray = [_seg((10, 0), (10, 400), 1), _seg((10, 400), (-291, 400), 1),
            _seg((-291, 400), (-291, 0), 1), *extra]
    return build_network(tray, [_shaft("R1", 10, 4.0), _shaft("R2", -291, 4.0)],
                         {1: DECI, 2: DECI})


def test_f2_a_same_floor_ner_is_not_measured_through_another_floor():
    net = _detour()
    r = net.route_length_m(Point(0, 0, 2), Point(-300, 0, 2), DECI)
    assert r and r["crosses_levels"] and r["length_m"] > 90      # the detour is real
    x, _ = _run(net, [_n("FAR", -300, 2)])
    assert x.ner_key == "FAR" and x.status is Status.SERVED, (x.status, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(30.0, abs=0.05)
    assert x.evaluation.method is DistanceMethod.STRAIGHT_LINE
    assert "another level" in _text(x), _text(x)


def test_f2_a_same_floor_tray_beats_a_detour_through_another_floor():
    """With a 30 m tray on the space's own floor, FAR is measured along it,
    not through level 1."""
    net = _detour([_seg((0, -5), (-300, -5), 2)])
    x, _ = _run(net, [_n("FAR", -300, 2)])
    assert x.ner_key == "FAR" and x.status is Status.SERVED, (x.status, x.reasons)
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.evaluation.effective_distance_m == pytest.approx(31.0, abs=0.05)


def test_f2_a_shorter_way_round_another_level_still_stands():
    """The rule is "never worse than on its own floor", not "never another
    floor": a 2 m riser pair under a direct tray upstairs, against a long
    tray on the space's own floor, is measured upstairs - and says so."""
    net = build_network(
        [_seg((10, 0), (-291, 0), 1),                                   # 30.1 m upstairs
         _seg((0, -5), (0, -600), 2), _seg((0, -600), (-300, -600), 2),
         _seg((-300, -600), (-300, -5), 2)],                            # 149 m downstairs
        [_shaft("R1", 10, 2.0), _shaft("R2", -291, 2.0)], {1: DECI, 2: DECI})
    x, _ = _run(net, [_n("FAR", -300, 2)])
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.evaluation.effective_distance_m == pytest.approx(36.0, abs=0.05)
    assert "through another level" in _text(x), _text(x)


def test_f2_a_route_on_one_floor_is_unchanged_by_risers_elsewhere():
    """No riser anywhere near: a same-floor route measures as it always did."""
    seg = [_seg((0, -5), (-149, -5), 2)]
    plain = build_network(seg, [], {1: DECI, 2: DECI})
    with_far = build_network(seg, [_shaft("FAR", 3000, 4.0)], {1: DECI, 2: DECI})
    _, here = _up_here()
    a, _ = _run(plain, [here])
    b, _ = _run(with_far, [here])
    assert a.evaluation.effective_distance_m == b.evaluation.effective_distance_m


# ---- R7-F3 -------------------------------------------------------------------

def test_f3_a_shaft_that_does_not_count_is_named_on_the_row():
    """The rc7 case: a 4 m shaft with 12 m + 12 m of lead-in. HERE serves; the
    row says a vertical route reaches UP and why it does not count."""
    net = build_network([], [_shaft("S1", 120, 4.0)], {1: DECI, 2: DECI})
    up, here = _up_here()
    x, _ = _run(net, [up, here])
    assert x.ner_key == "HERE"
    t = " | ".join(x.reasons)
    assert "UP" in t and "does not count" in t and "4.00 m" in t and "24.00 m" in t, t


def test_f3_no_ner_measurable_never_says_no_vertical_route_when_one_is():
    net = build_network([], [_shaft("S1", 120, 4.0)], {1: DECI, 2: DECI})
    up, _ = _up_here()
    x, res = _run(net, [up])
    assert x.ner_key is None and x.status is Status.NOT_ANALYSABLE
    t = " | ".join(x.reasons)
    assert "no vertical route registered" not in t, t
    assert "does not count" in t and "UP" in t, t
    detail = " | ".join(e["detail"] for e in res.exceptions)
    assert "no registered shaft" not in detail, detail


def test_f3_a_room_chosen_on_another_level_says_its_shaft_does_not_count():
    net = build_network([], [_shaft("S1", 120, 4.0)], {1: DECI, 2: DECI})
    up, here = _up_here()
    res = compute_coverage(areas=[(_a("S", 0, 0, 2), 2.0, DemandType.GENERAL, None, True)],
                           ners=[up, here], scale_for_page={1: DECI, 2: DECI}, rules=RULES,
                           network=net, pinned={"S": "UP"})
    x = res.assignments[0]
    assert x.ner_key == "UP" and x.status is Status.CROSS_LEVEL_NOT_MEASURED
    t = " | ".join(x.reasons)
    assert "no shaft, riser or vertical route registered" not in t, t
    assert "does not count" in t, t
    detail = " | ".join(e["detail"] for e in res.exceptions)
    assert "register the shaft" not in detail and "does not count" in detail, detail


def test_f3_without_any_shaft_the_old_sentence_stays():
    up, _ = _up_here()
    x, _ = _run(build_network([_seg((0, 50), (10, 50), 1)], [], {1: DECI, 2: DECI}), [up])
    assert "no vertical route registered" in " | ".join(x.reasons)


# ---- R7-F4 -------------------------------------------------------------------

@pytest.mark.parametrize("x0", [0.0, 0.5, 1.0, 1.3, 1.9, 3.1])
def test_f4_five_metres_traced_count_wherever_the_tray_starts(x0):
    """A 5.01 m tray from the NER to the space, at any offset on the 2-unit
    snap grid, is 5.01 m traced and counts."""
    net = build_network([_seg((x0, 0), (x0 + 50.1, 0), 1)], [], {1: DECI})
    r = net.route_length_m(Point(x0 + 50.1, 0, 1), Point(x0, 0, 1), DECI)
    assert r is not None
    assert r["traced_m"] == pytest.approx(5.01, abs=0.005), r
    assert route_counts(r), r


def test_f4_exactly_five_metres_counts_and_just_under_does_not():
    assert route_counts({"traced_m": 5.0, "lead_in_m": 0.0})
    assert route_counts({"traced_m": 4.9999999, "lead_in_m": 0.0})
    assert not route_counts({"traced_m": 4.99, "lead_in_m": 0.0})
    net = build_network([_seg((1, 0), (51, 0), 1)], [], {1: DECI})     # 5.00 m drawn
    r = net.route_length_m(Point(51, 0, 1), Point(1, 0, 1), DECI)
    assert route_counts(r), r
    net = build_network([_seg((1, 0), (49.9, 0), 1)], [], {1: DECI})   # 4.89 m drawn
    assert not route_counts(net.route_length_m(Point(49.9, 0, 1), Point(1, 0, 1), DECI))


def test_f4_the_space_goes_by_route_at_5_01_m():
    net = build_network([_seg((1, 0), (51.1, 0), 1)], [], {1: DECI})
    x, _ = _run(net, [_n("N", 1, 1)], space=_a("S", 51.1, 0, 1))
    assert x.evaluation.method is DistanceMethod.ROUTE, _text(x)
    assert x.evaluation.effective_distance_m == pytest.approx(5.01, abs=0.01)


# ---- R7-F5 -------------------------------------------------------------------

def test_f5_a_link_with_both_feet_on_one_sheet_is_refused(client, samples):
    pid = _project_with_drawing(client, samples, "551 F5", "RF5")
    body = {"key": "T1", "lower_page": 1, "lower_x": 10, "lower_y": 10,
            "upper_page": 1, "upper_x": 500, "upper_y": 10, "length_m": 1}
    r = client.post(f"/api/projects/{pid}/verticals", json=body)
    assert r.status_code == 422, r.text
    assert "two different sheets" in r.text, r.text
    assert client.get(f"/api/projects/{pid}/verticals").json() == []


def test_f5_a_same_sheet_link_already_stored_is_ignored_and_noted():
    """One stored by an earlier version: a 1 m 'shaft' between two points
    49 m apart on one sheet. It is not used, and the space beside it says so."""
    net = build_network([], [VerticalLink("T1", Point(0, 0, 1), Point(490, 0, 1), 1.0)],
                        {1: DECI})
    assert [k for k, *_ in net.ignored_verticals] == ["T1"]
    assert net.route_length_m(Point(0, 0, 1), Point(490, 0, 1), DECI) is None
    x, res = _run(net, [_n("N", 490, 1)], space=_a("S", 0, 0, 1))
    assert res.notes and "T1" in res.notes[0]
    assert x.evaluation.method is DistanceMethod.STRAIGHT_LINE
    assert x.evaluation.effective_distance_m == pytest.approx(49.0, abs=0.01)
    assert "T1" in " | ".join(x.reasons) and "ignored" in " | ".join(x.reasons), x.reasons


def test_f5_the_stored_link_is_listed_as_ignored_not_deleted(client, samples):
    from app.db import SessionLocal
    from app.models import VerticalLinkRow
    pid = _project_with_drawing(client, samples, "551 F5b", "RF6")
    with SessionLocal() as db:
        db.add(VerticalLinkRow(project_id=pid, key="T1", lower_page=1, lower_x=10, lower_y=10,
                               upper_page=1, upper_x=500, upper_y=10, length_m=1.0))
        db.commit()
    rows = client.get(f"/api/projects/{pid}/verticals").json()
    assert len(rows) == 1 and rows[0]["ignored"], rows
    assert "same sheet" in rows[0]["ignored"]
