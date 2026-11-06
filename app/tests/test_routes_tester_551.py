"""5.5.1-rc2 - the independent tester's findings on 5.5.1-rc1.

N1  a 1 m sleeve whose feet are nearer than an 8 m riser's made the space
    "not analysable": the 5 m rule was judged on the shortest connection
    only, and that one does not count. It is now judged on the shortest
    connection that counts; a shorter one that does not is named.
N2  two straight lead-ins meeting at a bare shaft foot (0 m traced) were a
    "route": the same-floor F2 case then never saw the way round the floor
    above, and its "not used" note was missing.
N4  traced and lead-in lengths are printed to two decimals everywhere, and a
    route counts exactly when the printed traced length is 5.00 m or more.

Each test failed on 5.5.1-rc1 (qa/NOTES-551.md, rc2 section).
"""

from __future__ import annotations

import re

import pytest

from app.domain.coverage import route_counts
from app.domain.distance import DistanceMethod, Status
from app.domain.geometry import Point
from app.domain.routes import build_network
from tests.test_routes_shafts_551 import (DECI, _a, _n, _run, _seg, _shaft, _text,
                                          _up_here)


# ---- N1 ----------------------------------------------------------------------

def _riser_and_sleeve(sleeve=True):
    links = [_shaft("RISER-8m", 20, 8.0)]
    if sleeve:
        links.append(_shaft("SLEEVE-1m", 10, 1.0))
    return build_network([], links, {1: DECI, 2: DECI})


def test_n1_a_short_sleeve_never_takes_away_a_counting_riser():
    up, _ = _up_here()
    alone, _ = _run(_riser_and_sleeve(False), [up])
    x, _ = _run(_riser_and_sleeve(), [up])
    assert alone.ner_key == "UP" and alone.evaluation.effective_distance_m == pytest.approx(12.0)
    assert x.ner_key == "UP" and x.status is Status.SERVED, (x.status, x.reasons)
    assert x.evaluation.method is DistanceMethod.ROUTE
    assert x.evaluation.effective_distance_m == pytest.approx(12.0, abs=0.01)
    t = _text(x)
    assert "SLEEVE-1m" in t and "does not count" in t and "RISER-8m" in t, t


def test_n1_the_long_way_never_beats_a_short_way_that_counts():
    """Both connections count: the shorter one is the measurement."""
    net = build_network([], [_shaft("RISER-8m", 20, 8.0), _shaft("LONG", 10, 45.8)],
                        {1: DECI, 2: DECI})
    up, _ = _up_here()
    x, _ = _run(net, [up])
    assert x.evaluation.effective_distance_m == pytest.approx(12.0, abs=0.01)
    assert "does not count" not in _text(x)


def test_n1_r7_f1_stays_fixed_for_two_links_on_the_same_feet():
    """The rc7 F1 case: a 4 m shaft and a 43.8 m one on the same feet. The
    shorter parallel link is the only one kept, it does not count, and HERE
    (14.9 m on the space's floor) keeps the space."""
    for order in ((4.0, 43.8), (43.8, 4.0)):
        net = build_network([], [_shaft(f"S{L:g}", 20, L) for L in order], {1: DECI, 2: DECI})
        up, here = _up_here()
        x, _ = _run(net, [up, here])
        assert x.ner_key == "HERE" and x.evaluation.effective_distance_m == pytest.approx(14.9)


def test_n1_route_options_are_every_connection_shortest_first():
    net = _riser_and_sleeve()
    opts = net.route_options(Point(0, 0, 2), Point(0, 0, 1), DECI)
    assert [o["vertical_links"] for o in opts] == [["SLEEVE-1m"], ["RISER-8m"]]
    assert [round(o["length_m"], 2) for o in opts] == [3.0, 12.0]
    assert net.route_length_m(Point(0, 0, 2), Point(0, 0, 1), DECI) == opts[0]


# ---- N2 ----------------------------------------------------------------------

def _f2_tester():
    """The tester's F2 fixture: HERE 14.9 m away on the space's floor, risers R1
    2 m from the space and R2 2 m from HERE (12.9 m from the space, inside the
    15 m join), joined by a 94.9 m tray on the floor above."""
    tray = [_seg((20, 0), (20, 400), 1), _seg((20, 400), (-129, 400), 1),
            _seg((-129, 400), (-129, 0), 1)]
    return build_network(tray, [_shaft("R1", 20, 4.0), _shaft("R2", -129, 4.0)],
                         {1: DECI, 2: DECI})


def test_n2_lead_ins_meeting_at_a_bare_foot_are_no_route():
    net = _f2_tester()
    r = net.route_length_m(Point(0, 0, 2), Point(-149, 0, 2), DECI)
    assert r is not None and r["traced_m"] > 0 and r["crosses_levels"], r
    assert r["vertical_links"] == ["R1", "R2"]
    assert r["length_m"] == pytest.approx(2 + 4 + 94.9 + 4 + 2, abs=0.05)


