"""Independent tester on 5.5.1-rc4 (a5482e6..3ddee11): the R3-1..R3-10 fixes.

Each test asserts the EXPECTED behaviour, so a failing test is a finding
(R4-n in /home/claude/w/tester-554/FINDINGS.md). Tests named ``test_guard_*``
pass and document what was checked and found fine.

Copied into the repository for 5.5.1-rc5 as ``test_tester_rc4_551.py``
(tester's ``test_tester_rc4_554.py``). Adapted only where the owner's
decisions differ, and each adaptation says so:
- R4-3: the full floor NER is taken (same floor first), so the test asserts
  HERE and the true sentence, with UP in the alternatives;
- R4-4 and R4-5 were deferred to 5.6.0 (they change projects without
  shafts) as strict xfails; 5.6.0 fixes both and they are normal tests now.
- R4-1 without shafts: the setup line that measured HERE alone on a project
  without shafts at 85.00 m now expects 135.04 m (5.6.0: the far corner is
  measured along the route on every project).

Fixtures as tests/test_tester_rc3_551.py: 1 unit = 0.1 m (DECI), space S at
(0, 0) on page 2, UP on page 1 (or 3), HERE on page 2.
"""

from __future__ import annotations

import time

import pytest

from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceMethod, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteSegment, VerticalLink, build_network
from app.domain.units import Scale

DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
DECI_U = Scale(metres_per_unit=0.1, source="auto", label="1u=0.1m", confirmed=False)
CENTI = Scale(metres_per_unit=0.01, source="manual", label="1u=0.01m", confirmed=True)
P = {1: DECI, 2: DECI}
P3 = {1: DECI, 2: DECI, 3: DECI}


def _n(key, x, page, crit=True, ports=None, used=0, y=0.0):
    return NER(key=key, label=key,
               kind=ElementKind.NER_CRITICAL if crit else ElementKind.NER_NONCRITICAL,
               point=Point(x, y, page), critical=crit, total_ports=ports, used_ports=used)


def _shaft(key, x, length_m, lower=1, upper=2, y=0.0):
    return VerticalLink(key, Point(x, y, lower), Point(x, y, upper), length_m)


def _seg(x0, y0, x1, y1, page, label=""):
    return RouteSegment(Point(x0, y0, page), Point(x1, y1, page), page, label=label)


def _run_many(net, ners, areas, pages=P, demand=DemandType.GENERAL, crit=None,
              pinned=None, respect_capacity=True):
    res = compute_coverage(areas=[(a, p, demand, crit, True) for a, p in areas],
                           ners=ners, scale_for_page=pages, rules=DistanceRules(),
                           network=net, pinned=pinned, respect_capacity=respect_capacity)
    return {a.space_key: a for a in res.assignments}


def _run(net, ners, pages=P, demand=DemandType.GENERAL, crit=None, pinned=None,
         area=None, ports=2.0):
    area = area or Area(key="S", name="S", page=2, label_point=Point(0, 0, 2))
    return _run_many(net, ners, [(area, ports)], pages, demand, crit, pinned)["S"]


def _t(x):
    return " | ".join(x.reasons + (x.evaluation.assumptions if x.evaluation else []))


# =============================================================================
# R3-1 variants: capacity in the floor gate
# =============================================================================

def test_guard_r3_1_unknown_capacity_upstairs_full_floor():
    """HERE full (known), UP unknown capacity: UP serves."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2, ports=10, used=10)])
    assert x.ner_key == "UP" and x.status is Status.SERVED, x.reasons


def test_guard_r3_1_second_floor_ner_with_ports_serves_before_upstairs():
    """HERE full at 14.9 m, HERE2 free at 40 m, UP 12 m: HERE2 (same floor)."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=48), _n("HERE", -149, 2, ports=10, used=10),
                   _n("HERE2", 400, 2, ports=10)])
    assert x.ner_key == "HERE2" and x.status is Status.SERVED, x.reasons


