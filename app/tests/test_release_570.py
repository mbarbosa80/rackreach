"""5.7.0 - the route rules decided by the owner on 16 Nov 2026 and the
route items of the 5.6.0 "Next version" list.

A1   two trays tied for the nearest, the shorter route does not count: the
     space takes the shortest connection that counts (as across levels),
     never a straight line while a counting route reaches the NER.
B    shaft and riser labels in a tie are ordered as a person reads them
     (R2 before R10); the network does not depend on the order the trays and
     links were registered in (tester R2-7); a critical space whose routed
     floor NERs are all non-critical keeps the critical-rated floor NERs
     within the limit in the "same floor first" gate (enforced mode).
"""
from __future__ import annotations

import itertools

import pytest

from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceMethod, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteSegment, VerticalLink, build_network
from app.domain.units import Scale

DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
P = {1: DECI, 2: DECI}


def _n(key, x, page=2, crit=True, y=0.0):
    return NER(key=key, label=key,
               kind=ElementKind.NER_CRITICAL if crit else ElementKind.NER_NONCRITICAL,
               point=Point(x, y, page), critical=crit)


def _seg(x0, y0, x1, y1, page=2, label=""):
    return RouteSegment(Point(x0, y0, page), Point(x1, y1, page), page, label=label)


def _run(net, ners, *, crit=None, enforce=False, pages=P):
    area = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2))
    res = compute_coverage(areas=[(area, 2.0, DemandType.GENERAL, crit, True)],
                           ners=ners, scale_for_page=pages, rules=DistanceRules(),
                           network=net, enforce_criticality=enforce)
    return {a.space_key: a for a in res.assignments}["S"]


def _t(x):
    return " | ".join(x.reasons + (x.evaluation.assumptions if x.evaluation else []))


# ---- A1 ---------------------------------------------------------------------

def _tied_trays(order):
    """The space is 3 m from tray A (above) and from tray B (below). Tray A
    reaches the NER at (-20, 30) after 2 m: 5 m in all, 2 m traced - it does
    not count. Tray B goes round: 6 + 6 + 4 = 16 m traced, 19 m in all - it
    counts. The straight line is about 3.6 m."""
    a = [_seg(0, 30, -20, 30, label="A")]
    b = [_seg(0, -30, -60, -30, label="B"), _seg(-60, -30, -60, 30, label="B2"),
         _seg(-60, 30, -20, 30, label="B3")]
    return a + b if order == "AB" else b + a


@pytest.mark.parametrize("order", ["AB", "BA"])
@pytest.mark.parametrize("has_shaft", [False, True])
def test_a1_the_shortest_connection_that_counts_wins_a_tie(order, has_shaft):
    shafts = [VerticalLink("Z", Point(5000, 0, 1), Point(5000, 0, 2), 4.0)] if has_shaft else []
    net = build_network(_tied_trays(order), shafts, P)
    x = _run(net, [_n("HERE", -20, y=30)])
    assert x.ner_key == "HERE"
    assert x.evaluation.method is DistanceMethod.ROUTE, _t(x)
    assert x.evaluation.effective_distance_m == pytest.approx(19.0, abs=1e-6), _t(x)
    assert "by a shorter connection" in _t(x) and "does not count" in _t(x), _t(x)


def test_a1_no_counting_connection_is_still_the_straight_line():
    """Only tray A, which does not count: the straight line, as before."""
    net = build_network([_seg(0, 30, -20, 30, label="A")], [], P)
    x = _run(net, [_n("HERE", -20, y=30)])
    assert x.evaluation.method is not DistanceMethod.ROUTE
    assert x.evaluation.effective_distance_m == pytest.approx((20 ** 2 + 30 ** 2) ** 0.5 / 10, abs=1e-6)


def test_a1_a_counting_shortest_route_is_unchanged():
    net = build_network([_seg(0, 30, -200, 30, label="A")], [], P)
    x = _run(net, [_n("HERE", -200, y=30)])
    assert x.evaluation.effective_distance_m == pytest.approx(23.0, abs=1e-6)
    assert "by a shorter connection" not in _t(x)


# ---- B: shaft and riser labels in natural order ------------------------------

@pytest.mark.parametrize("order", ["fwd", "rev"])
def test_b_equal_links_are_ordered_r2_before_r10(order):
    links = [VerticalLink("R10", Point(100, 0, 1), Point(100, 0, 2), 8.0),
             VerticalLink("R2", Point(-100, 0, 1), Point(-100, 0, 2), 8.0)]
    if order == "rev":
        links.reverse()
    net = build_network([], links, P)
    opts = net.route_options(Point(0, 0, 2), Point(0, 0, 1), DECI)
    assert opts[0]["length_m"] == pytest.approx(opts[1]["length_m"])
    assert opts[0]["vertical_links"] == ["R2"], opts[0]


# ---- B: the order of registration decides nothing (tester R2-7) ---------------

