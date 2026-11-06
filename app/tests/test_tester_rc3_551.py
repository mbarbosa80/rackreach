"""Independent tester on 5.5.1-rc3 (4ea564c..a5482e6): "same floor first" and R2-1.

Each test asserts the EXPECTED behaviour, so a failing test is a finding
(R3-n in /home/claude/w/tester-553/FINDINGS.md). Tests marked GUARD pass and
document what was checked and found fine.

Copied into the repo for 5.5.1-rc4 from the tester's
tests/test_tester_553.py. Adapted to the rc4 decisions (qa/NOTES-551.md, rc4):
the tester's own assertions are kept, and tightened where the decision is
narrower than "either outcome"; R3-9 follows the decision to keep reporting
the nearest NER the rules leave (UP), with the nearer floor NER kept in the
alternatives and named. The tests after each tester test are rc4 additions.
"""

from __future__ import annotations

import pytest

from app.domain.coverage import NER, compute_coverage
from app.domain.distance import DemandType, DistanceMethod, DistanceRules, Status
from app.domain.geometry import Area, Point
from app.domain.pdf_ingest import ElementKind
from app.domain.routes import RouteSegment, VerticalLink, build_network
from app.domain.units import Scale

DECI = Scale(metres_per_unit=0.1, source="manual", label="1u=0.1m", confirmed=True)
DECI_UNCONFIRMED = Scale(metres_per_unit=0.1, source="auto", label="1u=0.1m", confirmed=False)
CENTI = Scale(metres_per_unit=0.01, source="manual", label="1u=0.01m", confirmed=True)
P = {1: DECI, 2: DECI}


def _n(key, x, page, crit=True, ports=None, used=0, y=0.0):
    return NER(key=key, label=key,
               kind=ElementKind.NER_CRITICAL if crit else ElementKind.NER_NONCRITICAL,
               point=Point(x, y, page), critical=crit, total_ports=ports, used_ports=used)


def _shaft(key, x, length_m, lower=1, upper=2, y=0.0):
    return VerticalLink(key, Point(x, y, lower), Point(x, y, upper), length_m)


def _run(net, ners, pages=P, demand=DemandType.GENERAL, crit=None, pinned=None,
         area=None, ports=2.0):
    area = area or Area(key="S", name="S", page=2, label_point=Point(0, 0, 2))
    res = compute_coverage(areas=[(area, ports, demand, crit, True)], ners=ners,
                           scale_for_page=pages, rules=DistanceRules(), network=net,
                           pinned=pinned)
    return res.assignments[0]


def _t(x):
    return " | ".join(x.reasons + (x.evaluation.assumptions if x.evaluation else []))


# ---- R3-1: capacity - the fallback note is false -----------------------------

def test_r3_1_full_floor_ner_with_free_cross_level_ner_within_limit():
    """HERE (14.9 m, 10/10 ports used) and UP (12 m by a counting 8 m shaft,
    48 ports free). rc3 assigns HERE with INSUFFICIENT_CAPACITY and writes
    "every NER within the distance limit is out of ports" - UP is within the
    limit and has 48 free ports."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=48), _n("HERE", -149, 2, ports=10, used=10)])
    claim = "every NER within the distance limit is out of ports"
    # Either UP serves, or at least the row does not claim something false.
    assert x.ner_key == "UP" or claim not in _t(x), (x.ner_key, x.status, x.reasons)
    # rc4 decision: a full floor NER does not serve, so UP competes and serves.
    assert x.ner_key == "UP" and x.status is Status.SERVED, (x.ner_key, x.reasons)
    assert claim not in _t(x)
    assert "HERE at 14.90 m in a straight line, out of ports (0 free against 2 required)" \
        in _t(x), _t(x)


def test_r3_1_unknown_capacity_on_the_floor_still_serves():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=48), _n("HERE", -149, 2)])
    assert x.ner_key == "HERE", x.reasons


def test_r3_1_floor_ner_with_enough_free_ports_serves():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=48), _n("HERE", -149, 2, ports=10, used=8)])
    assert x.ner_key == "HERE" and x.status is Status.SERVED, x.reasons


def test_r3_1_both_full_the_fallback_claim_is_true():
    """Every NER within the limit is out of ports: the nearest is taken anyway,
    and the sentence is true."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1, ports=4, used=4), _n("HERE", -149, 2, ports=10, used=10)])
    # rc5 (owner's decision on R4-3): "same floor first" holds in the
    # capacity fallback too - HERE, the nearest on the space's own level, is
    # taken (rc4 took UP, 12 m by the shaft), and the sentence says so.
    assert x.ner_key == "HERE" and x.status is Status.INSUFFICIENT_CAPACITY, x.reasons
    assert "every NER within the distance limit is out of ports" in _t(x)
    assert "assigned to the nearest one on this level, HERE, anyway" in _t(x), _t(x)
    assert [k for k, _ in x.alternatives] == ["UP"], x.alternatives