def test_guard_r3_1_capacity_used_by_an_earlier_space_in_the_run():
    """Two spaces of 6 ports; HERE has 10 free. The first (key order) takes
    HERE, the second finds 4 free and goes upstairs, with the real figure."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    a1 = Area(key="A", name="A", page=2, label_point=Point(0, 0, 2))
    a2 = Area(key="B", name="B", page=2, label_point=Point(0, 0, 2))
    got = _run_many(net, [_n("UP", 0, 1, ports=48), _n("HERE", -149, 2, ports=10)],
                    [(a1, 6.0), (a2, 6.0)])
    assert got["A"].ner_key == "HERE"
    assert got["B"].ner_key == "UP", got["B"].reasons
    assert "out of ports (4 free against 6 required)" in _t(got["B"]), _t(got["B"])


def test_guard_r3_1_respect_capacity_off_full_floor_still_serves():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    a = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2))
    x = _run_many(net, [_n("UP", 0, 1, ports=48), _n("HERE", -149, 2, ports=10, used=10)],
                  [(a, 2.0)], respect_capacity=False)["S"]
    assert x.ner_key == "HERE", x.reasons


def test_r4_both_full_the_fallback_says_nearest_but_takes_the_farther_ner_upstairs():
    """HERE (14.9 m, own floor) and UP (50 m by a 48 m shaft) are both full.
    The capacity fallback takes UP and writes 'assigned to the nearest one,
    UP' - HERE, on the space's own floor, is nearer (14.90 m), and is the one
    "same floor first" would keep."""
    net = build_network([], [_shaft("S1", 20, 48.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=4, used=4), _n("HERE", -149, 2, ports=10, used=10)])
    t = _t(x)
    assert x.status is Status.INSUFFICIENT_CAPACITY, x.reasons
    # Expected: the nearest within the limit (HERE, own floor) - or at least
    # the sentence does not call a 50 m NER "the nearest" over a 14.9 m one.
    assert x.ner_key == "HERE" or "assigned to the nearest one, UP" not in t, (x.ner_key, t)
    # Adapted (R4-3 decision, rc5): HERE is taken, so it cannot be among its
    # own alternatives; the tester asked for HERE in the alternatives of UP.
    # The NER on the other level is the one that stays in sight.
    assert x.ner_key == "HERE", (x.ner_key, t)
    assert "assigned to the nearest one on this level, HERE, anyway" in t, t
    assert any(k == "UP" for k, _ in x.alternatives), x.alternatives


# =============================================================================
# R3-2 variants: unconfirmed scales
# =============================================================================

def test_r4_same_level_traced_route_on_unconfirmed_sheet_is_definitive_in_a_shaft_project():
    """The space's sheet is unconfirmed. HERE is reached by a traced tray on
    that sheet (20 m); the project has a shaft. The rule in force: an
    unconfirmed scale on any sheet a route runs on gives no definitive
    result. rc4 returns HERE SERVED (the developer's 'Not done')."""
    pages = {1: DECI, 2: DECI_U}
    segs = [_seg(0, 10, -200, 10, 2)]
    net = build_network(segs, [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -200, 2, y=10)], pages=pages)
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.status is not Status.SERVED, (x.ner_key, x.status, _t(x))


def test_guard_r3_2_three_levels_unconfirmed_middle_sheet():
    """Space on 3, NER on 1, a tray on 2 between two shafts: page 2's scale
    is not confirmed, so the route is not definitive."""
    pages = {1: DECI, 2: DECI_U, 3: DECI}
    segs = [_seg(20, 0, 400, 0, 2)]
    links = [_shaft("A", 20, 8.0, lower=2, upper=3), _shaft("B", 400, 8.0, lower=1, upper=2)]
    net = build_network(segs, links, pages)
    area = Area(key="S", name="S", page=3, label_point=Point(0, 0, 3))
    x = _run(net, [_n("UP", 400, 1)], pages=pages, area=area)
    assert x.status is Status.SCALE_NOT_CONFIRMED, (x.status, _t(x))
    pages_ok = {**pages, 2: DECI}
    y = _run(build_network(segs, links, pages_ok), [_n("UP", 400, 1)], pages=pages_ok, area=area)
    assert y.ner_key == "UP" and y.status is Status.SERVED
    assert y.evaluation.effective_distance_m == pytest.approx(2 + 8 + 38 + 8, abs=0.01)


def test_guard_r3_2_unconfirmed_floor_provisional_and_pinned_upstairs():
    pages = {1: DECI, 2: DECI_U}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2)], pages=pages, pinned={"S": "UP"})
    assert x.ner_key == "UP" and x.pinned
    assert x.status is Status.SCALE_NOT_CONFIRMED, x.status
    assert "HERE on this level is within 90 m (14.90 m, on a scale not yet confirmed)" in _t(x)


