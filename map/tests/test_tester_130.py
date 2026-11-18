"""Independent tester - RackReach Map 1.3.0-rc1 (the findings of the team
5.7.0-rc1 review that live in code the Map edition shares).

Every test FAILS on 1.3.0-rc1 and passes once fixed. IDs as in
/home/claude/w/tester-570/FINDINGS.md.
"""
from __future__ import annotations

import os

import pymupdf
import pytest

from tests.test_ner_register import client, samples  # noqa: F401


def _uid(prefix: str) -> str:
    return f"{prefix} {os.urandom(4).hex()}"


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
    pid = client.post("/api/projects", json={"name": _uid("T130 F8"), "venue_code": "SYN"}).json()["id"]
    with open(samples["pdf"], "rb") as fh:
        client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                    files={"file": ("plan.pdf", fh, "application/pdf")})

    def locked(target, src, out, **kw):
        raise isolate.IsolatedError(
            "FzErrorSystem", f"code=2: cannot remove file '{out}': Permission denied")
    monkeypatch.setattr(isolate, "run", locked)
    r = client.post(f"/api/projects/{pid}/reports/pdf")
    assert r.status_code == 422, r.text
    detail = r.json().get("detail", "")
    assert "damaged" not in detail and "import that copy" not in detail, detail


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
