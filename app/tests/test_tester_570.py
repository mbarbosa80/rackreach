"""Independent tester - RackReach 5.7.0-rc1.

Every test in this module reproduces a finding: it FAILS on 5.7.0-rc1 and
passes once the finding is fixed. See /home/claude/w/tester-570/FINDINGS.md.
"""
from __future__ import annotations

import os

import pymupdf
import pytest
from sqlalchemy import select

from tests.test_ner_register import client, samples  # noqa: F401


def _uid(prefix: str) -> str:
    return f"{prefix} {os.urandom(4).hex()}"


def _full_project(client, samples, name):
    """The synthetic drawing (NERs placed), the TEAP imported, scales
    confirmed and the matching run - the state of any real project."""
    r = client.post("/api/projects", json={"name": _uid(name), "venue_code": "SYN"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    with open(samples["pdf"], "rb") as fh:
        r = client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                        files={"file": ("plan.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    with open(samples["teap"], "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap.xlsx", fh,
                                        "application/vnd.openxmlformats-officedocument."
                                        "spreadsheetml.sheet")})
    assert r.status_code == 201, r.text
    tid, proposed = r.json()["id"], r.json()["proposed"]
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                          "mapping": proposed["mapping"]})
    assert r.status_code == 200, r.text
    for page in (1, 2, 3):
        client.post(f"/api/projects/{pid}/pages/{page}/scale",
                    json={"mode": "ratio", "denominator": 200.0, "confirm": True})
    assert client.post(f"/api/projects/{pid}/matching/run").status_code == 200
    return pid


def _live_ners(client, pid):
    r = client.get(f"/api/projects/{pid}/ners")
    assert r.status_code == 200, r.text
    return [n for n in r.json()["ners"] if n.get("is_live", True) and not n.get("deleted_at")]


def _pdf_text(client, pid, **params):
    q = "&".join(f"{k}={str(v).lower()}" for k, v in params.items())
    r = client.post(f"/api/projects/{pid}/reports/pdf?{q}")
    assert r.status_code == 200, r.text
    body = r.json()
    doc = pymupdf.open(stream=client.get(body["download"]).content, filetype="pdf")
    try:
        return " ".join(p.get_text() for p in doc).replace("\n", " ")
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# T1-1  "with NER notes" prints nothing once the project has been analysed:
#       the marked-up PDF draws the NERs of the saved As-Is snapshot, and
#       scenario_view rebuilds them without user_note.
# ---------------------------------------------------------------------------

def test_t1_1_note_printed_on_marked_pdf_of_an_analysed_project(client, samples):
    pid = _full_project(client, samples, "T570 notes analysed")
    ner = _live_ners(client, pid)[0]
    note = "Moved here because XPTO will cover the same areas"
    r = client.patch(f"/api/projects/{pid}/ners/{ner['id']}", json={"values": {"notes": note}})
    assert r.status_code == 200, r.text
    # The person runs the analysis (every real project has one), then exports.
    assert client.post(f"/api/projects/{pid}/analyse").status_code == 200
    off = _pdf_text(client, pid, notes=False)
    on = _pdf_text(client, pid, notes=True)
    assert "XPTO" not in off
    assert "XPTO" in on, "the note is not on the marked-up PDF of an analysed project"


# ---------------------------------------------------------------------------
# T1-2  A note with common typed characters outside Latin-1 (en dash, curly
#       quotes, euro sign, arrow) is printed with "·" in their place: the note
#       uses the base-14 "helv" font, which has no glyph for them.
# ---------------------------------------------------------------------------

from app.domain.coverage import NER, CoverageResult            # noqa: E402
from app.domain.distance import DistanceRules                  # noqa: E402
from app.domain.geometry import Point                          # noqa: E402
from app.domain.pdf_ingest import ElementKind                  # noqa: E402
from app.domain.reports import export_marked_pdf               # noqa: E402
from app.domain.units import Scale                             # noqa: E402

_SC = {1: Scale(metres_per_unit=0.1, source="manual", label="1:100", confirmed=True)}