def test_b_registration_order_never_moves_a_distance():
    """Two tray ends on one snap node, a T-junction and two links: every
    order of registration gives the same options."""
    segs = [_seg(0, 30, 100, 30, label="A"), _seg(100.9, 30.9, 100.9, 300, label="B"),
            _seg(-50, 30, 0, 30, label="C"), _seg(100.9, 300, 400, 300, label="D")]
    links = [VerticalLink("S1", Point(400, 300, 2), Point(400, 300, 1), 6.0),
             VerticalLink("S2", Point(399.5, 300.5, 2), Point(0, 0, 1), 6.0)]
    seen = set()
    for perm in itertools.permutations(segs):
        for lk in (links, links[::-1]):
            net = build_network(list(perm), list(lk), P)
            opts = net.route_options(Point(0, 0, 2), Point(400, 300, 1), DECI)
            seen.add(tuple((round(o["length_m"], 9), round(o["traced_m"], 9),
                            tuple(o["vertical_links"])) for o in opts))
    assert len(seen) == 1, seen


# ---- B: "same floor first" keeps a critical floor NER (enforced mode) --------

def test_b_critical_floor_ner_in_a_straight_line_beats_a_cross_level_one():
    """Critical space, criticality enforced. On its floor: NC (non-critical)
    23 m by a counting tray, C (critical) 30 m in a straight line, no tray to
    it. Upstairs: UP (critical) 13 m through a shaft. "Same floor first"
    looked only at the routed floor NERs (NC, refused for criticality), so
    UP took the space while C served on the floor."""
    segs = [_seg(0, 30, -200, 30, label="A")]
    shaft = [VerticalLink("SH", Point(0, -50, 2), Point(0, -50, 1), 8.0)]
    net = build_network(segs, shaft, P)
    ners = [_n("NC", -200, crit=False, y=30), _n("C", 0, y=300), _n("UP", 0, page=1, y=-50)]
    x = _run(net, ners, crit=True, enforce=True)
    assert x.ner_key == "C", (x.ner_key, _t(x))
    assert x.status in (Status.SERVED, Status.NEAR_LIMIT), (x.status, _t(x))
    assert "UP" in _t(x), _t(x)


def test_b_not_enforced_is_unchanged():
    """The same project without enforcement (what every screen uses): the
    nearest NER the rules give, criticality only alerts."""
    segs = [_seg(0, 30, -200, 30, label="A")]
    shaft = [VerticalLink("SH", Point(0, -50, 2), Point(0, -50, 1), 8.0)]
    net = build_network(segs, shaft, P)
    ners = [_n("NC", -200, crit=False, y=30), _n("C", 0, y=300), _n("UP", 0, page=1, y=-50)]
    x = _run(net, ners, crit=True, enforce=False)
    assert x.ner_key == "NC", (x.ner_key, _t(x))


# ---- R2-2 (M31): the suggestion is the NEAREST critical NER -------------------

@pytest.mark.parametrize("order", ["near_first", "far_first"])
def test_r2_2_the_alert_suggests_the_nearest_critical_ner(order):
    """Critical space, nearest NER non-critical: of two critical NERs the
    alert names the nearer one, whatever the order they come in."""
    crit = [_n("C_FAR", -800), _n("C_NEAR", 400)]
    if order == "far_first":
        crit.reverse()
    x = _run(None, [_n("NC", 100, crit=False)] + crit, crit=True)
    assert x.ner_key == "NC" and x.criticality_alert
    assert (x.critical_suggestion or {}).get("ner_key") == "C_NEAR", x.critical_suggestion


# ---- R2-4: the joint cache keeps the riser-free answer apart ------------------

def test_r2_4_the_cache_keeps_level_only_and_through_risers_apart():
    """The same point asked with and without risers: the joints differ (a
    riser foot is a joint only with risers) and neither answer is served for
    the other from the cache."""
    segs = [_seg(0, 30, -200, 30, label="T")]
    links = [VerticalLink("R1", Point(0, -20, 2), Point(0, -20, 1), 4.0)]
    net = build_network(segs, links, P)
    here = Point(0, 0, 2)
    tol = 600.0
    with_feet = [j["via"][0][0] for j in net._joints(here, tol, False)]
    level = [j["via"][0][0] for j in net._joints(here, tol, True)]
    again = [j["via"][0][0] for j in net._joints(here, tol, False)]
    assert with_feet == [(2, 0.0, -20.0)] and level == [(2, 0.0, 30.0)]   # foot vs tray
    assert with_feet == again
    opts_level = net.route_options(here, Point(-200, 30, 2), DECI, level_only=True)
    opts_all = net.route_options(here, Point(0, -20, 1), DECI)
    assert all(not o["vertical_links"] for o in opts_level)
    assert any(o["vertical_links"] == ["R1"] for o in opts_all)
    # asked again in the other order, the same answers
    assert net.route_options(here, Point(-200, 30, 2), DECI, level_only=True) == opts_level