def test_guard_r3_2_unconfirmed_floor_out_of_limit_reason():
    pages = {1: DECI, 2: DECI_U}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -950, 2)], pages=pages)
    assert x.ner_key is None and x.status is Status.SCALE_NOT_CONFIRMED
    assert ("HERE at 95.00 m in a straight line, over the 90 m limit (on a scale not yet "
            "confirmed)") in _t(x), _t(x)


# =============================================================================
# R3-3 variants: the farthest vertex, routes on both levels
# =============================================================================

def _long_room():
    # 100 m x 4 m, centre at the origin
    poly = [(-500, -20), (500, -20), (500, 20), (-500, 20)]
    return Area(key="S", name="S", page=2, label_point=Point(0, 0, 2), polygon=poly)


def test_r4_far_vertex_measured_differently_on_the_two_levels():
    """A 100 m x 4 m room. HERE, on the floor, is reached by a traced tray
    (85 m of route from the room's centre); UP by a 4 m shaft at the centre.
    The floor NER's far corner is judged by the straight line from that
    corner to HERE (about 51 m), so HERE comes back 85 m, within the limit,
    and UP is set aside. UP's far corner is judged along the route: from the
    corner to where the route joins the sheet, then the route (about 64 m).
    Measured the same way, HERE's far corner is about 85 - 0 + 50 = 135 m
    of cable: HERE does not serve, UP does."""
    # floor tray from the room's centre: 10 + 20 + 45 + 10 = 85 m to HERE at (100, -350)
    segs = [_seg(0, 0, 0, 100, 2), _seg(0, 100, 200, 100, 2), _seg(200, 100, 200, -350, 2),
            _seg(200, -350, 100, -350, 2)]
    up_segs = [_seg(0, 0, 0, 100, 1)]
    net = build_network(segs + up_segs, [_shaft("S1", 0, 4.0)], P)
    area = _long_room()
    here_alone = _run(build_network(segs, [], P), [_n("HERE", 100, 2, y=-350)], area=area)
    assert here_alone.evaluation.method is DistanceMethod.ROUTE
    # 5.6.0: without shafts too, the far corner along the route (was 85.00 m).
    assert here_alone.evaluation.effective_distance_m == pytest.approx(135.04, abs=0.01)
    assert here_alone.status is Status.OUT_OF_LIMIT
    up_alone = _run(build_network(up_segs, [_shaft("S1", 0, 4.0)], P),
                    [_n("UP", 0, 1, y=100)], area=area)
    up_far = up_alone.evaluation.effective_distance_m
    assert up_far < 90, (up_alone.status, up_far, _t(up_alone))
    x = _run(net, [_n("UP", 0, 1, y=100), _n("HERE", 100, 2, y=-350)], area=area)
    # Expected: the two NERs judged the same way on the far corner -> HERE's
    # far corner (~135 m of cable) does not serve, UP's (~64 m) does.
    assert x.ner_key == "UP", (x.ner_key, x.status,
                               x.evaluation.effective_distance_m if x.evaluation else None, _t(x))


def test_guard_r3_3_photographers_far_corner_through_shaft():
    """A 60 m x 4 m photographers' room, UP by an 8 m shaft 2 m from the
    centre: far corner ~32 + 8 + 2 = 42 m... widen to 120 m: ~62+10 = 72 > 70."""
    poly = [(-600, -20), (600, -20), (600, 20), (-600, 20)]
    area = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2), polygon=poly)
    x = _run(build_network([], [_shaft("S1", 20, 8.0)], P), [_n("UP", 0, 1)], area=area,
             demand=DemandType.PHOTOGRAPHER)
    assert x.status is Status.OUT_OF_LIMIT, (x.status, x.evaluation.effective_distance_m)
    assert x.evaluation.limit_m == 70


