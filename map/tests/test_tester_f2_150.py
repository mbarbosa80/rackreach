"""Independent tester F2, Map 1.4.0 (HEAD e4c9a40), 18 Nov 2026.

The level findings of the team edition (F2-1, F2-2, F2-3, F2-6), shown on the
Map edition. Every test fails on Map 1.4.0 as released. Synthetic data only.
"""
from __future__ import annotations

import os

import pytest

from app.db import SessionLocal
from app.domain.consolidation import Candidate, compare, level_key, normalise_level
from app.models import Drawing, Page
from tests.test_multi_book_410 import _ner, _two_books
from tests.test_ner_register import client  # noqa: F401
from tests.test_tester_140 import _merge_case, _patch, _row


def _page_project():
    from app.models import Project
    db = SessionLocal()
    p = Project(name=f"F2 map page level {os.urandom(4).hex()}")
    db.add(p)
    db.flush()
    d = Drawing(project_id=p.id, filename="b.pdf", stored_path="b.pdf", sha256="3" * 64,
                page_count=1)
    db.add(d)
    db.flush()
    db.add(Page(drawing_id=d.id, number=1, width=800, height=600, level=None,
                sheet_number="TST-401-O"))
    db.commit()
    return db, p


@pytest.mark.parametrize("bad", ["​", "‮20"])
def test_f2_1_page_level_refuses_invisible_characters(client, bad):
    from app import services as S
    db, p = _page_project()
    try:
        with pytest.raises((S.ValidationError, S.ConflictError)):
            S.set_page_level(db, p, 1, bad, actor="t")
    finally:
        db.close()


def test_f2_1b_page_level_over_40_is_refused_not_cut(client):
    from app import services as S
    db, p = _page_project()
    long = "MEZZANINE LEVEL WEST CONCOURSE UPPER TIER SECTION 12"
    try:
        try:
            out = S.set_page_level(db, p, 1, long, actor="t")
        except S.ValidationError:
            return
        assert out["level"] == long.upper(), out
    finally:
        db.close()


@pytest.mark.parametrize("raw", ["LEVEL2", "FLOOR2", "LVL2", "L-1"])
def test_f2_2_level_key_agrees_with_normalise_level(raw):
    assert level_key(raw) == level_key(normalise_level(raw))


def test_f2_2b_level2_and_level_02_are_one_floor():
    a = Candidate(ref="a", label="NER 4", page=1, level="LEVEL2")
    b = Candidate(ref="b", label="NER 4", page=2, level="LEVEL 02")
    assert compare(a, b).blocking != "different_level"


def test_f2_3_undo_keeps_a_level_typed_after_the_merge(client):
    pid, a, b = _two_books(f"F2 map undo {os.urandom(3).hex()}")
    with SessionLocal() as db:
        for pg in db.query(Page).join(Drawing).filter(Drawing.project_id == pid):
            pg.level = None
        db.commit()
    _ner(pid, a, 1, "NER 24", 100, 100)
    _ner(pid, b, 2, "NER 25", 200, 200)
    p, d = _row(client, pid, "NER 24"), _row(client, pid, "NER 25")
    assert _patch(client, pid, p["id"], "1").status_code == 200
    r = client.post(f"/api/projects/{pid}/ners/consolidate",
                    json={"primary_id": p["id"], "duplicate_id": d["id"], "force": True,
                          "justification": "the same NER"})
    assert r.status_code == 200, r.text
    cid = r.json()["consolidation_id"]
    assert _patch(client, pid, p["id"], "5").status_code == 200
    assert client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo").status_code == 200
    assert _row(client, pid, "NER 24")["level"] == "LEVEL 05"


def test_f2_6_a_level_that_grows_when_upper_cased_is_not_cut(client):
    pid, a, b = _two_books(f"F2 map eszett {os.urandom(3).hex()}")
    _ner(pid, a, 1, "NER 24", 100, 100)
    n = _row(client, pid, "NER 24")
    typed = "Großes Geschoß Straße Süd Außenbereich ß"
    r = _patch(client, pid, n["id"], typed)
    if r.status_code in (400, 409, 422):
        return
    assert _row(client, pid, "NER 24")["level"] == typed.upper()


def test_f2_7_a_long_unbroken_word_in_a_note_wraps_quickly():
    import time
    from app.domain.reports import fit_note
    t = time.time()
    lines, size, width = fit_note("https://example.invalid/" + "a" * 576, 800, 600)
    assert lines and time.time() - t < 1.0