# ---- R3-2: an unconfirmed scale on the space's sheet defeats the rule --------

def test_r3_2_unconfirmed_scale_hands_the_space_to_the_other_level_as_served():
    """Space page 2's scale is not confirmed. HERE, 14.9 m away on page 2,
    comes back SCALE_NOT_CONFIRMED (not 'compliant'), so the gate lets UP in
    and UP is returned SERVED by a route whose lead-in and tray lengths use
    that same unconfirmed scale. Confirming the scale flips the space to
    HERE."""
    pages = {1: DECI, 2: DECI_UNCONFIRMED}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2)], pages=pages)
    confirmed = _run(build_network([], [_shaft("S1", 20, 8.0)], P),
                     [_n("UP", 0, 1), _n("HERE", -149, 2)])
    assert confirmed.ner_key == "HERE"
    # expected: no definitive SERVED verdict from another level while the
    # space's own sheet is unconfirmed, and the same NER as once confirmed
    assert not (x.ner_key == "UP" and x.status is Status.SERVED), (x.ner_key, x.status, x.reasons)
    # rc4: nothing definitive; the NER reported is the one the confirmed scale gives.
    assert x.ner_key is None and x.status is Status.SCALE_NOT_CONFIRMED, (x.ner_key, x.status)
    assert x.nearest_label == "HERE", (x.nearest_label, x.reasons)
    assert "on a scale not yet confirmed" in _t(x), _t(x)


def test_r3_2_unconfirmed_scale_on_the_space_sheet_with_the_floor_out_of_limit():
    """HERE is out of the limit, so UP is a candidate - but its lead-in on the
    space's sheet uses the unconfirmed scale: not served."""
    pages = {1: DECI, 2: DECI_UNCONFIRMED}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -950, 2)], pages=pages)
    assert x.ner_key is None and x.status is Status.SCALE_NOT_CONFIRMED, (x.ner_key, x.reasons)


def test_r3_2_unconfirmed_scale_on_the_ners_sheet():
    """The space's sheet is confirmed, the NER's is not: no definitive served
    through that route either."""
    pages = {1: DECI_UNCONFIRMED, 2: DECI}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    x = _run(net, [_n("UP", 0, 1)], pages=pages)
    assert x.ner_key is None and x.status is Status.SCALE_NOT_CONFIRMED, (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(12.0)
    ok = _run(build_network([], [_shaft("S1", 20, 8.0)], P), [_n("UP", 0, 1)])
    assert ok.ner_key == "UP" and ok.status is Status.SERVED


# ---- R3-3: polygon - same-level NER judged on the far corner, cross-level not

def test_r3_3_polygon_far_corner_counts_on_the_floor_but_not_through_the_shaft():
    """A 180 m x 4 m room. HERE sits at its centre: 0 m to the centre but
    90.02 m to the far corner -> OUT_OF_LIMIT, so the floor 'does not serve'.
    UP, by an 8 m shaft 2 m from the centre, is returned SERVED at 12.0 m -
    measured to the centre only. The far corner is about 92 m + 8 m + 2 m =
    ~102 m of cable from UP."""
    poly = [(-900, -20), (900, -20), (900, 20), (-900, 20)]
    area = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2), polygon=poly)
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", 0, 2)], area=area)
    here_alone = _run(build_network([], [], P), [_n("HERE", 0, 2)], area=area)
    assert here_alone.status is Status.OUT_OF_LIMIT  # far corner 90.02 m
    # expected: UP measured to the same far corner is out of limit too
    assert not (x.ner_key == "UP" and x.status is Status.SERVED), (
        x.ner_key, x.status, x.evaluation.effective_distance_m, x.reasons)
    # rc4: UP alone is measured to the far corner: 92.02 + 8 + 2 m.
    up_alone = _run(build_network([], [_shaft("S1", 20, 8.0)], P), [_n("UP", 0, 1)], area=area)
    assert up_alone.status is Status.OUT_OF_LIMIT, up_alone.reasons
    assert up_alone.evaluation.effective_distance_m == pytest.approx(102.02, abs=0.01)
    assert up_alone.evaluation.farthest_point_distance_m == pytest.approx(102.02, abs=0.01)