def test_guard_r3_3_far_vertex_through_three_levels():
    pages = P3
    poly = [(-900, -20), (900, -20), (900, 20), (-900, 20)]
    area = Area(key="S", name="S", page=3, label_point=Point(0, 0, 3), polygon=poly)
    links = [_shaft("A", 20, 4.0, lower=2, upper=3), _shaft("B", 20, 4.0, lower=1, upper=2)]
    x = _run(build_network([], links, pages), [_n("UP", 20, 1)], pages=pages, area=area)
    assert x.status is Status.OUT_OF_LIMIT
    # far corner (-900, +-20) to the foot (20, 0): 92.02 m, + 4 + 4
    assert x.evaluation.effective_distance_m == pytest.approx(100.02, abs=0.01)


# =============================================================================
# R3-4 variants: notes
# =============================================================================

def test_r4_over_the_limit_printed_as_the_limit():
    """HERE 90.004 m: over the 90 m limit, printed '90.00 m, over the 90 m limit'."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -900.04, 2)])
    assert x.ner_key == "UP"
    assert "HERE at 90.00 m in a straight line, over the 90 m limit" not in _t(x), _t(x)


def test_guard_r3_4_four_floor_ners_three_named_then_and_more():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    ners = [_n("UP", 0, 1)] + [_n(f"F{i}", -950 - 10 * i, 2) for i in range(5)]
    seen = set()
    for order in (ners, list(reversed(ners))):
        x = _run(net, order)
        assert x.ner_key == "UP"
        seen.add(tuple(x.reasons))
        assert "; and 2 more on this level" in _t(x), _t(x)
        assert "F0 at 95.00 m" in _t(x)
    assert len(seen) == 1


def test_guard_r3_4_route_reason_on_the_floor():
    """A floor NER reached by a traced route over the limit: 'by the traced route'."""
    segs = [_seg(0, 10, -960, 10, 2)]
    net = build_network(segs, [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -960, 2, y=10)])
    assert x.ner_key == "UP", _t(x)
    assert "HERE at 97.00 m by the traced route, over the 90 m limit" in _t(x), _t(x)


# =============================================================================
# R3-5 variants: each sheet's scale
# =============================================================================

def test_guard_r3_5_reverse_direction_space_on_the_fine_sheet():
    """Space on page 2 at 1u = 0.01 m, UP on page 1 at 1u = 0.1 m: the space
    is 200 units = 2 m from the foot, UP 10 units = 1 m: 2 + 8 + 1 = 11 m."""
    pages = {1: DECI, 2: CENTI}
    net = build_network([], [_shaft("S1", 200, 8.0)], pages)
    x = _run(net, [_n("UP", 210, 1)], pages=pages)
    assert x.ner_key == "UP" and x.evaluation.effective_distance_m == pytest.approx(11.0), _t(x)


def test_guard_r3_5_three_levels_three_scales():
    pages = {1: CENTI, 2: DECI, 3: Scale(metres_per_unit=0.05, source="manual", label="x",
                                          confirmed=True)}
    # space page 3 at (0,0); foot A at (40, 0) on 3 = 2 m; tray on 2 from 0 to 300 = 30 m
    # foot B at (300, 0) on 2 -> page 1 foot at (30000, 0)? feet share coordinates.
    segs = [_seg(40, 0, 300, 0, 2)]
    links = [VerticalLink("A", Point(40, 0, 2), Point(40, 0, 3), 4.0),
             VerticalLink("B", Point(300, 0, 1), Point(300, 0, 2), 4.0)]
    net = build_network(segs, links, pages)
    area = Area(key="S", name="S", page=3, label_point=Point(0, 0, 3))
    x = _run(net, [_n("UP", 400, 1)], pages=pages, area=area)
    # 2 m lead-in (40 u x 0.05) + 4 + 26 m tray (260 u x 0.1) + 4 + 1 m (100 u x 0.01)
    assert x.ner_key == "UP" and x.evaluation.effective_distance_m == pytest.approx(37.0), _t(x)


def test_guard_r3_5_ner_sheet_without_scale_is_not_definitive():
    pages = {2: DECI}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1)], pages=pages)
    assert x.status is not Status.SERVED, (x.status, _t(x))


# =============================================================================
# R3-6 / R3-7 variants: determinism
# =============================================================================

@pytest.mark.parametrize("order", ["AB", "BA"])   # 5.6.0: R4-5 fixed, no xfail
def test_r4_equidistant_trays_registration_order_changes_the_distance(order):
    """The space is exactly 3 m from two trays on its sheet. Tray A leads to
    HERE in 20 m, tray B in 66 m. The joint is whichever tray was
    registered first, so HERE is 23 m or 69 m by registration order."""
    a = [_seg(0, 30, -200, 30, 2, "A")]                       # 3 m above: 20 m to HERE
    b = [_seg(0, -30, -400, -30, 2, "B"), _seg(-400, -30, -400, 30, 2, "B2"),
         _seg(-400, 30, -200, 30, 2, "B3")]                     # 3 m below: 66 m to HERE
    segs = a + b if order == "AB" else b + a
    net = build_network(segs, [_shaft("Z", 5000, 4.0)], P)
    x = _run(net, [_n("HERE", -200, 2, y=30)])
    assert x.evaluation.effective_distance_m == pytest.approx(23.0, abs=0.01), (order, _t(x))


@pytest.mark.parametrize("order", ["fwd", "rev"])
def test_guard_r3_7_four_levels_many_shafts_same_answer(order):
    pages = {1: DECI, 2: DECI, 3: DECI, 4: DECI}
    segs = [_seg(-1000, 0, 1000, 0, p) for p in (1, 2, 3, 4)]
    links = []
    for lo in (1, 2, 3):
        for x, lab in ((-600, "W"), (600, "E"), (-300, "w"), (300, "e")):
            links.append(_shaft(f"{lab}{lo}", x, 8.0, lower=lo, upper=lo + 1))
    if order == "rev":
        links.reverse()
        segs.reverse()
    net = build_network(segs, links, pages)
    opts = net.route_options(Point(0, 50, 4), Point(0, 50, 1), DECI)
    assert opts[0]["vertical_links"] == ["e3", "e2", "e1"], opts[0]["vertical_links"]


@pytest.mark.parametrize("order", ["fwd", "rev"])
def test_guard_r3_6_equal_links_coverage_note_independent(order):
    a, b = _shaft("B", 20, 8.0), _shaft("A", 20, 8.0)
    net = build_network([], [a, b] if order == "fwd" else [b, a], P)
    x = _run(net, [_n("UP", 0, 1)])
    assert x.ner_key == "UP"
    assert x.evaluation.effective_distance_m == pytest.approx(12.0)


# =============================================================================
# R3-8 / R3-9 variants
# =============================================================================

def test_guard_r3_8_pin_to_one_of_two_upstairs_floor_serves():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("UP2", 30, 1), _n("HERE", -149, 2)],
             pinned={"S": "UP2"})
    assert x.ner_key == "UP2" and x.pinned
    t = _t(x)
    assert "HERE on this level serves within 90 m (14.90 m) and would be used" in t, t
    assert any(k == "HERE" for k, _ in x.alternatives), x.alternatives


def test_guard_r3_9_alternatives_keep_the_floor_ner_with_three_upstairs():
    net = build_network([], [_shaft("S1", 20, 116.0)], P)
    ners = [_n("UP", 0, 1), _n("UP2", 10, 1), _n("UP3", 30, 1), _n("HERE", -950, 2)]
    x = _run(net, ners)
    assert x.ner_key is None and x.status is Status.OUT_OF_LIMIT
    keys = [k for k, _ in x.alternatives]
    assert "HERE" in keys and len(keys) == 3, keys


# =============================================================================
# Performance
# =============================================================================

def test_guard_perf_symmetric_ties_many_shafts():
    """5 levels, 21 x 21 tray grid per level, a shaft at every 4th crossing
    (lots of equal-length ways), 20 spaces, 20 NERs: under 60 s."""
    pages = {p: DECI for p in range(1, 6)}
    segs = []
    for p in pages:
        for i in range(21):
            segs.append(_seg(i * 100, 0, i * 100, 2000, p))
            segs.append(_seg(0, i * 100, 2000, i * 100, p))
    links = []
    for lo in range(1, 5):
        for i in range(0, 21, 4):
            for j in range(0, 21, 4):
                links.append(VerticalLink(f"V{lo}-{i}-{j}", Point(i * 100, j * 100, lo),
                                          Point(i * 100, j * 100, lo + 1), 4.0))
    net = build_network(segs, links, pages)
    ners = [_n(f"N{p}{k}", 100 + 400 * k, p, y=1050) for p in (1, 5) for k in range(5)]
    areas = [(Area(key=f"S{p}{k}", name="s", page=p, label_point=Point(50 + 400 * k, 950, p)), 1.0)
             for p in (2, 3, 4) for k in range(5)]
    t0 = time.time()
    got = _run_many(net, ners, areas, pages=pages)
    dt = time.time() - t0
    assert len(got) == 15
    assert dt < 60, dt


# =============================================================================
# Notes: further checks
# =============================================================================

def test_r4_one_level_capacity_note_false_in_a_project_without_shafts():
    """The developer's 'Not done': on one level the 5.3.0 route rule sets F
    (20 m, 48 free) aside, R (route, full) takes the space, and the row says
    'every NER within the distance limit is out of ports'. With a shaft
    anywhere in the project the sentence is corrected; without one it is not."""
    segs = [_seg(0, 10, 300, 10, 2)]
    net = build_network(segs, [], P)
    x = _run(net, [_n("R", 300, 2, y=10, ports=4, used=4), _n("F", -200, 2, ports=48)])
    assert x.ner_key == "R" and x.status is Status.INSUFFICIENT_CAPACITY
    assert "every NER within the distance limit is out of ports" not in _t(x), _t(x)


def test_r4_neither_serves_row_says_nearest_is_the_farther_ner():
    """R3-9 as decided: HERE (95 m, named) and UP (120 m). The last sentence
    reads 'nearest NER is UP at 120.0 m' right after 'HERE at 95.00 m'."""
    net = build_network([], [_shaft("S1", 20, 116.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -950, 2)])
    assert "HERE at 95.00 m" in _t(x)
    assert "nearest NER is UP at 120.0 m" not in _t(x), x.reasons


def test_r4_not_counting_cross_level_notes_follow_ner_registration_order():
    """Five NERs upstairs reached only by a 4 m shaft that does not count.
    The row names three of them and 'and 2 more'; which three depends on the
    order the NERs are registered in."""
    net = build_network([], [_shaft("S1", 20, 4.0)], P)
    ners = [_n(f"UP{i}", 20 + 10 * i, 1) for i in range(5)]
    a = _run(net, ners)
    b = _run(net, list(reversed(ners)))
    assert a.reasons == b.reasons, (a.reasons, b.reasons)


def test_r4_space_sheet_without_scale_note_says_no_ner_on_this_level():
    """The space's sheet has no scale; HERE is on that sheet and a shaft is
    registered. The row says 'no NER on this level and no vertical route
    registered' - both false. (Same on rc3.)"""
    pages = {1: DECI}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2)], pages=pages)
    assert "no NER on this level and no vertical route registered" not in _t(x), _t(x)


