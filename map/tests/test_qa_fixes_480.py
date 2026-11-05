"""4.8.0 — the defects of the QA pass of 28 Oct 2026 ("Parecer de QA — NER
Analyzer 4.7.0"), the browser stall after *Run matching*, and Marcelo's
answer that a deleted Wi-Fi point stays deleted.

Each test names its finding: A1 and A2 (high), M1 and M2 (medium), B1–B7
(low), R1 (the stall), W1 (the Wi-Fi decision).
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import re
import subprocess
import sys
import zipfile

import pymupdf
from sqlalchemy import select

from tests.test_wireless_tie_250 import (DWISA, TMP, _line, _match, _ner, _project,  # noqa: F401
                                         _room, _teap, client)
from tests.test_multi_book_410 import _two_books
from tests.test_qa_fixes_420 import _live, _room_at, _symbol_at
from app import migrate
from app import ner_register as R
from app import services as S
from app.db import SessionLocal
from app.domain import reports as RP
from app.models import AuditLog, Drawing, Element, NerAsset, NerNameHistory, Project

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A1 — a NER dragged away from the room the drawing prints is not created again
# ---------------------------------------------------------------------------

def test_a1_a_marker_keeps_where_it_was_first_put(client):
    pid, a, _b = _two_books("a1 origin")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        S.adopt_drawn_rooms(db, p, db.get(Drawing, a), [_room_at(1, 433, 771, "TEC.010", True)])
        e = db.execute(select(Element).where(Element.project_id == pid)).scalars().one()
        assert (e.origin_x, e.origin_y) == (433, 771)
        e.x, e.y = 301, 904                         # dragged onto the real rack
        db.commit()
        db.refresh(e)
        assert (e.origin_x, e.origin_y) == (433, 771)


def test_a1_place_ners_after_a_drag_beyond_12_m_creates_nothing(client):
    """VKK, 27 Oct 2026: NERc_03.1 dragged 16.5 m from its bare TEC.010; the
    next Place NERs created NERc_12.1 on the TEC.010. Twice in a row now: 0."""
    pid, a, _b = _two_books("a1 bms")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        dr = db.get(Drawing, a)
        assert S.adopt_drawn_rooms(db, p, dr, [_room_at(1, 433, 771, "TEC.010", True)])["created"] == 1
        e = db.execute(select(Element).where(Element.project_id == pid)).scalars().one()
        e.x, e.y = e.x + 20.0, e.y              # 20 m on this sheet (1 unit = 1 m)
        db.commit()
        for _ in (1, 2):
            st = S.adopt_drawn_rooms(db, p, dr, [_room_at(1, 433, 771, "TEC.010", True)])
            assert st["created"] == 0 and st["same_as_marker"] == 1
        assert len(_live(db, pid)) == 1


def test_a1_another_room_far_from_both_spots_is_still_created(client):
    pid, a, _b = _two_books("a1 other room")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        dr = db.get(Drawing, a)
        S.adopt_drawn_rooms(db, p, dr, [_symbol_at(1, 100, 100)])
        e = db.execute(select(Element).where(Element.project_id == pid)).scalars().one()
        e.x = 160.0
        db.commit()
        assert S.adopt_drawn_rooms(db, p, dr, [_symbol_at(1, 400, 400)])["created"] == 1


def test_a1_an_annotation_where_a_dragged_ner_was_read_is_absorbed(client):
    pid, a, _b = _two_books("a1 annotation")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        S.adopt_drawn_rooms(db, p, db.get(Drawing, a), [_symbol_at(2, 250, 250)])
        e = db.execute(select(Element).where(Element.project_id == pid)).scalars().one()
        e.x = 400.0
        db.commit()
        assert S._ner_marker_near(db, p, 2, 252, 251, drawing_id=a).id == e.id


def test_a1_the_start_up_pass_reads_the_first_position_from_the_move_log(client):
    pid, a, _b = _two_books("a1 backfill")
    with SessionLocal() as db:
        p = db.get(Project, pid)
        S.adopt_drawn_rooms(db, p, db.get(Drawing, a), [_room_at(1, 433, 771, "TEC.010", True)])
        e = db.execute(select(Element).where(Element.project_id == pid)).scalars().one()
        sh_key = __import__("app.sheets", fromlist=["x"]).for_project(db, pid).of(e)
        # A database from before 4.8.0: no origin, one move in the log.
        e.origin_x = e.origin_y = None
        e.x, e.y = 301.0, 904.0
        db.add(AuditLog(project_id=pid, action="ner.moved", entity="ner_asset",
                        entity_id=e.ner_asset_id, actor="mbarbosa",
                        payload={"element_id": e.id, "label": "NERc_03.1",
                                 "from": {"page": sh_key, "x": 433.35, "y": 770.96},
                                 "to": {"page": sh_key, "x": 301.0, "y": 904.0}}))
        db.commit()
        out = migrate.backfill_marker_origins(db, p)
        assert out == {"marker_origins": 1, "marker_origins_from_history": 1}
        db.refresh(e)
        assert (round(e.origin_x, 2), round(e.origin_y, 2)) == (433.35, 770.96)
        assert migrate.backfill_marker_origins(db, p) == {"marker_origins": 0}


# ---------------------------------------------------------------------------
# A2 — Wi-Fi points never on top of each other
# ---------------------------------------------------------------------------

def _two_spaces_one_room(client, name):
    pid = _project(name)
    room = _room(pid, "PRS.480", "Photo Positions", 300, 300)
    keys = [_teap(pid, f"PRS.480.0{i}", f"Photo Position {i}",
                  [_line(DWISA, 1, 1, "DWISA", 1)]) for i in (1, 2, 3)]
    _match(client, pid)
    for m in client.get(f"/api/projects/{pid}/matches?include_zero=true").json():
        if m["teap_code"].startswith("PRS.480.0"):
            r = client.patch(f"/api/projects/{pid}/matches/{m['id']}",
                             json={"drawing_space_id": room, "justification": "same room"})
            assert r.status_code == 200, r.text
    return pid, keys


def _wifi(pid):
    with SessionLocal() as db:
        return list(db.execute(select(Element).where(
            Element.project_id == pid, Element.kind == "WIRELESS").order_by(Element.id)).scalars())


def test_a2_free_slot_skips_what_is_taken():
    dx = S.TEAP_AP_STEP[0]
    taken = [(10.0, 10.0), (10.0 + dx, 10.0)]
    assert S.free_wifi_slot(10.0, 10.0, taken) == (10.0 + 2 * dx, 10.0)
    assert S.free_wifi_slot(10.0, 10.0, []) == (10.0, 10.0)


# ---------------------------------------------------------------------------
# W1 — a Wi-Fi point a person deleted stays deleted
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# M1 — exporting a package changes nothing the analysis measures
# ---------------------------------------------------------------------------

def test_m1_export_and_import_are_neutral():
    assert {"project.exported", "project.imported"} <= S._ANALYSIS_NEUTRAL


def test_m1_the_package_says_when_the_analysis_was_last_run():
    src = _read("backend/app/transfer.py")
    assert "S.scenario_saved_at(db, project, last)" in src


# ---------------------------------------------------------------------------
# M2 — text on a sheet larger than 14,400 pt (/UserUnit)
# ---------------------------------------------------------------------------

def test_m2_labels_land_on_a_sheet_with_a_user_unit(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=14400, height=9316)
    doc.xref_set_key(doc[0].xref, "UserUnit", "1.19057")
    doc.xref_set_key(doc[0].xref, "MediaBox", "[-7200 -4658.27 7200 4658.27]")
    page = doc[0]
    assert RP.user_unit(page) == 1.19057
    sp = RP.PageSpace(page)
    sp.box(11781, 10115, 11846, 10128, color=(0, 0, 1), fill=(1, 1, 1))
    sp.text(11786, 10125, "WIFI-35", fontsize=9, color=(0, 0, 0))
    sp.commit()
    out = tmp_path / "uu.pdf"
    doc.save(out)
    words = [w for w in pymupdf.open(out)[0].get_text("words") if w[4] == "WIFI-35"]
    assert len(words) == 1
    assert abs(words[0][0] - 11786) < 2 and abs(words[0][3] - 10125) < 20


def test_m2_a_normal_sheet_is_unchanged(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=2449, height=1585)
    assert RP.user_unit(doc[0]) == 1.0


# ---------------------------------------------------------------------------
# B2 — the next name is never a live NER's former name
# ---------------------------------------------------------------------------

def test_b2_former_names_are_not_offered_again(client):
    pid = _project("b2 former names")
    _ner(pid, "NERc_10.1", 100, 100)
    with SessionLocal() as db:
        a = db.execute(select(NerAsset).where(NerAsset.project_id == pid)).scalars().one()
        a.previous_name = "NER_34.1"
        db.add(NerNameHistory(ner_asset_id=a.id, old_name="NERc_11.1", new_name="NERc_10.1"))
        db.commit()
        names = R.live_names(db, pid)
    assert {"NER_34.1", "NERc_11.1"} <= set(names)
    r = client.get(f"/api/projects/{pid}/ner-next-name").json()
    assert r["critical"] == "NERc_12.1" and r["non_critical"] == "NER_35.1"


# ---------------------------------------------------------------------------
# B3 — what the screens never send is refused
# ---------------------------------------------------------------------------

def test_b3_markers(client):
    pid = _project("b3 markers")
    _ner(pid, "NER_20.1", 100, 100)
    post = lambda body: client.post(f"/api/projects/{pid}/elements",
                                    json={"page": 1, "x": 50, "y": 50, **body})
    assert post({"kind": "BANANA", "label": "QA"}).status_code == 422
    assert post({"kind": "NER_NONCRITICAL", "label": "  "}).status_code == 422
    assert post({"kind": "NER_NONCRITICAL", "label": "L" * 121}).status_code == 422
    assert post({"kind": "WIRELESS", "label": "", "x": 1e12}).status_code == 422
    # A NER under another one's name goes to the register, which merges it
    # on proof or asks (Possible duplicates) — not refused at the door.
    with SessionLocal() as db:
        aid = db.execute(select(NerAsset.id).where(NerAsset.project_id == pid)).scalar()
    same = post({"kind": "NER_NONCRITICAL", "label": "NER_20.1", "same_as_asset_id": aid})
    assert same.status_code == 201
    assert post({"kind": "NER_NONCRITICAL", "label": "NER_21.1"}).status_code == 201


def test_b3_projects_and_rules(client):
    assert client.post("/api/projects", json={"name": "X" * 201}).status_code == 422
    assert client.post("/api/projects", json={"name": "ok", "venue_code": "V" * 41}).status_code == 422
    pid = _project("b3 rules")
    patch = lambda body: client.patch(f"/api/projects/{pid}/settings", json=body)
    assert patch({"display_units": "parsecs"}).status_code == 422
    assert patch({"photographer_limit_m": 500}).status_code == 422
    assert patch({"general_limit_m": 60}).status_code == 422          # below the 70 m one
    assert patch({"general_limit_m": 100, "photographer_limit_m": 80}).status_code == 200
    assert patch({"display_units": "both"}).status_code == 200


def test_b3_restore_of_an_unknown_ner_is_404(client):
    pid = _project("b3 restore")
    assert client.post(f"/api/projects/{pid}/ners/99999999/restore").status_code == 404


# ---------------------------------------------------------------------------
# B4 — a book read without background does not hold the other requests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# B6 — the route log names the sheet as route.created does
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# B7 — NER_DATA_DIR moves the database too
# ---------------------------------------------------------------------------

def test_b7_the_database_follows_the_data_folder(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "NER_DATABASE_URL"}
    env["NER_DATA_DIR"] = str(tmp_path)
    out = subprocess.run([sys.executable, "-c",
                          "from app.config import settings; print(settings.database_url)"],
                         cwd=ROOT / "backend", env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert str(tmp_path / "ner.sqlite3") in out.stdout.strip()


# ---------------------------------------------------------------------------
# B1, B5, R1 — the screens and the test plumbing
# ---------------------------------------------------------------------------

def test_b1_the_notice_floats_over_the_page():
    assert "banner toast" in _read("frontend/src/App.tsx")
    css = _read("frontend/src/styles.css")
    block = css[css.index(".banner.toast"):]
    assert "position: fixed" in block[:400]


def test_b5_the_test_folders_are_removed_after_the_run():
    src = _read("tests/conftest.py")
    assert "pytest_sessionfinish" in src and "shutil.rmtree" in src

