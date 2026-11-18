"""5.7.0 - tester R5-3: a book imported under the 2.14.0 space rules got,
at the next server start, every label of it as a space. The import keeps a
name-only label only when it reads as a space name (AAM list + TEAP); a book
whose labels none of them do was stored with no space, and the 2.4.1
start-up pass for "books with no space" read it again with every label a
space. Owner's decision (16 Nov 2026): those labels stay out."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import tempfile

TMP = tempfile.mkdtemp(prefix="ner-test-570-r53-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/test.sqlite3")

import pymupdf                                                     # noqa: E402
from sqlalchemy import select                                      # noqa: E402

from app import migrate                                            # noqa: E402
from app import services as S                                      # noqa: E402
from app.db import SessionLocal, init_db                           # noqa: E402
from app.models import AuditLog, Drawing, DrawingSpace, Project    # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_t241", pathlib.Path(__file__).with_name("test_name_only_spaces_241.py"))
_t241 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_t241)


def _book(tmp_path) -> str:
    pdf = tmp_path / "frg-like.pdf"
    _t241._vdd_like_pdf(pdf)
    doc = pymupdf.open(str(pdf))
    doc.delete_page(0)
    doc.save(str(tmp_path / "one.pdf"))
    doc.close()
    return str(tmp_path / "one.pdf")


def _spaces(db, d):
    return db.execute(select(DrawingSpace).where(DrawingSpace.drawing_id == d.id)).scalars().all()


def test_a_book_read_by_the_current_rules_gets_nothing_at_the_next_start(tmp_path):
    init_db()
    with SessionLocal() as db:
        p = Project(name="r53-new", venue_code="TST")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="one.pdf", stored_path=_book(tmp_path),
                    sha256="0" * 64, page_count=1,
                    extract_summary={"spaces": 0, "spaces_pass": S.SPACES_PASS})
        db.add(d)
        db.commit()
        out = migrate.backfill_name_only_spaces(db, p)
        assert out["name_only_books_read"] == 0
        assert _spaces(db, d) == []
        db.refresh(d)
        assert d.extract_summary["name_only_pass"] == migrate.NAME_ONLY_PASS


def test_a_book_the_old_pass_already_filled_goes_through_the_rules_once(tmp_path):
    """What projects imported under 5.4-5.6 hold: the labels the start-up pass
    added. The 2.14.0 rules run once more on that book - a label that is no
    space name goes, one that is (or that a TEAP line is tied to) stays."""
    init_db()
    with SessionLocal() as db:
        p = Project(name="r53-old", venue_code="TST")
        db.add(p)
        db.flush()
        d = Drawing(project_id=p.id, filename="one.pdf", stored_path=_book(tmp_path),
                    sha256="0" * 64, page_count=1,
                    extract_summary={"spaces": 0, "spaces_pass": S.SPACES_PASS})
        db.add(d)
        db.flush()
        for name in ("Venue Technology Operations (VTO)", "TYP. SEE DETAIL 3"):
            db.add(DrawingSpace(project_id=p.id, drawing_id=d.id, page=1, space_code=None,
                                name=name, x=1, y=1, origin="extracted", confidence=0.6,
                                evidence=[]))
        db.add(AuditLog(project_id=p.id, action="drawing.spaces_backfilled", entity="drawing",
                        entity_id=d.id, actor="system",
                        payload={"pass": migrate.NAME_ONLY_PASS, "spaces": 2}))
        db.commit()
        migrate.cleanup_spaces_2140(db, p)
        names = sorted(s.name for s in _spaces(db, d))
        assert "TYP. SEE DETAIL 3" not in names
        db.refresh(d)
        assert d.extract_summary.get("name_only_570") is True
        # once: a space added by hand afterwards is not looked at again
        db.add(DrawingSpace(project_id=p.id, drawing_id=d.id, page=1, space_code=None,
                            name="NOT A ROOM", x=2, y=2, origin="extracted", confidence=0.6,
                            evidence=[]))
        db.commit()
        migrate.cleanup_spaces_2140(db, p)
        assert "NOT A ROOM" in [s.name for s in _spaces(db, d)]
