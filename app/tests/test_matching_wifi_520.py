"""5.2.0 — the matcher stops tying a whole code family to its first room, the
Wi-Fi points follow their space's room, and the worklist holds still.

Marcelo, 3 Nov 2026, on TEAP × drawing: the TEAP lines ``DOP.001.02``,
``DOP.001.03``, ``DOP.001.04``, ``DOP.001.04-02`` and ``DOP.001.04-03`` were
all tied to ``DOP.001.002 · Chaperone Waiting Room`` although the sheet prints
each of them — "como se vc só procurasse pelas 3 primeiras letras seguido do
ponto e os três primeiros números" — and the Wi-Fi points of those spaces
were then placed in that one room.

Why: the TEAP writes ``02`` where the sheet writes ``002``, so the exact code
never matched; the fallback compared only ``DOP.001``, ignored the name, and
let the order of the list break the tie.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import select

from tests.test_wireless_tie_250 import (DWISA, TMP, _line, _match,  # noqa: F401
                                         _ner, _project, _room, _teap, client)
from app.db import SessionLocal
from app.domain.matching import (MatchMethod, MatchSettings, canonical_code, code_segments,
                                 match_spaces)
from app.domain.pdf_ingest import DetectedSpace
from app.models import AuditLog, Element, SpaceMatchRow, TeapSpace

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"


def _r(code, name, page=11, x=100.0, y=100.0):
    return DetectedSpace(space_code=code, name=name, page=page, x=x, y=y, confidence=0.9)


DOP_SHEET = [
    _r("DOP.001", "Doping Control Station", x=10),
    _r("DOP.001.001", "Reception & Waiting", x=20),
    _r("DOP.001.002", "Chaperone Waiting Room", x=30),
    _r("DOP.001.003", "Office", x=40),
    _r("DOP.001.004-001", "Processing Room", x=50),
    _r("DOP.001.004-002", "Processing Room", x=60),
    _r("DOP.001.004-003", "Processing Room", x=70),
    _r("DOP.001.005-001", "Processing Toilet", x=80),
    _r("DOP.001.005-002", "Processing Toilet", x=90),
    _r("CNW.002", "CNW Contractor Office", page=14),
]

DOP_TEAP = [
    ("k02", "DOP.001.02", "Chaperone Waiting Room", None, None),
    ("k03", "DOP.001.03", "Office", None, None),
    ("k04", "DOP.001.04", "Processing Room", None, None),
    ("k0402", "DOP.001.04-02", "Processing Room 2", None, None),
    ("k0403", "DOP.001.04-03", "Processing Room 3", None, None),
]


# ---------------------------------------------------------------------------
# Codes are compared number by number
# ---------------------------------------------------------------------------

def test_a_code_is_read_number_by_number():
    assert code_segments("DOP.001.04-02") == ("DOP", "1", "4", "2")
    assert canonical_code("DOP.001.02") == canonical_code("DOP.001.002") == "DOP.1.2"
    assert canonical_code("TEC.004-02") == canonical_code("TEC.004.02")
    assert canonical_code("SPT.016.01") != canonical_code("SPT.016.010")
    assert canonical_code(None) is None


def test_the_case_on_marcelos_screen():
    rep = match_spaces(DOP_TEAP, DOP_SHEET)
    got = {m.teap_key: m for m in rep.matches}
    assert got["k02"].best.drawing_space.space_code == "DOP.001.002"
    assert got["k03"].best.drawing_space.space_code == "DOP.001.003"
    assert got["k0402"].best.drawing_space.space_code == "DOP.001.004-002"
    assert got["k0403"].best.drawing_space.space_code == "DOP.001.004-003"
    # the same number written with another width is the same code: signed
    for k in ("k02", "k03", "k0402", "k0403"):
        assert got[k].best.method == MatchMethod.EXACT_CODE
        assert got[k].confirmed, k
        assert "different width" in got[k].best.reason
    # DOP.001.04 is divided on the sheet: the Processing Room no other line holds
    m = got["k04"]
    assert m.best.drawing_space.space_code == "DOP.001.004-001"
    assert m.best.method == MatchMethod.BASE_CODE
    assert m.confirmed is False, "a room the sheet divides is still a person's call"
    # and no two lines share a room
    rooms = [g.best.drawing_space.space_code for g in got.values()]
    assert len(set(rooms)) == len(rooms)


def test_the_order_of_the_drawing_list_does_not_decide():
    rep_a = match_spaces(DOP_TEAP, DOP_SHEET)
    rep_b = match_spaces(DOP_TEAP, list(reversed(DOP_SHEET)))
    pick = lambda rep: {m.teap_key: m.best.drawing_space.space_code for m in rep.matches}
    assert pick(rep_a) == pick(rep_b)


def test_a_literal_code_still_beats_nothing_and_keeps_its_confidence():
    rep = match_spaces([("k", "SPT.016.01", "Team Room", None, None)],
                       [_r("SPT.016.01", "Team Room")])
    assert rep.matches[0].best.confidence == pytest.approx(0.98)


# ---------------------------------------------------------------------------
# Inside a code family: depth, then name, then who already holds the room
# ---------------------------------------------------------------------------

def test_a_neighbouring_code_is_chosen_by_name_and_never_confirmed():
    rep = match_spaces([("k", "SPT.016.03", "Team Room", None, None)],
                       [_r("SPT.016.01", "Storage", x=10), _r("SPT.016.02", "Team Room", x=20)])
    m = rep.matches[0]
    assert m.best.drawing_space.space_code == "SPT.016.02"
    assert m.confirmed is False
    assert "the names agree" in m.best.reason


def test_a_room_held_by_an_exact_line_is_ranked_below_a_free_one():
    rep = match_spaces([("a", "SPT.016.01", "Storage", None, None),
                        ("b", "SPT.016.05", "Storage", None, None)],
                       [_r("SPT.016.01", "Storage", x=10), _r("SPT.016.02", "Storage", x=20)])
    got = {m.teap_key: m.best.drawing_space.space_code for m in rep.matches}
    assert got == {"a": "SPT.016.01", "b": "SPT.016.02"}


def test_a_held_room_is_still_offered_when_nothing_else_fits():
    rep = match_spaces([("a", "SPT.016.01", "Storage", None, None),
                        ("b", "SPT.016.05", "Storage", None, None)],
                       [_r("SPT.016.01", "Storage", x=10)])
    got = {m.teap_key: m for m in rep.matches}
    assert got["b"].best.drawing_space.space_code == "SPT.016.01"
    assert "already tied to SPT.016.01" in got["b"].best.reason
    assert got["b"].confirmed is False


def test_two_lines_of_one_family_spread_over_its_free_rooms():
    rep = match_spaces([("a", "PRS.006", "Press Tribune", None, None),
                        ("b", "PRS.006", "Press Tribune", None, None)],
                       [_r("PRS.006.01", "Press Tribune", x=10),
                        _r("PRS.006.02", "Press Tribune", x=20)])
    rooms = {m.best.drawing_space.space_code for m in rep.matches}
    assert rooms == {"PRS.006.01", "PRS.006.02"}


def test_a_deeper_code_beats_the_family_head():
    rep = match_spaces([("k", "DOP.001.04", "Processing Room", None, None)],
                       [_r("DOP.001", "Doping Control Station"),
                        _r("DOP.001.004-001", "Processing Room")])
    assert rep.matches[0].best.drawing_space.space_code == "DOP.001.004-001"


def test_two_rooms_of_one_name_with_no_code_are_two_rooms_even_on_one_sheet():
    rep = match_spaces([("k", None, "Storage", None, None)],
                       [_r(None, "Storage", x=10), _r(None, "Storage", x=500)])
    m = rep.matches[0]
    assert m.confirmed is False
    assert any("ambiguous" in n for n in m.notes)


def test_the_same_code_on_one_sheet_twice_is_still_one_room():
    rep = match_spaces([("k", "TEC.004", "Technology Storage", None, None)],
                       [_r("TEC.004", "Technology Storage", x=10),
                        DetectedSpace(space_code="TEC.004", name="Technology Storage", page=11,
                                      x=300, y=100, area_label="12 m²", confidence=0.9)])
    m = rep.matches[0]
    assert m.confirmed is True
    assert m.best.drawing_space.area_label == "12 m²"


def test_the_same_name_outranks_a_deeper_code():
    """VPP on the work laptop: HOC.200.02 "Umpire Managers" — the person chose
    HOC.200.001 "Umpire Managers" over HOC.200.002-002 "Umpire Change Room"."""
    rep = match_spaces([("k", "HOC.200.02", "Umpire Managers", None, None)],
                       [_r("HOC.200.001", "Umpire Managers", x=10),
                        _r("HOC.200.002-002", "Umpire Change Room", x=20)])
    m = rep.matches[0]
    assert m.best.drawing_space.space_code == "HOC.200.001"
    assert m.confirmed is False
    assert any("the code goes deeper" in n or "goes deeper" in n for n in m.notes)


def test_the_room_that_contains_the_line_beats_a_neighbour_even_when_held():
    """PRS.001.03 "Photo Work Room" with no room of its own on the sheet: the
    Venue Media Centre (PRS.001) contains it; PRS.001.004 is another room."""
    rep = match_spaces([("vmc", "PRS.001", "Venue Media Centre", None, None),
                        ("k", "PRS.001.03", "Photo Work Room", None, None)],
                       [_r("PRS.001", "Venue Media Centre", x=10),
                        _r("PRS.001.004", "Venue Media Team Office", x=20)])
    got = {m.teap_key: m.best.drawing_space.space_code for m in rep.matches}
    assert got == {"vmc": "PRS.001", "k": "PRS.001"}


def test_a_decision_that_disagrees_with_an_exact_code_is_marked(client):
    pid = _project("match-520-elsewhere")
    first = _room(pid, "TEC.520-001", "RES OVR", 100, 100)
    _room(pid, "TEC.520-002", "RES OVR", 300, 100)
    key = _teap(pid, "TEC.520.02", "RES On Venue Results (OVR)", [_line("PC", 1, 1, "PC", 1)])
    _match(client, pid)
    rows = client.get(f"/api/projects/{pid}/matches").json()
    row = next(r for r in rows if r["teap_code"] == "TEC.520.02")
    assert row["drawing_code"] == "TEC.520-002" and row["confirmed"] and not row["exact_elsewhere"]
    _choose(client, pid, key, first)          # what the old matcher had proposed and was confirmed
    _match(client, pid)
    row = next(r for r in client.get(f"/api/projects/{pid}/matches").json()
               if r["teap_code"] == "TEC.520.02")
    assert row["drawing_code"] == "TEC.520-001", "your decision is kept"
    assert row["exact_elsewhere"].startswith("TEC.520-002 · RES OVR")


# ---------------------------------------------------------------------------
# A Wi-Fi point follows its space to the room it is tied to now
# ---------------------------------------------------------------------------

def _points(pid, key):
    with SessionLocal() as db:
        return list(db.execute(select(Element).where(
            Element.project_id == pid, Element.kind == "WIRELESS",
            Element.space_key == key).order_by(Element.id)).scalars())


def _choose(client, pid, key, room_id):
    with SessionLocal() as db:
        mid = db.execute(select(SpaceMatchRow.id).join(
            TeapSpace, TeapSpace.id == SpaceMatchRow.teap_space_id).where(
            TeapSpace.project_id == pid, TeapSpace.space_key == key)).scalar_one()
    r = client.patch(f"/api/projects/{pid}/matches/{mid}",
                     json={"drawing_space_id": room_id, "justification": "room chosen"})
    assert r.status_code == 200, r.text


def _place(client, pid):
    r = client.post(f"/api/projects/{pid}/wireless/place-from-teap")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_point_nobody_dragged_follows_its_space_to_the_new_room(client):
    pid = _project("wifi-520-follow")
    wrong = _room(pid, "DOP.520.002", "Chaperone Waiting Room", 100, 100)
    right = _room(pid, "DOP.520.004", "Processing Room", 900, 700)
    key = _teap(pid, "DOP.520.03", "Processing Room", [_line(DWISA, 1, 1, "DWISA", 1)])
    _ner(pid, "NER_20.1", 500, 500)
    _match(client, pid)
    _choose(client, pid, key, wrong)          # what the old matcher did
    assert _place(client, pid)["placed"] == 1
    (e,) = _points(pid, key)
    assert abs(e.x - 100) < 100 and abs(e.y - 100) < 100
    _choose(client, pid, key, right)          # the person corrects the room
    r = _place(client, pid)
    assert r["moved_to_room"] == 1 and r["placed"] == 0
    (e,) = _points(pid, key)
    assert abs(e.x - 900) < 100 and abs(e.y - 700) < 100
    with SessionLocal() as db:
        a = db.execute(select(AuditLog).where(
            AuditLog.project_id == pid, AuditLog.action == "wireless.followed_room")).scalars().one()
        assert a.payload["from"]["x"] < 300
    # again: nothing moves
    assert _place(client, pid)["moved_to_room"] == 0


def test_a_point_the_person_dragged_stays_and_is_reported(client):
    pid = _project("wifi-520-dragged")
    wrong = _room(pid, "DOP.521.002", "Chaperone Waiting Room", 100, 100)
    right = _room(pid, "DOP.521.004", "Processing Room", 900, 700)
    key = _teap(pid, "DOP.521.03", "Processing Room", [_line(DWISA, 1, 1, "DWISA", 1)])
    _ner(pid, "NER_20.1", 500, 500)
    _match(client, pid)
    _choose(client, pid, key, wrong)
    _place(client, pid)
    (e,) = _points(pid, key)
    r = client.patch(f"/api/projects/{pid}/elements/{e.id}", json={"x": 150.0, "y": 160.0})
    assert r.status_code == 200, r.text
    _choose(client, pid, key, right)
    r = _place(client, pid)
    assert r["moved_to_room"] == 0 and r["away_kept"] == 1
    (e,) = _points(pid, key)
    assert (e.x, e.y) == (150.0, 160.0)


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------

def _src(rel):
    return (SRC / rel).read_text(encoding="utf-8")


def test_wifi_is_its_own_step_after_demand():
    app = _src("App.tsx")
    order = [app.index(f'key: "{k}"') for k in ("matching", "demand", "wifi", "exceptions")]
    assert order == sorted(order)
    assert '{ key: "wifi", label: "Wi-Fi", step: "7"' in app
    panels = _src("pages/panels.tsx")
    scale = panels[panels.index("export function ScalePanel"):panels.index("function SpaceSelect")]
    assert "placeAccessPoints" not in scale and "Place Wi-Fi from the TEAP" not in scale
    wifi = panels[panels.index("export function WifiPanel"):]
    assert "placeAccessPoints" in wifi and "serveWireless" in wifi and "api.analyse" in wifi
    demand = panels[panels.index("export function DemandPanel"):panels.index("function exKind")]
    assert "serveWireless(" not in demand


def test_the_worklist_holds_still():
    panels = _src("pages/panels.tsx")
    body = panels[panels.index("export function MatchingPanel"):panels.index("export function DemandPanel")]
    assert "setFrozen" in body and "Re-sort" in body
    assert 'className="space-cell"' in body and "row-status" in body
    assert "suggestRooms(m, spaces)" in body
    css = _src("styles.css")
    assert ".banner.jobbar" in css and "position: fixed" in css[css.index(".banner.jobbar"):]
    job = _src("components/JobBar.tsx")
    assert "DONE_LINGER_MS" in job and '"finished"' in job


def test_the_project_package_card_is_below_the_list():
    panels = _src("pages/panels.tsx")
    body = panels[panels.index("export function ProjectsPanel"):panels.index("export function SetupPanel")]
    assert body.index('t("Projects")}</h2>') < body.index('t("Import a project package")}</h2>')


# ---------------------------------------------------------------------------
# 5.2.1 — an update without administrator rights over an administrator's tasks
# ---------------------------------------------------------------------------

def _txt(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


# setup.bat stays in the package; an installed copy does not carry it.
_needs_setup = pytest.mark.skipif(not (ROOT / "setup.bat").exists(),
                                  reason="setup.bat is not part of an installed copy")


@_needs_setup
def test_setup_checks_the_tasks_before_changing_anything():
    s = _txt("setup.bat")
    probe = s.index("task-check.ps1\" -Probe")
    assert probe < s.index(":update\n"), "the probe runs before the update touches anything"
    assert "registered by an" in s and "Run as" in s and "/enable" in s
    # an administrator's task is left alone, and the watchdog kept away meanwhile
    assert 'if defined TASKS_LOCKED set "HAD_WD="' in s
    assert '%TARGET%.updating' in s
    assert "task-share.ps1" in s
    assert "may NOT start by itself" in s


def test_autostart_says_done_only_when_both_tasks_are_enabled():
    a = _txt("autostart.bat")
    v = a.index("task-check.ps1\" -Verify")
    assert v < a.index("echo  Done. The server")
    assert "NOT DONE" in a
    chk = _txt("tools/task-check.ps1")
    assert "Settings.Enabled" in chk and "exit 4" in chk and "exit 3" in chk
    wd = _txt("tools/watchdog.ps1")
    assert '"$root.updating"' in wd


def test_people_are_sent_to_localhost():
    for rel in ("setup.bat", "autostart.bat", "tools/server-start.cmd", "install.bat",
                "INSTALL.txt", "setup.command", "mac/start.command"):
        if not (ROOT / rel).exists():
            continue                    # an installed copy carries one platform's scripts
        t = _txt(rel)
        opened = [ln for ln in t.splitlines()
                  if "127.0.0.1:" in ln and not any(k in ln for k in
                     ("curl", "PROBEURL", "REM", "#", "ping -n"))]
        assert not opened, (rel, opened)
    if (ROOT / "setup.bat").exists():
        assert 'set "URL=http://localhost:8000"' in _txt("setup.bat")


def test_the_new_scripts_ship():
    if not (ROOT / "tools/make_package.py").exists():
        pytest.skip("the packager is not part of an installed copy")
    mp = _txt("tools/make_package.py")
    assert '"tools/task-check.ps1", "tools/task-share.ps1"' in mp
    assert '"task-check.ps1", "task-share.ps1"' in mp
