"""Findings of the independent tester on 5.3.0-rc2, fixed in rc4.

F1 - *Separate* with an empty name gave the new NER the group's own name:
     two live NERs with one name, two rows in "NERs and ports". The 5.2.2
     test only checked that some name came back.
F3 - a route corner at 1e200 was saved (201) and every analysis and workbook
     of the project failed with OverflowError until the route was deleted;
     page 0 and zero-length segments were accepted too.
Each test failed before its fix.
"""

from __future__ import annotations

from app.domain.consolidation import normalise_label
from tests.test_ner_register import _ners, client, samples  # noqa: F401
from tests.test_separate_522 import _two_drawings


def test_f1_separate_with_an_empty_name_never_keeps_the_groups_name(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "Tester 530 F1", "TFA")
    r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach")
    assert r.status_code == 200, r.text
    assert normalise_label(r.json()["name"]) != normalise_label(row["display_name"])
    live = [x for x in _ners(client, pid) if x.get("is_live", True)]
    names = [normalise_label(x["display_name"]) for x in live]
    assert len(names) == len(set(names)), names


def _project_with_drawing(client, samples, name, code):
    pid = client.post("/api/projects", json={"name": name, "venue_code": code}).json()["id"]
    with open(samples["pdf"], "rb") as fh:
        client.post(f"/api/projects/{pid}/drawings",
                    files={"file": ("plan.pdf", fh, "application/pdf")})
    return pid


# ---------------------------------------------------------------------------
# F2 (rc5) - a value the TEAP got wrong never moves a port. Marcelo agreed on
# 4 Nov 2026: count it as 0 and list the space in Exceptions. His three real
# TEAPs (20,056 lines each) have no negative value; this is a guard.
# ---------------------------------------------------------------------------

def _demand(rows):
    from app.domain.demand import DemandRuleSet, compute_demand
    from app.domain.teap_import import TeapImport, TeapRow
    imp = TeapImport(source="t.xlsx", sheet="s", mapping={},
                     rows=[TeapRow(row_number=i + 2, sheet="s", values=v)
                           for i, v in enumerate(rows)])
    return {d.space_code: d for d in compute_demand(imp, DemandRuleSet(), product_ports={})}


def _line(code, qty, ports):
    return {"venue_code": "SYN", "space_code": code, "space_name": code,
            "product_code": "X", "quantity": qty, "network_ports": ports}


def test_f2_a_negative_line_never_takes_ports_away_and_is_reported():
    d = _demand([_line("TEC.103", 4, 4), _line("TEC.103", -5, -5)])["TEC.103"]
    assert d.total_ports == 4
    errs = [w for w in d.warnings if w.startswith("TEAP data error")]
    assert errs and "negative" in errs[0] and "row 3" in errs[0]


def test_f2_a_quantity_that_is_not_a_number_is_reported():
    d = _demand([_line("BRS.050", "three", "n/a"), _line("BRS.050", 2, 2)])["BRS.050"]
    assert d.total_ports == 2
    assert any("is not a number" in w for w in d.warnings)


def test_f2_ordinary_lines_carry_no_data_error():
    d = _demand([_line("A.1", 3, 3), _line("A.1", 0, 0)])["A.1"]
    assert d.total_ports == 3
    assert not [w for w in d.warnings if w.startswith("TEAP data error")]


# ---------------------------------------------------------------------------
# F7 (rc5) - a route counts only when it is mostly traced. Marcelo, 4 Nov
# 2026: at least 5 m traced and more traced than the straight lead-ins.
# ---------------------------------------------------------------------------

def _f7(tray):
    from app.domain.coverage import compute_coverage
    from app.domain.distance import DemandType, DistanceRules
    from app.domain.routes import build_network
    from tests.test_release_530 import DECI, _a, _n, _seg
    a, b = _n("A", 120, 0), _n("B", -80, 0)        # A 12 m, B 8 m in a straight line
    net = build_network([_seg(*tray)], [], {1: DECI})
    res = compute_coverage(areas=[(_a("S", 0, 0), 2.0, DemandType.GENERAL, None, True)],
                           ners=[a, b], scale_for_page={1: DECI}, rules=DistanceRules(),
                           network=net)
    return res.assignments[0]


def test_f7_a_phantom_route_with_almost_nothing_traced_does_not_count():
    x = _f7(((60, 100), (65, 100)))                 # 0.5 m traced, ~23 m lead-in
    assert x.ner_key == "B", x.reasons
    assert any("does not count" in s for s in x.evaluation.assumptions) or \
        not any("not compared" in r for r in x.reasons)


def test_f7_more_lead_in_than_traced_does_not_count():
    x = _f7(((60, 100), (120, 100)))                # 6 m traced, ~21.7 m lead-in
    assert x.ner_key == "B", x.reasons


def test_f7_a_real_route_still_decides():
    from tests.test_release_530 import DECI, _a, _bms_like  # noqa: F401
    from app.domain.coverage import compute_coverage
    from app.domain.distance import DemandType, DistanceMethod, DistanceRules
    a, b, net = _bms_like()                         # 40 m traced, 0.4 m lead-in
    res = compute_coverage(areas=[(_a("S", 0, 0), 4.0, DemandType.GENERAL, None, True)],
                           ners=[a, b], scale_for_page={1: DECI}, rules=DistanceRules(),
                           network=net)
    assert res.assignments[0].ner_key == "A"
    assert res.assignments[0].evaluation.method is DistanceMethod.ROUTE
