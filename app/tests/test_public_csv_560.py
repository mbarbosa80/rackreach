"""5.6.0 public edition: the CSV's alert columns carry no internal word
(tester R2-6 on 5.6.0-rc2: the header and the cells said "NER")."""
from __future__ import annotations

import csv
import types

from app.domain import reports


def _row(alert, suggestion):
    a = types.SimpleNamespace(
        space_key="k", space_name="S", area=types.SimpleNamespace(page=1),
        demand_type=types.SimpleNamespace(value="general"), ports_required=2,
        ner_label="RK_20.1", ner_key="n1", pinned=False, ner_critical=False,
        evaluation=None, status=types.SimpleNamespace(value="served"),
        criticality_alert=alert, critical_suggestion=suggestion)
    return types.SimpleNamespace(assignments=[a])


def _read(tmp_path, cov):
    p = reports.export_csv(str(tmp_path / "x.csv"), cov)
    with open(p, encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_public_csv_alert_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("RR_EDITION", "public")
    rows = _read(tmp_path, _row("no_critical_ner_in_limit", None))
    assert rows[0][-1] == "critical_rack_suggested"
    assert rows[1][-2:] == ["no_critical_rack_in_limit", "no critical rack measurable"]


def test_team_csv_alert_columns_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("RR_EDITION", "team")
    rows = _read(tmp_path, _row("no_critical_ner_in_limit", None))
    assert rows[0][-1] == "critical_ner_suggested"
    assert rows[1][-2:] == ["no_critical_ner_in_limit", "no critical NER measurable"]
