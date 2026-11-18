"""5.8.0: the smaller items - a note on a /UserUnit sheet, the note font once
in the PDF of several books."""
from __future__ import annotations

import os

import pymupdf
import pytest

from app.domain import reports as R
from tests.test_ner_register import client  # noqa: F401


@pytest.mark.parametrize("uu", [1.0, 1.19, 2.0])
def test_a_note_on_a_user_unit_sheet_sits_in_its_box(tmp_path, uu):
    doc = pymupdf.open()
    p = doc.new_page(width=800, height=600)
    if uu != 1.0:
        doc.xref_set_key(p.xref, "UserUnit", str(uu))
    sp = R.PageSpace(p)
    R.draw_note(sp, "word " * 40, centre=300, top=200, above=180)
    sp.commit()
    out = tmp_path / "n.pdf"
    doc.save(out)
    pg = pymupdf.open(out)[0]
    spans = [s for b in pg.get_text("dict")["blocks"] for ln in b["lines"] for s in ln["spans"]]
    lines = [ln["bbox"] for b in pg.get_text("dict")["blocks"] for ln in b["lines"]]
    box = [d["rect"] for d in pg.get_drawings()][0]
    assert all(abs(s["size"] - R.NOTE_SIZE) < 0.05 for s in spans)
    assert all(box.x0 - 1 <= x0 and x1 <= box.x1 + 1 and box.y0 - 1 <= y0 and y1 <= box.y1 + 1.5
               for x0, y0, x1, y1 in lines), (lines, box)


def test_the_pdf_of_several_books_carries_the_note_font_once(client):
    from app.db import SessionLocal
    from app.models import NerAsset
    from tests.test_multi_book_410 import _ner, _two_books
    pid, a, b = _two_books(f"580 font once {os.urandom(3).hex()}")
    _ner(pid, a, 1, "NER 24", 100, 100)
    _ner(pid, b, 2, "NER 25", 200, 200)
    with SessionLocal() as db:
        for x in db.query(NerAsset).filter(NerAsset.project_id == pid):
            x.notes = "a note ✓"
        db.commit()
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false&notes=true")
    assert r.status_code == 200, r.text
    doc = pymupdf.open(stream=client.get(r.json()["download"]).content, filetype="pdf")
    files = {}
    for pno in range(doc.page_count):
        for f in doc[pno].get_fonts(full=True):
            if f[0] and ("Nimbus" in f[3] or "Droid" in f[3]):
                files.setdefault(f[3].split("+")[-1], set()).add(f[0])
    assert files and all(len(x) == 1 for x in files.values()), files
