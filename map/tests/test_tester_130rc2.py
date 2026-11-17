"""Independent tester - RackReach Map 1.3.0-rc2.

The findings of the 5.7.0-rc2 pass that apply to the map edition (same code):
every test FAILS on Map 1.3.0-rc2 (commit c87f59e) and passes once fixed.
T2-7 (TEAP start-up pass) is team-only. See team/tests/test_tester_570rc2.py.
"""
from __future__ import annotations

import os
import shutil
from types import SimpleNamespace

import pymupdf
import pytest

# The composite fixtures (a synthetic composite book, its map built) and the
# signed-in client they use; ``samples`` is the synthetic drawing + TEAP.
from tests.test_composite_map_500 import _place, _project_with_book, _wait, client  # noqa: F401
from tests.test_ner_register import samples  # noqa: F401

from app.config import settings
from app.domain.coverage import NER, CoverageResult
from app.domain.distance import DistanceRules
from app.domain.geometry import Point
from app.domain.pdf_ingest import ElementKind
from app.domain.reports import export_marked_pdf
from app.domain.units import Scale


def _uid(prefix: str) -> str:
    return f"{prefix} {os.urandom(4).hex()}"


def _block(path) -> None:
    """Make ``path`` a file that cannot be replaced - what a PDF reader holding
    the last export open does on Windows. Here: a non-empty folder of that
    name, so mupdf's "cannot remove file" is the real error, not a mock."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)
    os.makedirs(path)
    with open(os.path.join(path, "held"), "w") as fh:
        fh.write("x")


def _post(client, url):
    """status, detail - a server exception (a bare 500) comes out as 500."""
    try:
        r = client.post(url)
        return r.status_code, r.text
    except Exception as exc:                                # noqa: BLE001
        return 500, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# T2-1  T1-5 is fixed for a one-book project only. In a project of several
#       books the marked books are merged and saved with merged.save(); a
#       PyMuPDF save error is FzErrorSystem, which is neither OSError nor
#       RuntimeError, so _saving() lets it through: a bare 500 (a job error
#       "FzErrorSystem: code=2: cannot remove file ...") instead of "close the
#       file and export again".
# ---------------------------------------------------------------------------

def test_t2_1_locked_marked_pdf_of_a_multi_book_project_is_said_in_words(client):
    from tests.test_multi_book_410 import _ner, _two_books
    pid, a, b = _two_books(_uid("T2-1 two books locked"))
    _ner(pid, a, 1, "NER 24", 100, 100)
    _ner(pid, b, 2, "NER 25", 200, 200)
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false")
    assert r.status_code == 200, r.text
    out = settings.exports_dir / f"project_{pid}" / r.json()["file"]
    _block(out)                                   # the last export is held open
    try:
        status, detail = _post(client, f"/api/projects/{pid}/reports/pdf?background=false")
    finally:
        shutil.rmtree(out, ignore_errors=True)
    assert status == 422 and "still open" in detail, (status, detail[:300])


def test_t2_1_control_the_one_book_project_is_said_in_words(client, samples):
    """Same blocked file, one book: rc2 says it in words (this passes)."""
    pid = client.post("/api/projects", json={"name": _uid("T2-1 one book locked"),
                                             "venue_code": "SYN"}).json()["id"]
    with open(samples["pdf"], "rb") as fh:
        client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                    files={"file": ("plan.pdf", fh, "application/pdf")})
    r = client.post(f"/api/projects/{pid}/reports/pdf?background=false")
    assert r.status_code == 200, r.text
    out = settings.exports_dir / f"project_{pid}" / r.json()["file"]
    _block(out)
    try:
        status, detail = _post(client, f"/api/projects/{pid}/reports/pdf?background=false")
    finally:
        shutil.rmtree(out, ignore_errors=True)
    assert status == 422 and "still open" in detail, (status, detail[:300])


# ---------------------------------------------------------------------------
# T2-2  The composite map PDF (always a job) has no _saving at all: with the
#       last composite PDF still open the job fails with the raw
#       "FzErrorSystem: code=2: cannot remove file ..." text.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def composite_pid(client, tmp_path_factory):
    """A project with the synthetic composite book and its map built (the
    composite tests' set-up, under a name of its own)."""
    tmp = tmp_path_factory.mktemp("t2proj")
    pid, facts = _project_with_book(client, tmp, _uid("T2-2 composite"),
                                    [("TST_400_TST_O.pdf", {})])
    did, f = next(iter(facts.items()))
    _place(client, pid, did, f, (2, 1), (250, 100), "NER_21.1")
    r = client.post(f"/api/projects/{pid}/composite/build", json={"drawing_id": did})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["state"] == "done"
    return pid


def test_t2_2_locked_composite_map_pdf_is_said_in_words(client, composite_pid):
    pid = composite_pid
    j = _wait(client, client.post(f"/api/projects/{pid}/composite-report").json()["job_id"])
    assert j["state"] == "done", j
    out = settings.exports_dir / f"project_{pid}" / j["result"]["file"]
    _block(out)
    try:
        j = _wait(client, client.post(f"/api/projects/{pid}/composite-report").json()["job_id"])
    finally:
        shutil.rmtree(out, ignore_errors=True)
    assert j["state"] == "error", j
    assert "still open" in (j.get("error") or ""), j.get("error")


# ---------------------------------------------------------------------------
# T2-3  level_key: "[-:]?" after the word LEVEL eats a minus sign, so a
#       basement written "LEVEL -1" (NIVEL -1, the European way) is level 1.
#       Two same-named NERs on level -1 and level 1 are no longer blocked as
#       "different_level" by the consolidation, and the occurrence links
#       treat the two floors as one. Before rc2 the two levels differed.
#       (Also inconsistent: "-1" and "LEVEL -1" do not compare equal.)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("basement", ["LEVEL -1", "NIVEL -1", "Level -2"])
def test_t2_3_a_basement_written_with_a_minus_is_not_the_floor_above(basement):
    from app.domain.consolidation import Candidate, compare, level_key
    upper = basement.replace("-", "")
    assert level_key(basement) != level_key(upper), (basement, level_key(basement))
    a = Candidate(ref=1, label="NER 7", kind="NER_NONCRITICAL", page=3, level=basement)
    b = Candidate(ref=2, label="NER 7", kind="NER_NONCRITICAL", page=9, level=upper)
    assert compare(a, b).blocking == "different_level"


# ---------------------------------------------------------------------------
# T2-4  T1-4 was fixed by comparing levels with level_key in three places,
#       and by writing a typed "2" as "LEVEL 02". A title "LEVEL 2 PLAN" is
#       still read as "LEVEL 2" (_LEVEL_IN_TITLE_RX keeps the digits as
#       printed), and the composite map groups sheets by the raw level text:
#       the typed sheet and the titled sheet of one level are two groups.
# ---------------------------------------------------------------------------

def test_t2_4_a_typed_level_and_a_title_level_are_one_composite_group():
    from app import services as S
    from app.domain import composite as C
    from app.domain.pdf_ingest import _LEVEL_IN_TITLE_RX
    titled = f"LEVEL {_LEVEL_IN_TITLE_RX.search('LEVEL 2 PLAN').group(1)}"
    typed = S.normalise_level("2")
    pages = [{"number": 1, "sheet_number": "", "level": titled, "key_plan": False, "mpu": 0.01},
             {"number": 2, "sheet_number": "", "level": typed, "key_plan": False, "mpu": 0.01}]
    groups = C.plan_groups(pages)
    assert len(groups) == 1, [(g["key"], g["pages"]) for g in groups]


# ---------------------------------------------------------------------------
# T2-5  T1-3 "wrapped, never cut": a note box taller than the sheet is put at
#       the top edge and still runs off the bottom - its last lines are not on
#       the PDF.
# ---------------------------------------------------------------------------

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


def _ner(note, x=300.0, y=150.0):
    return NER(key="A1", label="NER_20.1", kind=ElementKind.NER_NONCRITICAL,
               point=Point(x, y, 1), critical=False, notes=[note], user_note=note)


def test_t2_5_a_note_taller_than_the_sheet_is_not_cut(tmp_path):
    note = " ".join(f"word{i}" for i in range(300))
    doc = _marked(tmp_path, [_ner(note)], w=800, h=300)
    text = doc[0].get_text()
    missing = [f"word{i}" for i in range(300) if f"word{i}" not in text.split()]
    assert not missing, f"{len(missing)} words fall off the sheet, from {missing[0]}"


# ---------------------------------------------------------------------------
# T2-6  T1-2: characters the embedded note font has no glyph for (a check
#       mark, a double arrow, any CJK) are written as glyph 0 (.notdef):
#       a blank on paper and "\x00" in the text layer, without a word - the
#       rc1 "·" replaced by nothing. ("Checked ✓ cable route ⇒ B2" prints
#       "Checked   cable route   B2".)
# ---------------------------------------------------------------------------

def test_t2_6_every_character_of_a_note_is_drawn(tmp_path):
    note = "Checked ✓ cable route ⇒ B2"
    doc = _marked(tmp_path, [_ner(note)])
    blank = [chr(c[0]) for span in doc[0].get_texttrace()
             if span["font"].startswith("Nimbus") or "rrnote" in span["font"]
             for c in span["chars"] if c[1] == 0 and chr(c[0]).strip()]
    assert not blank, f"printed as nothing: {blank}"


# ---------------------------------------------------------------------------
# T2-8  _output_locked's words test ("permission denied", "access is
#       denied") also match a failure to READ the book: a source the child
#       could not open is reported as "the export could not be saved ... the
#       last export is probably still open - close it", which sends the person
#       to close a PDF that is not the problem.
# ---------------------------------------------------------------------------

def test_t2_8_a_book_that_cannot_be_read_is_not_called_a_locked_export(client, samples,
                                                                        monkeypatch):
    from app import isolate
    pid = client.post("/api/projects", json={"name": _uid("T2-8 unreadable book"),
                                             "venue_code": "SYN"}).json()["id"]
    with open(samples["pdf"], "rb") as fh:
        client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                    files={"file": ("plan.pdf", fh, "application/pdf")})

    def unreadable(target, src, out, **kw):
        raise isolate.IsolatedError(
            "FzErrorSystem", f"code=2: cannot open file '{src}': Permission denied")
    monkeypatch.setattr(isolate, "run", unreadable)
    status, detail = _post(client, f"/api/projects/{pid}/reports/pdf?background=false")
    assert status == 422, (status, detail)
    assert "still open" not in detail, detail
