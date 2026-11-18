"""5.7.0 - the level of a page typed on the Scale screen (Marcelo, 16 Nov
2026: only where the drawing does not make the level clear). A colleague's
question - is there a floor or level input? - had no answer but the title
block: a sheet whose title states no level could never be told apart."""
from __future__ import annotations

import os
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="ner-level570-test-")
os.environ.setdefault("NER_DATA_DIR", TMP)
os.environ.setdefault("NER_DATABASE_URL", f"sqlite:///{TMP}/test.sqlite3")

from app import migrate                                            # noqa: E402
from app import services as S                                      # noqa: E402
from app.db import SessionLocal, init_db                           # noqa: E402
from app.domain.pdf_ingest import ElementKind                      # noqa: E402
from app.models import (AuditLog, Criticality, Drawing, KeepDecision,  # noqa: E402
                        NerAsset, NerStatus, Page, Project)


@pytest.fixture
def db():
    init_db()
    with SessionLocal() as session:
        yield session


@pytest.fixture
def project(db):
    p = Project(name=f"level-{os.urandom(4).hex()}")
    db.add(p)
    db.flush()
    d = Drawing(project_id=p.id, filename="v.pdf", stored_path="v.pdf",
                sha256="0" * 64, page_count=2)
    db.add(d)
    db.flush()
    db.add(Page(drawing_id=d.id, number=1, width=800, height=600, level="LEVEL 01",
                sheet_number="TST-401-O"))
    db.add(Page(drawing_id=d.id, number=2, width=800, height=600, level=None,
                sheet_number="TST-402-O"))
    db.add(NerAsset(project_id=p.id, code=f"NER-{os.urandom(3).hex()}", display_name="NER_20.1",
                    kind=ElementKind.NER_NONCRITICAL, classification=Criticality.NON_CRITICAL,
                    status=NerStatus.IDENTIFIED, keep_decision=KeepDecision.UNDECIDED,
                    canonical_page=2, canonical_drawing_id=d.id, canonical_x=1, canonical_y=1,
                    position_known=True, level=None))
    db.commit()
    return p


@pytest.mark.parametrize("typed, stored", [
    ("02", "LEVEL 02"), ("b1", "LEVEL B1"), ("Level 3", "LEVEL 03"), ("3", "LEVEL 03"), ("L04", "LEVEL 04"),
    ("  mezzanine  ", "MEZZANINE"), ("", None),
])
def test_the_level_is_written_as_the_titles_write_it(typed, stored):
    assert S.normalise_level(typed) == stored


def test_a_page_with_no_level_takes_one_and_its_ner_follows(db, project):
    out = S.set_page_level(db, project, 2, "02", actor="tester")
    assert out["level"] == "LEVEL 02" and out["level_source"] == "manual"
    assert out["ners"] == ["NER_20.1"]
    a = db.query(NerAsset).filter_by(project_id=project.id).one()
    assert a.level == "LEVEL 02"
    assert db.query(AuditLog).filter_by(project_id=project.id, action="page.level_set").count() == 1
    # A level typed before can be changed and taken back.
    S.set_page_level(db, project, 2, "03", actor="tester")
    db.refresh(a)
    assert a.level == "LEVEL 03"
    S.set_page_level(db, project, 2, "", actor="tester")
    pg = db.query(Page).filter_by(number=2).filter(Page.drawing_id == project.drawings[0].id).one()
    assert pg.level is None and pg.level_source is None


def test_a_level_the_drawing_states_is_never_overwritten(db, project):
    with pytest.raises(S.ConflictError):
        S.set_page_level(db, project, 1, "05", actor="tester")


def test_an_unknown_page_is_refused(db, project):
    with pytest.raises(S.ValidationError):
        S.set_page_level(db, project, 9, "05", actor="tester")


def test_the_guessed_level_clearing_keeps_a_typed_level(db, project):
    S.set_page_level(db, project, 2, "412", actor="tester")
    pg = db.query(Page).filter(Page.drawing_id == project.drawings[0].id, Page.number == 2).one()
    pg.level = "412"                      # a figure the guess clearer looks for
    db.commit()
    migrate.clear_guessed_levels(db, project)
    db.refresh(pg)
    assert pg.level == "412"