def test_r4_nearest_sentence_rounds_onto_the_limit():
    """Only UP, 90.04 m by route (86.04 m shaft + 4 m): out of limit; the
    row ends 'nearest NER is UP at 90.0 m (limit 90 m)'."""
    net = build_network([], [_shaft("S1", 20, 86.04)], P)
    x = _run(net, [_n("UP", 0, 1)])
    assert x.status is Status.OUT_OF_LIMIT
    assert "nearest NER is UP at 90.0 m (limit 90 m)" not in _t(x), x.reasons


def test_r4_pinned_over_the_limit_sentence_rounds_onto_the_limit():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("FAR", -900.4, 2)], pinned={"S": "FAR"})
    assert x.status is Status.OUT_OF_LIMIT
    assert "and it is 90.0 m away against a 90 m limit" not in _t(x), x.reasons


def test_r4_measured_from_an_occurrence_through_a_shaft_note_says_the_ner_is_on_that_page():
    """The space is tied to page 2 and also labelled (confirmed) on page 3.
    UP is on page 1, reached by a shaft from page 3 only. The row says
    'measured from page 3 ... - UP is on that page'; UP is on page 1."""
    occ = Area(key="S", name="S", page=3, label_point=Point(0, 0, 3), meta={"link_basis": "same code"})
    area = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2), occurrences=[occ])
    net = build_network([], [_shaft("S13", 20, 8.0, lower=1, upper=3)], P3)
    x = _run(net, [_n("UP", 0, 1)], pages=P3, area=area)
    assert x.ner_key == "UP" and x.status is Status.SERVED, _t(x)
    assert "UP is on that page" not in _t(x), _t(x)


