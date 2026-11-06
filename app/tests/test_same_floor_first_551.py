"""5.5.1-rc3 - "same floor first" (owner decision on N11, Marcelo, 6 Nov 2026).

A NER on another level, reached through a shaft or riser route, is a
candidate for a space only when no NER on the space's own level serves it
within the applicable limit (90 m general, 70 m photographers), measured as
the same-level rules measure it (a counting same-level route if there is one,
else the straight line). On the space's own level nothing changes. A NER
set aside this way is named on the row. A NER a person chose (Served by) is
honoured whatever the rule says.

Also R2-1 (tester on rc2): two equally long connections were ordered by
insertion order; the named link could change after a re-import.

Each test failed on 5.5.1-rc2 (qa/NOTES-551.md, rc3 section), except the
guards named there.
"""

from __future__ import annotations

import pytest

from app.domain.coverage import compute_coverage
from app.domain.distance import DemandType, DistanceMethod, DistanceRules, Status
from app.domain.geometry import Point
from app.domain.routes import build_network
from tests.test_routes_shafts_551 import DECI, _a, _n, _run, _seg, _shaft, _text, _up_here

PAGES = {1: DECI, 2: DECI}


def _cov(net, ners, demand=DemandType.GENERAL, pinned=None, critical=None):
    res = compute_coverage(areas=[(_a("S", 0, 0, 2), 2.0, demand, critical, True)],
                           ners=ners, scale_for_page=PAGES, rules=DistanceRules(),
                           network=net, pinned=pinned)
    return res.assignments[0]


def _reasons(x):
    return " | ".join(x.reasons)


# ---- the tester's case (D1 on rc2) -------------------------------------------

