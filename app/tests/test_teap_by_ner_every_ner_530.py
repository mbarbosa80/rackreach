""""TEAP by NER" lists every NER, also one that serves no space yet (5.3.0 rc5).

Marcelo, 4 Nov 2026: a NER with 0 ports was missing from "TEAP by NER" while
"NERs and ports" listed it. On his 15 projects that was 75 NERs on 13 of them.
"""

from __future__ import annotations

import openpyxl

from app.domain import reports as R


def _line(code, name, pcode, desc, ner, ports):
    r = [None] * 17
    r[0], r[1], r[6], r[7], r[10], r[16] = code, name, pcode, desc, ner, ports
    return tuple(r)


def _sheet(rows, crit):
    wb = openpyxl.Workbook()
    ws = wb.active
    R._write_teap_by_ner(ws, rows, crit)
    return [r for r in ws.iter_rows(values_only=True)]


def test_a_ner_serving_nothing_is_listed_with_a_zero_total():
    rows = _sheet([_line("A.1", "Room A", "DWISA", "Wireless", "NERc_01.1", 2)],
                  {"NERc_01.1": True, "NER_20.1": False})
    listed = {r[0] for r in rows[1:] if r and r[0]}
    assert "NER_20.1" in listed
    mine = [r for r in rows if r and r[0] == "NER_20.1"]
    assert [r[5] for r in mine] == [getattr(R, "NO_SPACE_SERVED", "?"), "NER_20.1 total"]
    assert mine[0][6] == 0 and mine[0][1] == "no"
    # the NER with ports is unchanged and comes first (natural order)
    order = [r[0] for r in rows[1:] if r and r[0] and str(r[5]).endswith(" total")
             and r[5] == f"{r[0]} total"]
    assert order == ["NERc_01.1", "NER_20.1"] or order == ["NER_20.1", "NERc_01.1"]


def test_pending_stays_last_and_no_ner_is_listed_twice():
    rows = _sheet([_line("A.1", "Room A", "LAPTP", "Laptop", "NER_21.1", 1),
                   _line("B.1", "Room B", "LAPTP", "Laptop", None, 3)],
                  {"NER_21.1": False, "NER_22.1": False})
    totals = [r[5] for r in rows if r and r[5] and str(r[5]).endswith(" total")
              and r[0] and r[5] == f"{r[0]} total"]
    assert totals.count("NER_21.1 total") == 1 and totals.count("NER_22.1 total") == 1
    assert totals[-1] == f"{R.PENDING_NER_LABEL} total"