# =============================================================================
# rc5 additions (developer): the owner's decisions on R4-1 to R4-11
# =============================================================================

def test_rc5_r4_1_same_level_route_far_corner_along_the_route_on_every_project():
    """The tester's 100 m x 4 m room, HERE by its 85 m tray. The far corner
    goes corner -> joint -> route (50.04 + 85 = 135.04 m), with a shaft
    anywhere in the project or not. 5.5.1-rc5 kept the one-level rule of
    5.3.0 without one (85.00 m, the straight line from the corner to HERE);
    5.6.0 measures alike on every project."""
    segs = [_seg(0, 0, 0, 100, 2), _seg(0, 100, 200, 100, 2), _seg(200, 100, 200, -350, 2),
            _seg(200, -350, 100, -350, 2)]
    area = _long_room()
    with_shaft = _run(build_network(segs, [_shaft("Z", 5000, 4.0)], P),
                      [_n("HERE", 100, 2, y=-350)], area=area)
    assert with_shaft.status is Status.OUT_OF_LIMIT
    assert with_shaft.evaluation.effective_distance_m == pytest.approx(135.04, abs=0.01)
    assert "then along the route" in _t(with_shaft), _t(with_shaft)
    without = _run(build_network(segs, [], P), [_n("HERE", 100, 2, y=-350)], area=area)
    assert without.evaluation.effective_distance_m == pytest.approx(135.04, abs=0.01)
    assert without.status is Status.OUT_OF_LIMIT
    assert "then along the route" in _t(without), _t(without)


