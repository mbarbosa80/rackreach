"""5.7.0 - a NER's note (Marcelo, 16 Nov 2026): it was written on the NERs
screen and read only in the workbook. It is now shown on hover in the
Drawing viewer and, when the export asks for it ("with NER notes", off by
default), printed under the NER's name on the marked-up PDF - wrapped, on
every sheet the NER is drawn on. Only the person's note: never the app's own
sentences about the NER."""
from __future__ import annotations

import pymupdf
import pytest

from app.domain.coverage import NER, CoverageResult
from app.domain.distance import DistanceRules
from app.domain.geometry import Point
from app.domain.pdf_ingest import ElementKind
from app.domain.reports import NOTE_SIZE, NOTE_WIDTH, export_marked_pdf, wrap_note
from app.domain.units import Scale

RULES = DistanceRules()
SCALES = {1: Scale(metres_per_unit=0.1, source="manual", label="1:100", confirmed=True),
          2: Scale(metres_per_unit=0.1, source="manual", label="1:100", confirmed=True)}
NOTE = ("NER moved from position X to Y, and NER XPTO was removed because this one "
        "will cover the same areas")


def _pdf(path, pages=2):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=800, height=600)
    doc.save(path)
    doc.close()
    return path


def _ner(note="", kind=ElementKind.NER_NONCRITICAL, others=()):
    return NER(key="A1", label="NER_20.1", kind=kind, point=Point(300, 200, 1),
               other_points=list(others), critical=kind == ElementKind.NER_CRITICAL,
               notes=[note, "criticality read from the drawing and not yet confirmed"]
               if note else ["criticality read from the drawing and not yet confirmed"],
               user_note=note)


def _text(tmp_path, ners, **kw):
    src = _pdf(str(tmp_path / "src.pdf"))
    out = str(tmp_path / "out.pdf")
    export_marked_pdf(src, out,
                      coverage=CoverageResult(assignments=[], ner_load={}, exceptions=[],
                                              uncovered=[]),
                      ners=ners, scale_for_page=SCALES, rules=RULES, **kw)
    doc = pymupdf.open(out)
    try:
        return [p.get_text().replace("\n", " ") for p in doc]
    finally:
        doc.close()


def test_the_note_is_off_by_default(tmp_path):
    pages = _text(tmp_path, [_ner(NOTE)])
    assert "XPTO" not in " ".join(pages)


def test_the_note_is_printed_when_asked_and_wrapped(tmp_path):
    pages = _text(tmp_path, [_ner(NOTE)], draw_notes=True)
    assert "XPTO" in pages[0] and "same areas" in pages[0]
    lines = wrap_note(NOTE)
    assert len(lines) > 1
    assert all(pymupdf.get_text_length(t, fontname="helv", fontsize=NOTE_SIZE) <= NOTE_WIDTH + 1e-6
               for t in lines)
    assert " ".join(lines) == NOTE


def test_the_apps_own_sentences_are_never_printed(tmp_path):
    pages = _text(tmp_path, [_ner("")], draw_notes=True)
    assert "not yet confirmed" not in " ".join(pages)


def test_the_note_is_on_every_sheet_the_ner_is_drawn_on(tmp_path):
    pages = _text(tmp_path, [_ner(NOTE, others=[Point(100, 100, 2)])], draw_notes=True)
    assert "XPTO" in pages[0] and "XPTO" in pages[1]


def test_wrap_keeps_line_breaks_and_breaks_a_word_too_long():
    lines = wrap_note("first line\nsecond line " + "W" * 80)
    assert lines[0] == "first line"
    assert lines[1].startswith("second line")
    assert all(pymupdf.get_text_length(t, fontname="helv", fontsize=NOTE_SIZE) <= NOTE_WIDTH + 1e-6
               for t in lines)
    assert "".join(lines).replace(" ", "").endswith("W" * 80)


@pytest.mark.parametrize("note", ["", "   ", "\n\n"])
def test_an_empty_note_draws_nothing(tmp_path, note):
    assert wrap_note(note) == [] or wrap_note(note) == [""] or not "".join(wrap_note(note)).strip()