def _marked(tmp_path, ners, w=800, h=600):
    src = str(tmp_path / "src.pdf")
    doc = pymupdf.open()
    doc.new_page(width=w, height=h)
    doc.save(src)
    doc.close()
    out = str(tmp_path / "out.pdf")
    export_marked_pdf(src, out, coverage=CoverageResult(assignments=[], ner_load={},
                                                        exceptions=[], uncovered=[]),
                      ners=ners, scale_for_page=_SC, rules=DistanceRules(), draw_notes=True,
                      draw_legend=False)
    return pymupdf.open(out)


def _ner_with_note(note, x=300.0, y=200.0):
    return NER(key="A1", label="NER_20.1", kind=ElementKind.NER_NONCRITICAL,
               point=Point(x, y, 1), critical=False, notes=[note], user_note=note)


def test_t1_2_note_keeps_dashes_quotes_euro_and_arrows(tmp_path):
    note = "Moved – see “VDB rev C” → B2, cost €1k"
    doc = _marked(tmp_path, [_ner_with_note(note)])
    text = doc[0].get_text().replace("\n", " ")
    for ch in ("–", "“", "”", "→", "€"):
        assert ch in text, (ch, text[-200:])


# ---------------------------------------------------------------------------
# T1-3  "Wrapped, never cut": a NER near the bottom (or left) edge of a sheet
#       gets its note box drawn past the page edge - the lines below the
#       sheet are not on the PDF.
# ---------------------------------------------------------------------------

def test_t1_3_note_near_the_sheet_edge_is_not_cut(tmp_path):
    note = " ".join(f"word{i}" for i in range(60))          # several lines
    doc = _marked(tmp_path, [_ner_with_note(note, x=20.0, y=580.0)])
    text = doc[0].get_text()                                # clipped to the page
    missing = [f"word{i}" for i in range(60) if f"word{i}" not in text]
    assert not missing, f"{len(missing)} words of the note fall off the sheet: {missing[:5]}"


# ---------------------------------------------------------------------------
# T1-4  A level typed "2", "Level 2" or "L1" is stored "LEVEL 2" / "LEVEL 1",
#       while the title blocks (and level_from_sheet_number) write
#       "LEVEL 02" / "LEVEL 01". The register compares levels as text, so the
#       detail sheet's NER and the same NER on a sheet whose title states
#       "LEVEL 02" are declared on "different levels" - never merged, the
#       opposite of what the box is for.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("typed, title", [("2", "LEVEL 02"), ("Level 2", "LEVEL 02"),
                                          ("L1", "LEVEL 01")])
def test_t1_4_a_typed_level_agrees_with_the_same_level_on_a_title(typed, title):
    from app import services as S
    from app.domain.consolidation import Candidate, compare
    a = Candidate(ref=1, label="NER 7", kind="NER_NONCRITICAL", page=3,
                  level=S.normalise_level(typed))
    b = Candidate(ref=2, label="NER 7", kind="NER_NONCRITICAL", page=9, level=title)
    assert compare(a, b).blocking != "different_level", (S.normalise_level(typed), title)


# ---------------------------------------------------------------------------
# T1-5  F8: every failure of the child is worded "the file may be damaged ...
#       import that copy instead" - also when the book is fine and the PDF
#       could not be WRITTEN (the previous export still open in a PDF reader
#       on Windows: same file name every time). The advice sends the person to
#       re-import a good book.
# ---------------------------------------------------------------------------

def test_t1_5_a_locked_output_file_is_not_blamed_on_the_book(client, samples, monkeypatch):
    from app import isolate
    pid = _full_project(client, samples, "T570 F8 locked output")

    def locked(target, src, out, **kw):
        raise isolate.IsolatedError(
            "FzErrorSystem", f"code=2: cannot remove file '{out}': Permission denied")
    monkeypatch.setattr(isolate, "run", locked)
    r = client.post(f"/api/projects/{pid}/reports/pdf")
    assert r.status_code == 422, r.text
    detail = r.json().get("detail", "")
    assert "damaged" not in detail and "import that copy" not in detail, detail


# ---------------------------------------------------------------------------
# T1-6  R7-F9 (teap_values_570) says a repaired space gets "what a new import
#       would give". It does not: (a) a line with an explicit port count and a
#       wrong quantity loses its ports (a new import keeps the explicit
#       count); (b) total_ports is recomputed as the bare sum of the lines,
#       so the project's growth margin is dropped from the space.
# ---------------------------------------------------------------------------