def test_r3_3_a_small_room_is_still_served_from_upstairs():
    """A 4 m x 4 m room around the space: the far corner adds a little, served."""
    poly = [(-20, -20), (20, -20), (20, 20), (-20, 20)]
    area = Area(key="S", name="S", page=2, label_point=Point(0, 0, 2), polygon=poly)
    x = _run(build_network([], [_shaft("S1", 20, 8.0)], P), [_n("UP", 0, 1)], area=area)
    assert x.ner_key == "UP" and x.status is Status.SERVED
    # (-20, +-20) to the foot (20, 0): 4.47 m, then 8 m + 2 m.
    assert x.evaluation.effective_distance_m == pytest.approx(14.47, abs=0.01)


# ---- R3-4: the note for a floor NER lost on the limit ------------------------

def test_r3_4_floor_ner_over_the_limit_is_described_as_not_compared_no_route():
    """HERE 90.01 m away, UP 12 m by shaft. UP correctly serves, but the row
    says HERE was 'not compared (no route to them)' 'at 90.0 m' - HERE was
    compared (it is why UP was admitted) and 90.0 m reads as within 90 m."""
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -900.1, 2)])
    assert x.ner_key == "UP"
    t = _t(x)
    assert "not compared (no route to them): HERE at 90.0 m" not in t, t
    # rc4: the real reason, two decimals.
    assert "HERE at 90.01 m in a straight line, over the 90 m limit" in t, t
    assert "not compared" not in t, t


def test_r3_4_floor_ner_just_within_the_limit_is_printed_with_two_decimals():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -899.9, 2)])
    assert x.ner_key == "HERE"
    assert "HERE on this level serves within 90 m (89.99 m)" in _t(x), _t(x)


def test_r3_4_floor_ner_not_critical_rated_says_so():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2, crit=False)], crit=True)
    assert x.ner_key == "UP"
    assert "HERE at 14.90 m in a straight line, not critical-rated" in _t(x), _t(x)
    assert "not compared" not in _t(x)


# ---- R3-5: different scales on the two levels --------------------------------

def test_r3_5_lead_in_on_the_ners_sheet_uses_the_spaces_scale():
    """Page 1 (UP's sheet) is 1u = 0.01 m, page 2 (the space's) 1u = 0.1 m.
    UP is 100 units = 1.00 m from the shaft foot on its sheet; the space is
    2.00 m from the foot on its sheet; the shaft is 8 m. Real: 8 m traced,
    3 m lead-in, 11 m - counts. rc3 converts UP's 100 units with page 2's
    scale (10 m), reports 12.00 m lead-in and drops UP."""
    pages = {1: CENTI, 2: DECI}
    net = build_network([], [_shaft("S1", 20, 8.0)], pages)
    opts = net.route_options(Point(0, 0, 2), Point(120, 0, 1), DECI)
    assert opts and opts[0]["lead_in_m"] == pytest.approx(3.0), opts
    x = _run(net, [_n("UP", 120, 1)], pages=pages)
    assert x.ner_key == "UP" and x.evaluation.effective_distance_m == pytest.approx(11.0), x.reasons