def test_rc5_r4_2_a_same_level_route_on_an_unconfirmed_sheet_on_every_project():
    """5.6.0: with vertical links or without, a traced route on a sheet whose
    scale is unconfirmed gives no definitive result (5.5.1-rc5 and earlier:
    served without vertical links)."""
    pages = {1: DECI, 2: DECI_U}
    segs = [_seg(0, 10, -200, 10, 2)]
    x = _run(build_network(segs, [], pages), [_n("HERE", -200, 2, y=10)], pages=pages)
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.status is Status.SCALE_NOT_CONFIRMED and x.ner_key is None
    assert x.nearest_label == "HERE"
    y = _run(build_network(segs, [_shaft("Z", 5000, 4.0)], pages),
             [_n("HERE", -200, 2, y=10)], pages=pages)
    assert y.status is Status.SCALE_NOT_CONFIRMED and y.ner_key is None
    assert y.nearest_label == "HERE"


def test_rc5_r4_3_full_floor_ner_is_kept_when_upstairs_is_out_of_limit():
    """HERE (14.9 m, full) and UP (104 m by a 100 m shaft, free): nothing
    serves within the limit with free ports, so the capacity fallback takes
    the nearest NER on the level, HERE, and says so; rc4 reported UP out of
    limit and left the space with no NER."""
    net = build_network([], [_shaft("S1", 20, 100.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=4), _n("HERE", -149, 2, ports=10, used=10)])
    assert x.ner_key == "HERE" and x.status is Status.INSUFFICIENT_CAPACITY, _t(x)
    assert "assigned to the nearest one on this level, HERE, anyway" in _t(x)
    assert [k for k, _ in x.alternatives] == ["UP"]


def test_rc5_r4_3_no_floor_ner_within_limit_the_nearest_overall_is_taken():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=4, used=4), _n("UP2", 40, 1, ports=4, used=4),
                   _n("HERE", -950, 2, ports=10, used=10)])
    assert x.ner_key == "UP" and x.status is Status.INSUFFICIENT_CAPACITY
    assert "assigned to the nearest one, UP, anyway" in _t(x), _t(x)
    assert [k for k, _ in x.alternatives] == ["UP2", "HERE"], x.alternatives


def test_rc5_r4_6_unused_upstairs_ners_named_nearest_first_not_by_label():
    net = build_network([], [_shaft("S1", 20, 4.0)], P)
    ners = [_n(k, x, 1) for k, x in (("Z", 20), ("A", 30), ("M", 40), ("B", 50), ("C", 60))]
    x = _run(net, ners)
    named = [r.split(" ")[4] for r in x.reasons if r.startswith("a vertical route reaches")]
    assert named == ["Z", "A", "M", "B", "C"], x.reasons      # label order: A B C M Z
    # With a NER measured on the floor, three are named, then "and 2 more".
    y = _run(net, list(reversed(ners)) + [_n("HERE", -950, 2)])
    named = [r.split(" ")[4] for r in y.reasons if r.startswith("a vertical route reaches")]
    assert named == ["Z", "A", "M"], y.reasons
    assert "and 2 more NER(s) on other levels reached only by routes that do not count" in _t(y)


