"""5.7.0 - the open findings of the 5.3.0-5.6.0 testers, fixed (owner, 16 Nov
2026: "faz tudo"). Each test failed on 5.6.0.

F4  linking a NER's last marker to another NER left a live NER with no marker;
F5  a new Wi-Fi point that got a deleted point's number inherited its drags;
F6  lines of another venue were filed under "no room on the drawing";
F8  a book that cannot be marked up answered a bare 500;
F9  /overlaps answered 500 with no TEAP;
F10 a venue written "S Y N" was dropped by the filter "SYN", in silence;
F11 a Wi-Fi decision on a space gone from the TEAP showed its raw key;
R7-F6  POST /elements took NER names that rename refuses;
R7-F7  the workbook printed an apostrophe before a formula-like name;
R7-F8  "1_5", "5e1", Arabic-Indic digits and TRUE were read as numbers;
R7-F9  values an earlier version stored kept their effect until a re-import;
R7-F10 two lines of 1e308 summed to infinity;
R7-F11 the workbook never said "TEAP data error".
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import openpyxl
import pytest

from app.db import SessionLocal
from tests.test_ner_register import _ners, client, samples  # noqa: F401
from tests.test_separate_522 import _two_drawings
from tests.test_tester_findings_530 import _demand, _line, _project_with_drawing


def _errors(d):
    return [w for w in d.warnings if w.startswith("TEAP data error")]


# ---- R7-F8 / R7-F10: numbers --------------------------------------------------

@pytest.mark.parametrize("raw", ["1_5", "5e1", "٣", "1e-300", True, "0x10"])
def test_r7_f8_a_count_is_written_with_ascii_digits(raw):
    d = _demand([_line("N.1", 1, raw), _line("N.1", 2, 2)])["N.1"]
    assert d.total_ports == 2 + (1 if raw is True else 0) or d.total_ports == 2, d.total_ports
    assert _errors(d), d.warnings


def test_r7_f8_plain_numbers_still_read():
    for raw, want in (("3", 3), (" 2.5 ", 2.5), ("1,234", 1234), (4, 4), (2.0, 2)):
        assert _demand([_line("N.2", 1, raw)])["N.2"].total_ports == want, raw


def test_r7_f10_an_implausible_value_is_a_data_error_not_infinity():
    d = _demand([_line("N.3", 1, 1e308), _line("N.3", 1, 1e308), _line("N.3", 2, 2)])["N.3"]
    assert d.total_ports == 2
    assert len(_errors(d)) == 2 and "more than 100,000" in _errors(d)[0]


# ---- R7-F9: values stored by an earlier version ---------------------------------

def test_r7_f9_stored_wrong_values_are_repaired_once():
    from app import migrate
    from app.db import init_db
    from app.models import Project, TeapFile, TeapSpace
    init_db()
    with SessionLocal() as db:
        p = Project(name=f"f9-570-{os.urandom(3).hex()}", venue_code="TST")
        db.add(p)
        db.flush()
        tf = TeapFile(project_id=p.id, filename="t.xlsx", stored_path="t.xlsx")
        db.add(tf)
        db.flush()
        t = TeapSpace(project_id=p.id, teap_file_id=tf.id, space_key="TST|A.1", space_code="A.1", space_name="A",
                      total_ports=-1.0, demand_type="general",
                      lines=[{"row": 2, "qty": 4, "ports": 4}, {"row": 3, "qty": -5, "ports": -5}],
                      warnings=[])
        db.add(t)
        db.commit()
        assert migrate.teap_values_570(db, p) == {"teap_spaces_repaired": 1}
        db.refresh(t)
        assert t.total_ports == 4 and any(w.startswith("TEAP data error") for w in t.warnings)
        assert migrate.teap_values_570(db, p) == {"teap_spaces_repaired": 0}


# ---- R7-F11 and F6: the workbook ---------------------------------------------------

def _workbook(tmp_path, demands, coverage, venue_code="TST"):
    from app.domain.distance import DistanceRules
    from app.domain.reports import export_workbook
    out = tmp_path / "w.xlsx"
    export_workbook(str(out), coverage=coverage, ners=[], demands=demands,
                    rules=DistanceRules(), project_name="p", scale_for_page={},
                    venue_code=venue_code)
    return openpyxl.load_workbook(str(out))


def _demand_row(key, name, ports, warnings=()):
    return SimpleNamespace(space_key=key, space_name=name, space_code=key.split("|")[-1],
                           total_ports=ports, warnings=list(warnings), lines=[],
                           breakdown=lambda: [])


def test_r7_f11_the_workbook_lists_the_teap_data_errors(tmp_path):
    from app.domain.coverage import CoverageResult
    d = _demand_row("TST|A.1", "Room A", 0, ["TEAP data error: negative quantity (-5) on row 3"])
    wb = _workbook(tmp_path, [d], CoverageResult(assignments=[], ner_load={}, exceptions=[],
                                                 uncovered=[]))
    rows = [[c.value for c in r] for r in wb["Exceptions"].iter_rows(min_row=2)]
    assert any(r[0] == "TST|A.1" and r[3] == "TEAP data error" for r in rows), rows


def test_f6_another_venues_line_is_said_so(tmp_path):
    from app.domain.coverage import Assignment, CoverageResult
    from app.domain.distance import DemandType, Status
    from app.domain.geometry import Area, Point
    rows = []
    for key in ("TST|A.1", "OTH|B.1"):
        area = Area(key=key, name=key, page=1, label_point=Point(0, 0, 1),
                    meta={"unplaced": True})
        rows.append(Assignment(space_key=key, space_name=key, area=area,
                               demand_type=DemandType.GENERAL, ports_required=2,
                               ner_key=None, ner_label=None, ner_critical=None, evaluation=None,
                               status=Status.NOT_ANALYSABLE, match_confirmed=False))
    cov = CoverageResult(assignments=rows, ner_load={}, exceptions=[], uncovered=[])
    wb = _workbook(tmp_path, [_demand_row("TST|A.1", "A", 2), _demand_row("OTH|B.1", "B", 2)], cov)
    text = " | ".join(str(c.value) for r in wb["Reconciliation"].iter_rows() for c in r if c.value)
    assert "another venue (OTH)" in text, text
    assert "no room on the drawing" in text


# ---- R7-F7 --------------------------------------------------------------------------

def test_r7_f7_a_formula_like_name_reads_as_typed_and_stays_text(tmp_path):
    from app.domain import reports as R
    wb = openpyxl.Workbook()
    R._write_sheet(wb.active, ["NER"], [["=1+"], ["@x"]])
    R._order_and_colour_sheets(wb)
    wb.save(str(tmp_path / "q.xlsx"))
    ws = openpyxl.load_workbook(str(tmp_path / "q.xlsx")).active
    assert [(c.value, c.data_type, c.quotePrefix) for c in ws["A"][1:]] == [
        ("=1+", "s", True), ("@x", "s", True)]


# ---- F10 ------------------------------------------------------------------------------

def test_f10_the_venue_filter_ignores_spaces_and_counts_the_rest(tmp_path):
    from app.domain.teap_import import read_sheet
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TEAP"
    ws.append(["Venue Code", "Space Code", "Space Name", "Product", "Qty"])
    ws.append(["S Y N", "A.1", "Room A", "X", 1])
    ws.append(["syn", "A.2", "Room B", "X", 1])
    ws.append(["OTH", "B.1", "Room C", "X", 1])
    path = tmp_path / "t.xlsx"
    wb.save(str(path))
    imp = read_sheet(str(path), "TEAP", 1, {"venue_code": "Venue Code", "space_code": "Space Code",
                                             "space_name": "Space Name", "product_code": "Product",
                                             "quantity": "Qty"}, venue_filter="SYN")
    assert [r.values["space_code"] for r in imp.rows] == ["A.1", "A.2"]
    assert any("1 row(s) of other venues left out" in w for w in imp.warnings), imp.warnings


# ---- API: F9, R7-F6, F4, F5, F11 --------------------------------------------------

def test_f9_overlaps_without_a_teap_says_why(client, samples):
    pid = _project_with_drawing(client, samples, f"570 F9 {os.urandom(3).hex()}", "TF9")
    r = client.get(f"/api/projects/{pid}/overlaps")
    assert r.status_code == 422, (r.status_code, r.text[:200])


@pytest.mark.parametrize("bad", ["=1+", "@NER", "NER\x07bell", "N" * 81, "---"])
def test_r7_f6_a_ner_placed_by_hand_takes_the_rename_rules(client, samples, bad):
    pid = _project_with_drawing(client, samples, f"570 R7F6 {os.urandom(3).hex()}", "TR6")
    r = client.post(f"/api/projects/{pid}/elements",
                    json={"page": 1, "kind": "NER_NONCRITICAL", "label": bad, "x": 50, "y": 50})
    assert r.status_code == 422, (bad, r.status_code, r.text[:200])


def test_f4_linking_the_last_marker_away_retires_the_ner(client, samples):
    from app.models import NerAsset
    pid, row, e1, e2 = _two_drawings(client, samples, f"570 F4 {os.urandom(3).hex()}", "TF4")
    other = next(x for x in _ners(client, pid) if x["id"] != row["id"])
    for e in (e1, e2):
        r = client.post(f"/api/projects/{pid}/occurrences/{e}/link",
                        json={"asset_id": other["id"], "status": "confirmed"})
        assert r.status_code == 200, r.text
    with SessionLocal() as db:
        old = db.get(NerAsset, row["id"])
        assert old.deleted_at is not None and "last marker" in (old.deleted_reason or "")
    assert row["id"] not in [x["id"] for x in _ners(client, pid) if x.get("is_live") is not False]


def test_f5_a_deleted_points_drags_do_not_follow_its_number():
    from app import services as S
    from app.db import init_db
    from app.models import AuditLog, Project
    init_db()
    with SessionLocal() as db:
        p = Project(name=f"f5-570-{os.urandom(3).hex()}", venue_code="TST")
        db.add(p)
        db.flush()
        for action in ("element.moved", "element.deleted"):
            db.add(AuditLog(project_id=p.id, action=action, entity="element", entity_id=777,
                            actor="t", payload={}))
        db.add(AuditLog(project_id=p.id, action="element.moved", entity="element", entity_id=778,
                        actor="t", payload={}))
        db.commit()
        assert S.wifi_hand_moved(db, p.id) == {778}


def test_f11_a_space_gone_from_the_teap_is_named_in_words():
    from app import services as S
    from app.db import init_db
    from app.models import Project
    init_db()
    with SessionLocal() as db:
        p = Project(name=f"f11-570-{os.urandom(3).hex()}", venue_code="TST", wifi_declined=["TST|SPT.200.01"])
        db.add(p)
        db.commit()
        out = S.wifi_decisions(db, p)
        assert out["deleted"][0]["space_name"] == "SPT.200.01 - no longer in the TEAP"
