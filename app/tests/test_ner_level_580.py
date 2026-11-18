"""5.8.0 - the NER's level as a field, like indoor / outdoor (two analysts of the
team, 17 Nov 2026), and its column on the NERs sheet."""
from __future__ import annotations

import io
import os

import openpyxl

from tests.test_tester_570 import _full_project, _live_ners
from tests.test_ner_register import client, samples  # noqa: F401


def _ner(client, pid, nid):
    return next(n for n in client.get(f"/api/projects/{pid}/ners").json()["ners"] if n["id"] == nid)


def test_a_level_chosen_by_hand_is_written_as_the_titles_write_it_and_kept(client, samples):
    pid = _full_project(client, samples, f"L580 hand {os.urandom(3).hex()}")
    n = _live_ners(client, pid)[0]
    r = client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": "2"}})
    assert r.status_code == 200, r.text
    got = _ner(client, pid, n["id"])
    assert got["level"] == "LEVEL 02" and got["level_source"] == "manual"
    assert "page_levels" in got
    # a level typed on the NER's sheet afterwards does not overwrite it
    page = got["canonical"]["page"]
    client.post(f"/api/projects/{pid}/pages/{page}/level", json={"level": "05"})
    assert _ner(client, pid, n["id"])["level"] == "LEVEL 02"
    # empty gives it back its sheet's level
    r = client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": ""}})
    assert r.status_code == 200, r.text
    got = _ner(client, pid, n["id"])
    assert got["level_source"] is None
    assert got["level"] in ("LEVEL 05", None)


def test_a_control_character_in_a_level_is_refused(client, samples):
    pid = _full_project(client, samples, f"L580 ctl {os.urandom(3).hex()}")
    n = _live_ners(client, pid)[0]
    r = client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": "02\x07"}})
    assert r.status_code in (400, 409, 422), r.text


def test_the_level_is_a_column_of_the_ners_sheet(client, samples):
    pid = _full_project(client, samples, f"L580 sheet {os.urandom(3).hex()}")
    n = _live_ners(client, pid)[0]
    client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": "Mezzanine"}})
    assert client.post(f"/api/projects/{pid}/analyse").status_code == 200
    r = client.post(f"/api/projects/{pid}/reports/xlsx")
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(client.get(r.json()["download"]).content))
    ws = wb["NERs and ports"]
    head = [c.value for c in ws[1]]
    assert head[head.index("Page") + 1] == "Level", head
    col = head.index("Level")
    rows = {row[0]: row[col] for row in ws.iter_rows(min_row=2, values_only=True) if row[0]}
    assert rows.get(n["display_name"]) == "MEZZANINE", rows
    # the utilisation formula still points at the right columns
    util = ws.cell(row=2, column=head.index("Utilisation %") + 1).value
    assert "I2" in util and "J2" in util, util