def test_n2_the_way_round_another_level_is_named_when_not_used():
    _, here = _up_here()
    x, _ = _run(_f2_tester(), [here])
    assert x.ner_key == "HERE" and x.evaluation.method is DistanceMethod.STRAIGHT_LINE
    assert x.evaluation.effective_distance_m == pytest.approx(14.9)
    assert re.search(r"a route through another level \(via R1, R2\) reaches HERE in "
                     r"106\.9 m.*not used", _text(x)), _text(x)


def test_n2_a_foot_on_a_tray_still_joins():
    """Only a *bare* foot is left out: a shaft foot on a tray end is a node of
    the traced route like any other."""
    net = build_network([_seg((20, 0), (20, -100), 2)], [_shaft("R1", 20, 4.0)],
                        {1: DECI, 2: DECI})
    r = net.route_length_m(Point(0, 0, 2), Point(20, -100, 2), DECI)
    assert r is not None and r["traced_m"] == pytest.approx(10.0)


# ---- N4 ----------------------------------------------------------------------

_LEN = re.compile(r"(\d+\.\d+) m (?:along the traced route|of it is traced|traced against|"
                  r"of straight lead-in|straight lead-in)")


def _two_decimals(text):
    found = _LEN.findall(text)
    assert found, text
    return all(len(v.split(".")[1]) == 2 for v in found)


@pytest.mark.parametrize("units, counts, printed", [(49.95, True, "5.00"),
                                                     (49.94, False, "4.99")])
def test_n4_the_printed_traced_length_and_the_verdict_agree(units, counts, printed):
    net = build_network([_seg((0, 0.7), (units, 0.7), 2)], [], {2: DECI})
    r = net.route_length_m(Point(0, 0, 2), Point(units, 0.7, 2), DECI)
    assert route_counts(r) is counts
    x, _ = _run(net, [_n("N", units, 2, 0.7)])
    t = _text(x)
    assert f"{printed} m" in t and _two_decimals(t), t
    assert (x.evaluation.method is DistanceMethod.ROUTE) is counts


def test_n4_the_shaft_note_uses_two_decimals_for_both_figures():
    net = build_network([], [_shaft("S1", 20, 4.0)], {1: DECI, 2: DECI})
    up, here = _up_here()
    x, _ = _run(net, [up, here])
    t = " | ".join(x.reasons)
    assert "4.00 m of it is traced against 4.00 m of straight lead-in" in t, t


def test_n4_a_counting_route_prints_two_decimals():
    net = build_network([_seg((0, 0), (900, 0), 1)], [], {1: DECI})
    x, _ = _run(net, [_n("N", 1000, 1)], space=_a("S", 0, 0, 1))
    t = _text(x)
    assert "90.00 m along the traced route + 10.00 m straight lead-in" in t, t


# ---- N3 ----------------------------------------------------------------------

def _grid(n, page):
    from app.domain.routes import RouteSegment
    out = []
    for i in range(n + 1):
        for j in range(n):
            out.append(RouteSegment(Point(j * 100, i * 100, page), Point((j + 1) * 100, i * 100, page), page))
            out.append(RouteSegment(Point(i * 100, j * 100, page), Point(i * 100, (j + 1) * 100, page), page))
    return out


def test_n3_joining_routes_projects_only_onto_nearby_segments(monkeypatch):
    """The T-junction pass projected every node onto every segment of its page
    (rc1: 2.2 million projections for the tester's 1,680-segment grid with 200
    shafts, 3.7 s). A box around each segment rules out the far ones first;
    the joins are the same (the network is compared below)."""
    import random
    from app.domain import routes as R
    random.seed(1)
    links = [R.VerticalLink(f"V{k}", Point(random.uniform(0, 2000), random.uniform(0, 2000), 2),
                            Point(random.uniform(0, 2000), random.uniform(0, 2000), 1), 4.0)
             for k in range(200)]
    calls = {"n": 0}
    real = R._project

    def counted(*a):
        calls["n"] += 1
        return real(*a)

    monkeypatch.setattr(R, "_project", counted)
    net = build_network(_grid(20, 1) + _grid(20, 2), links, {1: DECI, 2: DECI})
    assert calls["n"] < 50_000, calls
    # a T-junction in the middle of a corridor still joins
    t = build_network([_seg((0, 0), (200, 0), 1), _seg((100, 2.9), (100, 100), 1)], [], {1: DECI})
    r = t.route_length_m(Point(0, 0, 1), Point(100, 100, 1), DECI)
    assert r is not None and r["traced_m"] == pytest.approx(19.71, abs=0.01)
    assert len(net.graph.edges) >= 1680