def _stored_space(lines, total, growth=None):
    from app.db import SessionLocal, init_db
    from app.models import Project, TeapFile, TeapSpace
    init_db()
    db = SessionLocal()
    p = Project(name=_uid("T570 F9"), venue_code="TST",
                demand_rules={"growth_margin_pct": growth} if growth else None)
    db.add(p)
    db.flush()
    tf = TeapFile(project_id=p.id, filename="t.xlsx", stored_path="t.xlsx")
    db.add(tf)
    db.flush()
    t = TeapSpace(project_id=p.id, teap_file_id=tf.id, space_key="TST|A.1", space_code="A.1",
                  space_name="A", total_ports=total, demand_type="general", lines=lines,
                  warnings=[])
    db.add(t)
    db.commit()
    return db, p, t


def test_t1_6a_explicit_ports_survive_a_wrong_quantity():
    from app import migrate
    from tests.test_tester_findings_530 import _demand, _line
    # What a new import gives: quantity -2 is a data error, the 3 explicit ports stay.
    assert _demand([_line("A.1", -2, 3)])["A.1"].total_ports == 3
    db, p, t = _stored_space([{"row": 2, "qty": -2, "ports": 3.0, "rule": "A"}], 3.0)
    try:
        migrate.teap_values_570(db, p)
        db.refresh(t)
        assert t.total_ports == 3.0, t.total_ports
    finally:
        db.close()


def test_t1_6b_the_growth_margin_is_kept():
    from app import migrate
    # 4 good ports + one line of 200,000 (stored by 5.6.0), growth margin 10 %.
    db, p, t = _stored_space([{"row": 2, "qty": 4, "ports": 4.0, "rule": "B"},
                              {"row": 3, "qty": 200000, "ports": 200000.0, "rule": "B"}],
                             (4 + 200000) * 1.1, growth=10)
    try:
        migrate.teap_values_570(db, p)
        db.refresh(t)
        assert abs(t.total_ports - 4.4) < 1e-9, t.total_ports
    finally:
        db.close()


# ---------------------------------------------------------------------------
# T1-7  F6: the Reconciliation compares the venue part of the space key with
#       the project's venue CODE. When the TEAP has no venue code column and
#       is filtered by venue NAME (the import supports it), every pending line
#       of the project's own venue is said to be "another venue".
# ---------------------------------------------------------------------------

def test_t1_7_own_venue_by_name_is_not_another_venue(client, samples):
    import openpyxl
    r = client.post("/api/projects", json={"name": _uid("T570 F6 name"), "venue_code": "SYN"})
    pid = r.json()["id"]
    with open(samples["pdf"], "rb") as fh:
        client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                    files={"file": ("plan.pdf", fh, "application/pdf")})
    with open(samples["teap"], "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap.xlsx", fh, "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")})
    tid, proposed = r.json()["id"], r.json()["proposed"]
    mapping = dict(proposed["mapping"])
    mapping["venue_code"] = None                       # a TEAP with venue names only
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                          "mapping": mapping, "venue_filter": "Synthetic Venue"})
    assert r.status_code == 200, r.text
    for page in (1, 2, 3):
        client.post(f"/api/projects/{pid}/pages/{page}/scale",
                    json={"mode": "ratio", "denominator": 200.0, "confirm": True})
    client.post(f"/api/projects/{pid}/matching/run")
    client.post(f"/api/projects/{pid}/analyse")
    r = client.post(f"/api/projects/{pid}/reports/xlsx")
    assert r.status_code == 200, r.text
    import io
    wb = openpyxl.load_workbook(io.BytesIO(client.get(r.json()["download"]).content))
    text = " | ".join(str(c.value) for row in wb["Reconciliation"].iter_rows() for c in row
                      if c.value)
    assert "Pending ports" in text, text[:300]
    assert "another venue" not in text, text[text.find("Pending"):][:400]


# ---------------------------------------------------------------------------
# T1-8  F4: a NER's last marker simply UNLINKED (asset_id None - no other
#       NER involved) also retires the NER, with the reason "its last marker
#       was linked to another NER", which is not what happened.
# ---------------------------------------------------------------------------

