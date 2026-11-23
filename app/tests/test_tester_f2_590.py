"""Independent tester F2, RackReach 5.8.0 (HEAD 2352699), 18 Nov 2026.

Every test marked FAIL below fails on 5.8.0 as released. Synthetic data only.
See FINDINGS.md beside the clone for the verdict and severity.
"""
from __future__ import annotations

import io
import os

import openpyxl
import pytest

from tests.test_ner_register import TMP, client, samples  # noqa: F401  (sets the env)
from tests.test_tester_570 import _full_project, _live_ners
from tests.test_tester_580 import _merge, _ner, _patch, _reconciliation, _teap_two_venues

from app.domain.consolidation import Candidate, compare, level_key, normalise_level


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


def _db_project_with_page(level=None):
    from app.db import SessionLocal, init_db
    from app.models import Drawing, Page, Project
    init_db()
    db = SessionLocal()
    p = Project(name=_uid("F2 page level"))
    db.add(p)
    db.flush()
    d = Drawing(project_id=p.id, filename="b.pdf", stored_path="b.pdf", sha256="2" * 64,
                page_count=1)
    db.add(d)
    db.flush()
    db.add(Page(drawing_id=d.id, number=1, width=800, height=600, level=level,
                sheet_number="TST-401-O"))
    db.commit()
    return db, p


# ---------------------------------------------------------------------------
# F2-1  The T3-4 / T3-6 guards were put on the NER's Level field only. The
#       Scale screen's level (POST /pages/{n}/level) still takes a zero-width
#       space, a right-to-left override and a 60-character level (cut to 40
#       without a word) - and the NERs of that sheet take it, and the
#       workbook's Level column prints it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["​", "02​", "‮20", "⁦B1"])
def test_f2_1_page_level_refuses_invisible_characters(bad):
    from app import services as S
    db, p = _db_project_with_page()
    try:
        with pytest.raises((S.ValidationError, S.ConflictError)):
            S.set_page_level(db, p, 1, bad, actor="t")
    finally:
        db.close()


def test_f2_1b_page_level_over_40_characters_is_refused_not_cut():
    from app import services as S
    db, p = _db_project_with_page()
    long = "MEZZANINE LEVEL WEST CONCOURSE UPPER TIER SECTION 12"   # 52 chars
    try:
        try:
            out = S.set_page_level(db, p, 1, long, actor="t")
        except S.ValidationError:
            return                                    # refused: what T3-6 promised
        # 5.8.0: stored cut, without a word
        assert out["level"] == long.upper(), out
    finally:
        db.close()


# ---------------------------------------------------------------------------
# F2-2  normalise_level and level_key disagree on spellings a sheet can carry
#       without a space ("LEVEL2", "FLOOR2", "LVL3" - the font-size reader
#       _sheet_context keeps them as printed) and on "L-1". The composite map
#       groups "LEVEL2" with "LEVEL 02" (normalise_level) while the merge and
#       the cross-sheet link rule call them different floors (level_key).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["LEVEL2", "FLOOR2", "LVL2", "L-1", "L 2"])
def test_f2_2_level_key_agrees_with_normalise_level(raw):
    assert level_key(raw) == level_key(normalise_level(raw)), (raw, normalise_level(raw))


def test_f2_2b_same_ner_on_level2_and_level_02_is_not_blocked_as_another_floor():
    a = Candidate(ref="a", label="NER 4", page=1, level="LEVEL2", building=None)
    b = Candidate(ref="b", label="NER 4", page=2, level="LEVEL 02", building=None)
    assert compare(a, b).blocking != "different_level", compare(a, b).to_dict()


def test_f2_2c_the_sheet_reader_keeps_level2_as_printed():
    """Shows the raw spelling reaches Page.level (the level_hint path)."""
    from app.domain.pdf_ingest import _sheet_context

    class It:
        def __init__(self, t, s=8):
            self.text, self.size = t, s
    assert _sheet_context([It("LEVEL2")])[1] == "LEVEL2"


# ---------------------------------------------------------------------------
# F2-3  undo of a merge restores the primary's level as it was before the
#       merge even when the merge did not change it - a level the analyst
#       typed on the primary AFTER the merge is lost by the undo.
# ---------------------------------------------------------------------------

def test_f2_3_undo_keeps_a_level_typed_after_the_merge(client, samples):
    pid = _full_project(client, samples, _uid("F2 undo level"))
    ns = {n["display_name"]: n for n in _live_ners(client, pid)}
    a, b = ns["NER_20.1"], ns["NER_22.1"]
    assert _patch(client, pid, a["id"], "1").status_code == 200   # primary: LEVEL 01 by hand
    cid = _merge(client, pid, a["id"], b["id"])
    assert _ner(client, pid, a["id"])["level"] == "LEVEL 01"       # merge did not touch it
    assert _patch(client, pid, a["id"], "5").status_code == 200   # corrected later
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text
    got = _ner(client, pid, a["id"])
    # 5.8.0: back to LEVEL 01 - the later correction is gone
    assert got["level"] == "LEVEL 05", got