def test_r3_5_join_reach_on_the_ners_sheet_uses_its_own_scale():
    """UP is 14 m from the shaft foot on its 1u = 0.01 m sheet (1400 units):
    within the 15 m join reach measured on that sheet, although 1400 units
    would be 140 m at the space's scale. 20 m shaft: 20 m traced, 16 m of
    lead-in - it counts, 36 m."""
    pages = {1: CENTI, 2: DECI}
    net = build_network([], [_shaft("S1", 20, 20.0)], pages)
    x = _run(net, [_n("UP", 1420, 1)], pages=pages)
    assert x.ner_key == "UP" and x.evaluation.effective_distance_m == pytest.approx(36.0), x.reasons


def test_r3_5_the_space_end_keeps_the_callers_scale():
    """Same sheet both ends: nothing changes, whatever the network was built with."""
    pages = {1: CENTI, 2: DECI}
    net = build_network([RouteSegment(Point(0, 0, 2), Point(500, 0, 2), 2)], [], pages)
    r = net.route_length_m(Point(0, 10, 2), Point(500, 10, 2), DECI)
    assert r["traced_m"] == pytest.approx(50.0) and r["lead_in_m"] == pytest.approx(2.0)


# ---- R3-6 / R3-7: R2-1 is not complete ---------------------------------------

@pytest.mark.parametrize("order", ["AB", "BA"])
def test_r3_6_equal_links_on_the_same_feet_keep_the_first_registered_label(order):
    a, b = _shaft("A", 20, 8.0), _shaft("B", 20, 8.0)
    net = build_network([], [a, b] if order == "AB" else [b, a], P)
    opts = net.route_options(Point(0, 0, 2), Point(0, 0, 1), DECI)
    assert opts[0]["vertical_links"] == ["A"], opts   # BA gives ["B"]


@pytest.mark.parametrize("order", ["LR", "RL"])
def test_r3_7_equal_paths_between_one_joint_pair_follow_registration_order(order):
    """A tray on each level; two identical 8 m shafts 60 m left and right of
    the space. Space and UP each join their tray at one point, so there is
    one joint pair and Dijkstra picks the path - by insertion order. The note
    names LEFT or RIGHT depending on registration order."""
    segs = [RouteSegment(Point(-1000, 0, 2), Point(1000, 0, 2), 2),
            RouteSegment(Point(-1000, 0, 1), Point(1000, 0, 1), 1)]
    L, R = _shaft("LEFT", -600, 8.0), _shaft("RIGHT", 600, 8.0)
    net = build_network(segs, [L, R] if order == "LR" else [R, L], P)
    area = Area(key="S", name="S", page=2, label_point=Point(0, 50, 2))
    x = _run(net, [_n("UP", 0, 1, y=50), _n("HERE", -149, 2, y=50)], area=area)
    assert x.ner_key == "HERE"
    assert "via LEFT" in _t(x), _t(x)                  # RL gives "via RIGHT"


@pytest.mark.parametrize("order", ["fwd", "rev"])
def test_r3_7_equal_paths_through_three_levels_choose_by_labels(order):
    """Space on page 3, NER on page 1. Two equal ways down: B-left then C on
    the left, A-right then D on the right. The labels decide: A-right, D."""
    pages = {1: DECI, 2: DECI, 3: DECI}
    segs = [RouteSegment(Point(-1000, 0, p), Point(1000, 0, p), p) for p in (1, 2, 3)]
    links = [_shaft("B-left", -600, 8.0, lower=2, upper=3), _shaft("A-right", 600, 8.0, lower=2, upper=3),
             _shaft("C", -600, 8.0, lower=1, upper=2), _shaft("D", 600, 8.0, lower=1, upper=2)]
    if order == "rev":
        links.reverse()
        segs.reverse()
    net = build_network(segs, links, pages)
    opts = net.route_options(Point(0, 50, 3), Point(0, 50, 1), DECI)
    assert opts[0]["vertical_links"] == ["A-right", "D"], opts[0]
    assert opts[0]["length_m"] == pytest.approx(5 + 60 + 8 + 8 + 60 + 5)


# ---- R3-8: a cross-level pin makes the floor NER "not compared" --------------

