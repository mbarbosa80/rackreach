"""Findings of the independent tester on 5.3.0-rc5, fixed in rc6.

N1 "nan" in the TEAP made the whole import fail; N2 "inf"/"1e400" made a
space's ports null; N3 a space whose every line was wrong vanished as "no
port requested"; N4 a shaft foot at 1e200 broke every analysis; N5 a control
character in a NER name broke the workbook; N6 a NER name starting with "="
became a formula; N7/N8 Separate names; N9/N10 coordinates and NaN on the API;
N12 "1,5" read as 15. Each test failed on rc5's code.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import openpyxl

from app.domain.consolidation import normalise_label
from tests.test_ner_register import _ners, client, samples  # noqa: F401
from tests.test_separate_522 import _two_drawings
from tests.test_tester_findings_530 import _demand, _line, _project_with_drawing


def _post(client, url, body):
    """JSON with NaN / Infinity literals, the way a browser or requests sends
    them (httpx refuses to encode them, so the body is written by hand)."""
    import json
    return client.post(url, content=json.dumps(body),
                       headers={"content-type": "application/json"})


def _errors(d):
    return [w for w in d.warnings if w.startswith("TEAP data error")]


# ---- N1 / N2 / N12 -----------------------------------------------------------

def test_n1_nan_is_a_data_error_not_a_crash():
    d = _demand([_line("A.1", "nan", "nan"), _line("A.1", 2, 2)])["A.1"]
    assert d.total_ports == 2 and _errors(d)


def test_n2_infinite_values_never_reach_the_total():
    d = _demand([_line("A.2", 1, "inf"), _line("A.2", "1e400", 3), _line("A.2", 4, 4)])["A.2"]
    assert d.total_ports == d.total_ports and d.total_ports < 1e6     # finite, not NaN
    assert len(_errors(d)) >= 2


def test_n12_a_decimal_comma_is_not_read_as_thousands():
    d = _demand([_line("A.3", 1, "1,5")])["A.3"]
    assert d.total_ports != 15 and _errors(d)
    assert _demand([_line("A.4", 1, "1,234")])["A.4"].total_ports == 1234


# ---- N3 ----------------------------------------------------------------------

def test_n3_a_space_with_a_data_error_stays_on_the_list():
    from app import services as S
    bad = SimpleNamespace(total_ports=0, lines=[], warnings=["TEAP data error: negative quantity"])
    ok0 = SimpleNamespace(total_ports=0, lines=[], warnings=[])
    assert S.in_scope(bad) is True and S.in_scope(ok0) is False
    assert "data error" in (S.scope_note(bad) or "")


# ---- N4 / N9 / N10 -----------------------------------------------------------

def test_n9_a_ner_cannot_be_put_off_the_sheet(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "rc6 N9", "RCC")
    nid = row["id"]
    r = client.patch(f"/api/projects/{pid}/ners/{nid}/position", json={"page": 1, "x": 1e200, "y": 5})
    assert r.status_code == 422
    r = client.patch(f"/api/projects/{pid}/ners/{nid}/position", json={"page": 0, "x": 5, "y": 5})
    assert r.status_code == 422
    r = client.post(f"/api/projects/{pid}/ners/{nid}/move", json={"page": 1, "x": 1e200, "y": 5})
    assert r.status_code == 422


# ---- N5 / N6 -----------------------------------------------------------------

def test_n5_n6_names_that_break_or_inject_into_excel_are_refused(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "rc6 N5", "RCD")
    for bad in ("NER\x07bell", "=1+", '=HYPERLINK("http://example.invalid","x")', "@NER", "---"):
        r = client.post(f"/api/projects/{pid}/ners/{row['id']}/rename", json={"name": bad})
        assert r.status_code in (400, 409, 422), (bad, r.status_code)
        r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach", params={"name": bad})
        assert r.status_code in (400, 409, 422), (bad, r.status_code)


def test_n5_n6_an_old_bad_name_never_reaches_a_cell_as_a_formula():
    from app.domain import reports as R
    wb = openpyxl.Workbook()
    ws = wb.active
    R._write_sheet(ws, ["NER", "Ports"], [['=HYPERLINK("http://x","y")', 1], ["NER\x07bell", 2]])
    ws2 = wb.create_sheet("TEAP by NER")
    r = [None] * 17
    r[0], r[1], r[6], r[7], r[10], r[16] = "A.1", "Room", "X", "x", "=1+", 2
    R._write_teap_by_ner(ws2, [tuple(r)], {"=1+": True})
    R._order_and_colour_sheets(wb)
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    assert re.match(r"^=(SUBTOTAL|SUM|IF)\(", str(cell.value)), cell.value
    assert ws["A3"].value == "NERbell"


# ---- N7 / N8 -----------------------------------------------------------------

def test_n7_a_name_the_register_calls_the_same_is_refused(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "rc6 N7", "RCE")
    r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach", params={"name": "NER 061"})
    assert r.status_code == 409, r.text[:200]


def test_n8_an_empty_name_is_always_the_next_name_of_the_series(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "rc6 N8", "RCF")
    # The tester's case: a marker whose own label is not the group's name
    # ("tmp0", or a removed NER's name) kept that label on rc5.
    from app.db import SessionLocal
    from app.models import Element
    with SessionLocal() as db:
        db.get(Element, e2).label = "tmp0"
        db.commit()
    r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach")
    assert r.status_code == 200
    assert re.fullmatch(r"NERc?_\d+\.1", r.json()["name"]), r.json()["name"]
    names = [normalise_label(x["display_name"]) for x in _ners(client, pid) if x.get("is_live", True)]
    assert len(names) == len(set(names))


# ---- N11 (rc7) - the rule holds through a shaft ------------------------------

def _n11(shaft_m, foot_x):
    from app.domain.coverage import NER, compute_coverage
    from app.domain.distance import DemandType, DistanceRules
    from app.domain.geometry import Point
    from app.domain.pdf_ingest import ElementKind
    from app.domain.routes import VerticalLink, build_network
    from tests.test_release_530 import DECI, _a

    def n(key, x, page):
        return NER(key=key, label=key, kind=ElementKind.NER_CRITICAL,
                   point=Point(x, 0, page), critical=True, total_ports=None)
    up = n("UP", 0, 1)                 # the NER on the other level
    here = n("HERE", -149, 2)          # 14.9 m away on the space's own floor
    net = build_network([], [VerticalLink("S1", Point(foot_x, 0, 1), Point(foot_x, 0, 2), shaft_m)],
                        {1: DECI, 2: DECI})
    res = compute_coverage(areas=[(_a("S", 0, 0, page=2), 2.0, DemandType.GENERAL, None, True)],
                           ners=[up, here], scale_for_page={1: DECI, 2: DECI},
                           rules=DistanceRules(), network=net)
    return res.assignments[0]


def test_n11_a_shaft_that_is_mostly_lead_in_does_not_take_the_space():
    x = _n11(4.0, 120)                 # 4 m shaft, 12 m + 12 m of lead-in
    assert x.ner_key == "HERE", (x.ner_key, x.reasons)


def test_n11_a_real_shaft_route_still_serves_across_levels():
    x = _n11(8.0, 20)                  # 8 m shaft, 2 m + 2 m of lead-in
    assert x.ner_key == "UP", (x.ner_key, x.reasons)