# ---------------------------------------------------------------------------
# F2-4  A re-import that FAILS (422 "nothing to import ... left as it was")
#       still commits the new venue filter (and mapping / sheet) on the TEAP
#       file the spaces come from. With T3-1's rule (a filtered TEAP never has
#       "another venue") the Reconciliation sheet of an all-venue TEAP then
#       files the other venue's rows under "no room on the drawing".
# ---------------------------------------------------------------------------

def test_f2_4_a_failed_reimport_leaves_the_teap_and_the_workbook_as_they_were(client, samples):
    pid = _full_project(client, samples, _uid("F2 failed reimport"))
    path = _teap_two_venues(samples["teap"], os.path.join(TMP, f"f2_two_{pid}.xlsx"))
    with open(path, "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap-all.xlsx", fh, "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")})
    tid, proposed = r.json()["id"], r.json()["proposed"]
    body = {"sheet": proposed["sheet"], "header_row": proposed["header_row"],
            "mapping": proposed["mapping"]}
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={**body, "allow_all_venues": True})
    assert r.status_code == 200 and r.json()["venue_filter"] is None, r.text
    client.post(f"/api/projects/{pid}/matching/run")
    assert client.post(f"/api/projects/{pid}/analyse").status_code == 200
    before = _reconciliation(client, pid)
    assert "another venue (OTH)" in before
    # a typo in the filter: refused, "the TEAP already in the project was left as it was"
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import", json={**body, "venue_filter": "SYNX"})
    assert r.status_code == 422, r.text
    assert client.post(f"/api/projects/{pid}/analyse").status_code == 200
    after = _reconciliation(client, pid)
    assert "another venue (OTH)" in after, after[after.find("Pending"):][:400]


# ---------------------------------------------------------------------------
# F2-5  The rc3 multi-venue guard for a CSV counts rows with an empty venue
#       cell as one more "venue"; the workbook guard (preview_venues) skips
#       them. A one-venue CSV with a few rows whose venue is blank, in a
#       project with no venue code, is refused as "2 venues" - it imported
#       before rc3, and the same rows as a workbook import.
# ---------------------------------------------------------------------------

def test_f2_5_a_csv_with_blank_venue_rows_is_not_two_venues(client, tmp_path):
    pid = client.post("/api/projects", json={"name": _uid("F2 csv blank")}).json()["id"]
    f = tmp_path / "teap.csv"
    f.write_text("Venue Code,Space Code,Space Name,Product Code,Quantity,Network Ports\n"
                 "SYN,A.1,Alpha,SW,1,2\nSYN,B.1,Beta,SW,1,3\n,C.1,Gamma,SW,1,1\n",
                 encoding="utf-8")
    with open(f, "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap.csv", fh, "text/csv")})
    assert r.status_code == 201, r.text
    tid, proposed = r.json()["id"], r.json()["proposed"]
    assert proposed["mapping"].get("venue_code"), proposed
    r = client.post(f"/api/projects/{pid}/teap/{tid}/preview-venues",
                    json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                          "mapping": proposed["mapping"]})
    assert r.json()["count"] == 1, r.json()                # the screen says one venue
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                          "mapping": proposed["mapping"]})
    # 5.8.0: 422 "this sheet covers 2 venues (SYN (2 rows),  (1 rows))"
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# F2-6  T3-6 "refused, never cut" is measured before upper-casing; upper()
#       lengthens some letters ("ß" -> "SS"), and normalise_level then cuts
#       the result at 40 without a word.
# ---------------------------------------------------------------------------

def test_f2_6_a_level_that_grows_when_upper_cased_is_not_cut(client, samples):
    pid = _full_project(client, samples, _uid("F2 eszett"))
    n = _live_ners(client, pid)[0]
    typed = "Großes Geschoß Straße Süd Außenbereich ß"        # 40 characters
    assert len(typed) == 40
    r = _patch(client, pid, n["id"], typed)
    if r.status_code == 400 or r.status_code == 422:
        return                                                  # refused: fine
    stored = _ner(client, pid, n["id"])["level"]
    assert stored == typed.upper(), (stored, typed.upper())


# ---------------------------------------------------------------------------
# F2-7  wrap_note breaks a word wider than the box by trying one character
#       less at a time, measuring each try through note_runs (a has_glyph call
#       per character): cubic in the word's length, and fit_note can call it
#       several times. A note with a pasted link or path of 800 characters
#       takes ~10 s per sheet the NER is drawn on; 3,000 characters did not
#       finish in 20 s. There is no length limit on a note, and the export
#       runs in a child with no timeout.
# ---------------------------------------------------------------------------

def test_f2_7_a_long_unbroken_word_in_a_note_wraps_quickly():
    import time
    from app.domain.reports import fit_note
    t = time.time()
    lines, size, width = fit_note("https://example.invalid/" + "a" * 576, 800, 600)
    took = time.time() - t
    assert lines and took < 1.0, f"{took:.1f} s for one 600-character word"