def test_r3_8_pinned_cross_level_ner_sets_the_floor_ner_aside_as_unrouted():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2)], pinned={"S": "UP"})
    assert x.ner_key == "UP" and x.pinned
    t = _t(x)
    assert "not compared (no route to them): HERE" not in t, t
    assert any(k == "HERE" for k, _ in x.alternatives), x.alternatives
    # rc4: named honestly.
    assert "HERE on this level serves within 90 m (14.90 m) and would be used" in t, t


# ---- R3-9: neither serves - the nearer floor NER is hidden -------------------

def test_r3_9_both_out_of_limit_reports_the_farther_cross_level_ner_as_nearest():
    net = build_network([], [_shaft("S1", 20, 116.0)], P)   # UP 120 m
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -950, 2)])    # HERE 95 m
    assert x.ner_key is None and x.status is Status.OUT_OF_LIMIT
    # rc5 (owner's decision on R4-8, which replaces the rc4 decision on
    # R3-9): the truly nearest of every NER compared is reported - HERE, as
    # the tester expected on rc3 - and the sentence agrees with the figures.
    assert x.nearest_label == "HERE", (x.nearest_label, x.reasons)
    assert [k for k, _ in x.alternatives] == ["HERE", "UP"], x.alternatives
    assert "HERE at 95.00 m in a straight line, over the 90 m limit" in _t(x), _t(x)
    assert x.reasons[-1] == "nearest NER is HERE at 95.0 m (limit 90 m)", x.reasons


# ---- GUARDS (pass on rc3) ----------------------------------------------------

@pytest.mark.parametrize("here_x, demand, expect", [
    (-900, DemandType.GENERAL, "HERE"),        # exactly 90.00 m: near limit, serves
    (-899.9, DemandType.GENERAL, "HERE"),
    (-900.1, DemandType.GENERAL, "UP"),        # 90.01 m
    (-700, DemandType.PHOTOGRAPHER, "HERE"),   # exactly 70.00 m
    (-700.1, DemandType.PHOTOGRAPHER, "UP"),   # 70.01 m
    (-750, DemandType.GENERAL, "HERE"),        # 75 m: general yes
    (-750, DemandType.PHOTOGRAPHER, "UP"),     # 75 m: photographers no
])
def test_guard_boundaries(here_x, demand, expect):
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", here_x, 2)], demand=demand)
    assert x.ner_key == expect, (x.ner_key, x.reasons)


def test_guard_many_levels_named_three_then_and_n_more_deterministic():
    pages = {1: DECI, 2: DECI, 3: DECI}
    links = [_shaft("S1", 20, 8.0), _shaft("S3", -20, 8.0, lower=2, upper=3)]
    ners = ([_n(f"UP{i}", i * 5, 1) for i in range(3)]
            + [_n(f"DN{i}", -i * 5, 3) for i in range(3)] + [_n("HERE", -149, 2)])
    seen = set()
    for order in (ners, list(reversed(ners))):
        x = _run(build_network([], links, pages), order, pages=pages)
        assert x.ner_key == "HERE"
        seen.add(tuple(x.reasons))
        assert "and 3 more NER(s) on other levels not used" in _t(x)
    assert len(seen) == 1


def test_guard_no_same_level_ner_cross_levels_compete_shortest_wins():
    pages = {1: DECI, 2: DECI, 3: DECI}
    links = [_shaft("S1", 20, 30.0), _shaft("S3", -20, 8.0, lower=2, upper=3)]
    x = _run(build_network([], links, pages), [_n("UP", 0, 1), _n("DN", 0, 3)], pages=pages)
    assert x.ner_key == "DN" and x.evaluation.effective_distance_m == pytest.approx(12.0)


def test_guard_critical_space_floor_ner_noncritical_is_not_floor():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("HERE", -149, 2, crit=False)], crit=True)
    assert x.ner_key == "UP"


def test_guard_pin_to_floor_ner_out_of_limit_stays_out_of_limit():
    net = build_network([], [_shaft("S1", 20, 8.0)], P)
    x = _run(net, [_n("UP", 0, 1), _n("FAR", -950, 2)], pinned={"S": "FAR"})
    assert x.ner_key == "FAR" and x.status is Status.OUT_OF_LIMIT and x.pinned