@pytest.mark.parametrize("value, limit, decimals, printed", [
    (90.004, 90, 2, "90.004 m"),       # over: never "90.00 m"
    (89.996, 90, 2, "89.996 m"),       # under: never "90.00 m"
    (90.04, 90, 1, "90.04 m"),         # over: never "90.0 m"
    (89.96, 90, 1, "89.96 m"),
    (95.3, 90, 2, "95.30 m"),          # far from the limit: as before
    (95.34, 90, 1, "95.3 m"),
    (90.0, 90, 1, "90.0 m"),           # exactly the limit
    (90 + 1e-12, 90, 1, "just over 90 m"),
    (0.04, 0.0, 1, "0.04 m"),          # a margin
])
def test_rc5_r4_7_a_figure_never_prints_onto_the_limit(value, limit, decimals, printed):
    from app.domain.distance import figure_vs_limit
    assert figure_vs_limit(value, limit, decimals) == printed


def test_rc5_r4_7_exception_details_and_summary_never_round_onto_the_limit():
    area = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2))
    near = compute_coverage(areas=[(area, 2.0, DemandType.GENERAL, None, True)],
                            ners=[_n("N", -899.6, 2)], scale_for_page=P, rules=DistanceRules())
    assert near.exceptions[0]["detail"] == "89.96 m of 90 m (0.04 m margin)"
    over = compute_coverage(areas=[(area, 2.0, DemandType.GENERAL, None, True)],
                            ners=[_n("N", -900.4, 2)], scale_for_page=P, rules=DistanceRules(),
                            pinned={"S": "N"})
    assert over.exceptions[0]["detail"] == "assigned to N by you; 90.04 m of 90 m"
    assert over.assignments[0].evaluation.summary() == "out_of_limit: 90.04 m of 90.0 m"


def test_rc5_r4_9_unscaled_sheet_without_shafts_names_the_ner_on_it():
    """The older sentence was false without shafts too: "no NER on this
    level" while HERE is on it. Only the sentence changes (status, NER and
    figures are the same)."""
    x = _run(None, [_n("HERE", -149, 2)], pages={1: DECI})
    assert x.status is Status.NOT_ANALYSABLE and x.ner_key is None
    assert x.reasons[-1] == ("no NER is measurable from this space: page 2 has no scale, so "
                             "HERE on this level could not be measured - set the sheet's scale")
    y = _run(None, [_n("UP", 0, 1)], pages={1: DECI})          # true: kept
    assert y.reasons[-1] == ("no NER is measurable from this space "
                             "(no NER on this level and no vertical route registered)")


def test_rc5_r4_9_unscaled_sheet_with_a_shaft_names_both():
    pages = {1: DECI}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2)], pages=pages)
    assert ("page 2 has no scale, so HERE on this level and a route through a shaft or riser "
            "to another level could not be measured") in x.reasons[-1], x.reasons


def test_rc5_r4_11_the_canonical_way_does_not_depend_on_the_order_of_questions():
    """The canonical search is kept per (target, node) for the network's
    life: asking about two spaces in either order gives the same routes."""
    pages = {1: DECI, 2: DECI, 3: DECI}
    segs = [_seg(-1000, 0, 1000, 0, p) for p in (1, 2, 3)]
    links = [_shaft(f"{lab}{lo}", x, 8.0, lower=lo, upper=lo + 1)
             for lo in (1, 2) for x, lab in ((-300, "w"), (300, "e"), (-600, "W"), (600, "E"))]
    qs = [(Point(0, 50, 3), Point(0, 50, 1)), (Point(-450, 50, 3), Point(450, 50, 1)),
          (Point(450, 50, 1), Point(-450, 50, 3))]
    got = []
    for order in (list(range(3)), [2, 1, 0]):
        net = build_network(segs, links, pages)
        ans = {i: net.route_options(*qs[i], DECI)[0]["vertical_links"] for i in order}
        got.append(ans)
    assert got[0] == got[1]
    assert got[0][0] == ["e2", "e1"], got[0]