def test_t1_8_retire_reason_says_what_happened_on_an_unlink(client, samples):
    from app.db import SessionLocal
    from app.models import NerAsset
    from tests.test_separate_522 import _two_drawings
    pid, row, e1, e2 = _two_drawings(client, samples, _uid("T570 F4 unlink"), "TU4")
    for e in (e1, e2):
        r = client.post(f"/api/projects/{pid}/occurrences/{e}/link",
                        json={"asset_id": None, "status": "unlinked"})
        assert r.status_code == 200, r.text
    with SessionLocal() as db:
        old = db.get(NerAsset, row["id"])
        if old.deleted_at is not None:
            assert "another NER" not in (old.deleted_reason or ""), old.deleted_reason


# ---------------------------------------------------------------------------
# T1-9  Level by hand: the start-up title-block pass (backfill_sheet_numbers,
#       pages whose sheet number was never read) writes the title's level
#       over a level typed by hand and leaves level_source = "manual": a
#       level read off the drawing is then editable and is kept as "typed"
#       by clear_guessed_levels.
# ---------------------------------------------------------------------------

def test_t1_9_title_block_pass_and_a_typed_level_agree(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import migrate
    from app import services as S
    from app.db import SessionLocal, init_db
    from app.domain import pdf_ingest
    from app.models import Drawing, Page, Project
    init_db()
    book = tmp_path / "b.pdf"
    book.write_bytes(b"%PDF-1.4\n")
    with SessionLocal() as db:
        p = Project(name=_uid("T570 level backfill"))
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="b.pdf", stored_path=str(book),
                    sha256="0" * 64, page_count=1)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=800, height=600, level=None,
                    sheet_number=None))
        db.commit()
        S.set_page_level(db, p, 1, "05", actor="tester")
        monkeypatch.setattr(pdf_ingest, "read_title_blocks", lambda path, pages: {
            1: SimpleNamespace(number="TST-402-O", title="LEVEL 02 PLAN", level="LEVEL 02")})
        migrate.backfill_sheet_numbers(db, p)
        pg = db.query(Page).filter(Page.drawing_id == d.id).one()
        # Either the typed level is kept, or the title's level is taken AND
        # marked as read off the drawing - never the drawing's level marked typed.
        assert (pg.level, pg.level_source) in (("LEVEL 05", "manual"), ("LEVEL 02", None)), \
            (pg.level, pg.level_source)


# ---------------------------------------------------------------------------
# T1-10 Level by hand, several books: a NER whose canonical_drawing_id is
#       NULL belongs to the FIRST book everywhere else (Sheets.key,
#       migrate "a.canonical_drawing_id or first"); set_page_level lets it
#       take a level typed on the same page number of ANOTHER book.
# ---------------------------------------------------------------------------

def test_t1_10_a_level_typed_in_book_two_stays_in_book_two():
    from app import services as S
    from app.db import SessionLocal, init_db
    from app.models import (Criticality, Drawing, KeepDecision, NerAsset, NerStatus, Page,
                            Project)
    from app.sheets import STRIDE
    init_db()
    with SessionLocal() as db:
        p = Project(name=_uid("T570 level two books"))
        db.add(p)
        db.flush()
        books = []
        for i in range(2):
            d = Drawing(project_id=p.id, filename=f"TST_{400 + i * 100}.pdf",
                        stored_path=f"b{i}.pdf", sha256=f"{i}" * 64, page_count=2)
            db.add(d)
            db.flush()
            books.append(d)
            for n in (1, 2):
                db.add(Page(drawing_id=d.id, number=n, width=800, height=600, level=None,
                            sheet_number=f"TST-{4 + i}0{n}-O"))
        a = NerAsset(project_id=p.id, code=f"NER-{os.urandom(3).hex()}", display_name="NER_9.1",
                     kind=ElementKind.NER_NONCRITICAL, classification=Criticality.NON_CRITICAL,
                     status=NerStatus.IDENTIFIED, keep_decision=KeepDecision.UNDECIDED,
                     canonical_page=2, canonical_drawing_id=None, canonical_x=1, canonical_y=1,
                     position_known=True, level=None)
        db.add(a)
        db.commit()
        first, second = sorted(b.id for b in books)
        out = S.set_page_level(db, p, second * STRIDE + 2, "03", actor="tester")
        db.refresh(a)
        assert a.level is None and "NER_9.1" not in out["ners"], (a.level, out)