def test_the_tester_case_a_long_shaft_never_beats_the_floor():
    """UP by a 43.8 m shaft (feet 1 m away: 45.80 m, counts), a 4 m shaft 2 m
    away (does not count), HERE 14.9 m away on the space's floor: HERE."""
    net = build_network([], [_shaft("S-long", 10, 43.8), _shaft("S-short", 20, 4.0)], PAGES)
    up, here = _up_here()
    x = _cov(net, [up, here])
    assert x.ner_key == "HERE" and x.status is Status.SERVED, (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(14.9)
    assert x.evaluation.method is DistanceMethod.STRAIGHT_LINE
    t = _reasons(x)
    assert "via S-long" in t and "reaches UP" in t and "45.80 m" in t, t
    assert "not used: HERE on this level serves within 90 m" in t, t
    assert "only the NER(s) it reaches were compared" not in t, t


# ---- same level within the limit wins over any cross-level route -------------

@pytest.mark.parametrize("links, up_m", [
    ([("SHORT", 20, 8.0)], 12.0),        # cross-level route shorter than HERE's 14.9 m
    ([("LONG", 10, 85.0)], 87.0),        # longer, still within the limit
])
def test_same_level_within_limit_beats_a_shorter_or_longer_cross_level_route(links, up_m):
    net = build_network([], [_shaft(k, x, L) for k, x, L in links], PAGES)
    up, here = _up_here()
    alone = _cov(net, [up])
    assert alone.ner_key == "UP" and alone.evaluation.effective_distance_m == pytest.approx(up_m)
    x = _cov(net, [up, here])
    assert x.ner_key == "HERE" and x.evaluation.effective_distance_m == pytest.approx(14.9)
    assert "reaches UP" in _reasons(x) and f"{up_m:.2f} m" in _reasons(x), _reasons(x)
    assert all(k != "UP" for k, _ in x.alternatives)


def test_a_same_level_route_still_decides_on_its_own_floor():
    """The 5.3.0 rule on the space's own floor is unchanged: a counting route
    reaches NEAR2 (20 m), so the straight-line HERE (14.9 m) is set aside as
    before - and UP, on another level, too."""
    net = build_network([_seg((0, 0), (200, 0), 2)], [_shaft("SHORT", 20, 8.0)], PAGES)
    up, here = _up_here()
    x = _cov(net, [up, here, _n("NEAR2", 200, 2)])
    assert x.ner_key == "NEAR2" and x.evaluation.method is DistanceMethod.ROUTE, x.reasons
    t = _reasons(x)
    assert "HERE at 14.9 m in a straight line" in t and "reaches UP" in t, t


# ---- same level out of the limit: the other level can serve -------------------

def test_same_level_out_of_limit_lets_a_cross_level_route_serve():
    net = build_network([], [_shaft("SHORT", 20, 8.0)], PAGES)
    up = _n("UP", 0, 1)
    far = _n("FAR", -950, 2)            # 95 m on the space's floor
    x = _cov(net, [up, far])
    assert x.ner_key == "UP" and x.status is Status.SERVED, (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(12.0)
    assert "not used: FAR" not in _reasons(x)


def test_photographers_use_the_70_m_limit():
    """FAR 75 m away on the space's floor: within 90 m, out of 70 m."""
    net = build_network([], [_shaft("SHORT", 20, 8.0)], PAGES)
    up, far = _n("UP", 0, 1), _n("FAR", -750, 2)
    general = _cov(net, [up, far], DemandType.GENERAL)
    assert general.ner_key == "FAR", general.reasons
    assert "FAR on this level serves within 90 m" in _reasons(general)
    photo = _cov(net, [up, far], DemandType.PHOTOGRAPHER)
    assert photo.ner_key == "UP" and photo.evaluation.limit_m == 70, photo.reasons
    assert photo.evaluation.effective_distance_m == pytest.approx(12.0)


def test_a_non_critical_floor_ner_does_not_keep_a_critical_space_from_upstairs():
    """A critical space: a non-critical NER on its floor does not serve it, so
    the critical NER upstairs is a candidate."""
    from app.domain.coverage import NER
    from app.domain.pdf_ingest import ElementKind
    net = build_network([], [_shaft("SHORT", 20, 8.0)], PAGES)
    up = _n("UP", 0, 1)
    here = NER(key="HERE", label="HERE", kind=ElementKind.NER_NONCRITICAL,
               point=Point(-149, 0, 2), critical=False, total_ports=None)
    x = _cov(net, [up, here], critical=True)
    assert x.ner_key == "UP", (x.ner_key, x.reasons)


# ---- Served by ---------------------------------------------------------------

def test_served_by_is_honoured_whatever_the_rule_says():
    net = build_network([], [_shaft("SHORT", 20, 8.0)], PAGES)
    up, here = _up_here()
    x = _cov(net, [up, here], pinned={"S": "UP"})
    assert x.ner_key == "UP" and x.pinned, (x.ner_key, x.reasons)
    assert x.evaluation.effective_distance_m == pytest.approx(12.0)
    y = _cov(build_network([], [_shaft("SHORT", 20, 8.0)], PAGES),
             [_n("UP", 0, 1), _n("FAR", -950, 2)], pinned={"S": "FAR"})
    assert y.ner_key == "FAR" and y.status is Status.OUT_OF_LIMIT and y.pinned


# ---- R2-1 --------------------------------------------------------------------

def test_r2_1_equal_connections_are_ordered_by_link_then_position():
    """Two identical 8 m shafts 2 m left and right of the space: the first
    connection is the same whatever order they were registered in."""
    left, right = _shaft("LEFT", -20, 8.0), _shaft("RIGHT", 20, 8.0)
    seen = set()
    for links in ([left, right], [right, left]):
        net = build_network([], links, PAGES)
        opts = net.route_options(Point(0, 0, 2), Point(0, 0, 1), DECI)
        seen.add(tuple(opts[0]["vertical_links"]))
        assert [o["vertical_links"] for o in opts] == [["LEFT"], ["RIGHT"]]
    assert seen == {("LEFT",)}
    up, _ = _up_here()
    x, _ = _run(build_network([], [right, left], PAGES), [up])
    assert x.evaluation.effective_distance_m == pytest.approx(12.0)
