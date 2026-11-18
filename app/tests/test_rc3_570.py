"""5.7.0-rc3: what the rc2 tester left as risks, and the edges of the rc3 fixes."""
from __future__ import annotations

import os

import pytest

from app.domain.consolidation import level_key, normalise_level


def _uid(p):
    return f"{p} {os.urandom(4).hex()}"


@pytest.mark.parametrize("typed, stored", [("-1", "LEVEL -1"), ("Level -1", "LEVEL -1"),
                                           ("LVL-3", "LEVEL 03"), ("b2", "LEVEL B2")])
def test_a_basement_typed_with_a_minus_keeps_it(typed, stored):
    assert normalise_level(typed) == stored


def test_a_title_states_a_basement():
    from app.domain.pdf_ingest import _LEVEL_IN_TITLE_RX as R
    assert R.search("LEVEL -1 PLAN").group(1) == "-1"
    assert R.search("KEY PLAN - LEVEL 01").group(1) == "01"
    assert level_key("LEVEL -1") == level_key("-1") != level_key("LEVEL 1")


def test_a_map_built_under_the_title_spelling_is_the_same_group():
    from app.domain import composite as C
    assert C.group_key("", "LEVEL 2") == "LEVEL 02"
    assert C.same_group("LEVEL 2", "LEVEL 02") and C.same_group("412", "412")
    assert not C.same_group("412", "LEVEL 412") and not C.same_group("LEVEL 1", "LEVEL 02")


def test_a_note_with_symbols_is_drawn_in_the_fallback_font_or_as_a_question_mark():
    from app.domain.reports import NOTE_FALLBACK, NOTE_FONT, note_runs
    runs = note_runs("ok ✓ ⇒ 漢 😀")
    assert ("ok ", NOTE_FONT) == runs[0]
    assert any(fn == NOTE_FALLBACK and "✓" in t for t, fn in runs)
    assert runs[-1] == (" ?", NOTE_FONT)           # no font has it: "?", never nothing


def test_fit_note_makes_a_long_note_fit():
    from app.domain.reports import fit_note
    lines, size, width = fit_note(" ".join(["word"] * 2000), 800, 300)
    assert size * 1.25 * len(lines) <= 300 - 7 and width <= 800


def test_a_level_with_a_control_character_or_on_a_key_plan_is_refused():
    from app import services as S
    from app.db import SessionLocal, init_db
    from app.models import Drawing, Page, Project
    init_db()
    with SessionLocal() as db:
        p = Project(name=_uid("rc3 level guard"))
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="b.pdf", stored_path="b.pdf", sha256="1" * 64,
                    page_count=2)
        db.add(d)
        db.flush()
        db.add(Page(drawing_id=d.id, number=1, width=800, height=600, level=None,
                    sheet_number="TST-401-O"))
        db.add(Page(drawing_id=d.id, number=2, width=800, height=600, level=None,
                    sheet_number="TST-402-O", key_plan=True))
        db.commit()
        with pytest.raises(S.ValidationError):
            S.set_page_level(db, p, 1, "02\x07", actor="t")
        with pytest.raises(S.ValidationError):
            S.set_page_level(db, p, 2, "02", actor="t")
        assert S.set_page_level(db, p, 1, "02", actor="t") is not None


def test_a_csv_teap_takes_the_venue_filter(tmp_path):
    from app.domain.teap_import import read_csv
    f = tmp_path / "t.csv"
    f.write_text("Venue,Space,Name,Qty\nSYN,A.1,A,2\nOTH,B.1,B,3\nS Y N,C.1,C,1\n",
                 encoding="utf-8")
    mapping = {"venue_code": "Venue", "space_code": "Space", "space_name": "Name",
               "quantity": "Qty"}
    imp = read_csv(str(f), mapping, venue_filter="syn")
    assert [r.values["space_code"] for r in imp.rows] == ["A.1", "C.1"]
    assert any("1 row(s) of other venues" in w for w in imp.warnings)
    assert len(read_csv(str(f), mapping).rows) == 3


def test_the_locked_guard_reads_the_words_not_just_any_permission_error(tmp_path):
    from app.export_guard import output_locked
    out, src = tmp_path / "x_marked.pdf", tmp_path / "plan.pdf"

    class FzErrorSystem(Exception):
        pass
    assert output_locked(out, FzErrorSystem(f"code=2: cannot remove file '{out}'")) is not None
    assert output_locked(out, FzErrorSystem(f"code=2: cannot open file '{src}': Permission "
                                            f"denied"), src=src) is None
    assert output_locked(out, ValueError("bad page")) is None


def test_a_csv_teap_lists_its_venues_and_imports_the_one_chosen(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_ner_register import _sign_in
    c = TestClient(app)
    _sign_in(c)
    pid = c.post("/api/projects", json={"name": _uid("rc3 csv venues"),
                                        "venue_code": "SYN"}).json()["id"]
    f = tmp_path / "teap.csv"
    f.write_text("Venue Code,Space Code,Space Name,Product Code,Quantity,Network Ports\n"
                 "SYN,A.1,Alpha,SW,1,2\nOTH,B.1,Beta,SW,1,3\n", encoding="utf-8")
    with open(f, "rb") as fh:
        r = c.post(f"/api/projects/{pid}/teap", files={"file": ("teap.csv", fh, "text/csv")})
    assert r.status_code == 201, r.text
    tid, proposed = r.json()["id"], r.json()["proposed"]
    r = c.post(f"/api/projects/{pid}/teap/{tid}/preview-venues",
               json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                     "mapping": proposed["mapping"]})
    assert r.status_code == 200, r.text
    assert {v["venue_code"] for v in r.json()["venues"]} == {"SYN", "OTH"}
    assert r.json()["suggested"] == "SYN" and r.json()["csv"] is True
    r = c.post(f"/api/projects/{pid}/teap/{tid}/import",
               json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                     "mapping": proposed["mapping"], "venue_filter": "SYN"})
    assert r.status_code == 200, r.text
    spaces = c.get(f"/api/projects/{pid}/teap-spaces").json()
    spaces = spaces if isinstance(spaces, list) else spaces.get("spaces", [])
    codes = {s.get("space_code") for s in spaces}
    assert "A.1" in codes and "B.1" not in codes, codes
