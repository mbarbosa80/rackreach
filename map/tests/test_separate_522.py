"""5.2.2 — *Separate* takes the marker out of the group for good.

Marcelo, 3 Nov 2026, on the VBB project: NERc_10.1 was drawn in two places by its
TEC code. *Separate* on the NERs screen made a new NER, but the old one kept
its registered position on the separated marker — the marked PDF drew
NERc_10.1 under the renamed NER_34.1 — and nothing said the two were
different rooms. Now Separate asks for the new name, never accepts the
group's, puts the old NER's position back on its own markers and records the
pair as different rooms.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.test_ner_register import (_add_second_occurrence, _ners, client,  # noqa: F401
                                     samples)
from app import migrate
from app.db import SessionLocal
from app.domain.consolidation import normalise_label
from app.models import NerAsset, NerConsolidation, Page, Project


def _two_drawings(client, samples, name, code):
    pid = client.post("/api/projects", json={"name": name, "venue_code": code}).json()["id"]
    with open(samples["pdf"], "rb") as fh:
        client.post(f"/api/projects/{pid}/drawings?place_ners=true",
                    files={"file": ("plan.pdf", fh, "application/pdf")})
    with SessionLocal() as db:
        for pg in db.query(Page).join(Page.drawing).filter(
                Page.drawing.has(project_id=pid)).all():
            pg.level = "Level 1"
        db.commit()
    e1 = _add_second_occurrence(client, pid, "NER 61", 1, "Level 1")
    e2 = _add_second_occurrence(client, pid, "NER 61", 2, "Level 1")
    row = next(x for x in _ners(client, pid) if normalise_label(x["display_name"]) == "ner 61")
    assert row["occurrence_count"] == 2
    return pid, row, e1, e2


def test_separate_names_the_new_ner_and_moves_the_old_one_off_the_marker(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "Separate 522 a", "SPA")
    with SessionLocal() as db:
        old = db.get(NerAsset, row["id"])
        assert old.canonical_page == 1          # the first marker is the registered position
    r = client.post(f"/api/projects/{pid}/occurrences/{e1}/detach",
                    params={"reason": "another room", "name": "NER_34.1"})
    assert r.status_code == 200, r.text
    new_id = r.json()["new_ner_id"]
    assert r.json()["name"] == "NER_34.1"
    with SessionLocal() as db:
        old = db.get(NerAsset, row["id"])
        new = db.get(NerAsset, new_id)
        assert new.display_name == "NER_34.1"
        assert [e.label for e in new.occurrences] == ["NER_34.1"]
        # nothing of the old NER is left where the separated marker is
        assert old.canonical_page == 2
        assert [e.page for e in old.occurrences] == [2]
        rec = db.execute(select(NerConsolidation).where(
            NerConsolidation.project_id == pid, NerConsolidation.decision == "separate")).scalars().all()
        assert {(c.primary_asset_id, c.merged_asset_id) for c in rec} == {(old.id, new.id)}
    # the duplicate queue never offers the pair again
    dup = client.get(f"/api/projects/{pid}/ners/duplicates/candidates").json()
    assert not any({c["primary"]["id"], c["duplicate"]["id"]} == {row["id"], new_id}
                   for c in dup["candidates"])


def test_separate_refuses_the_group_name_and_another_ners_name(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "Separate 522 b", "SPB")
    r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach",
                    params={"name": "ner 61"})
    assert r.status_code == 409 and "separated from" in r.json()["detail"]
    other = next(x for x in _ners(client, pid) if x["id"] != row["id"])
    r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach",
                    params={"name": other["display_name"]})
    assert r.status_code == 409
    # still one NER with two markers: a refusal changes nothing
    again = next(x for x in _ners(client, pid) if x["id"] == row["id"])
    assert again["occurrence_count"] == 2
    # empty name: the next free name, as before
    r = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach")
    assert r.status_code == 200 and r.json()["name"]


def test_the_start_up_pass_records_an_older_separation(client, samples):
    pid, row, e1, e2 = _two_drawings(client, samples, "Separate 522 c", "VJJ")
    new_id = client.post(f"/api/projects/{pid}/occurrences/{e2}/detach").json()["new_ner_id"]
    with SessionLocal() as db:
        for c in db.execute(select(NerConsolidation).where(
                NerConsolidation.project_id == pid)).scalars():
            db.delete(c)                    # what a separation before 5.2.2 left
        db.commit()
        p = db.get(Project, pid)
        assert migrate.separate_detached_522(db, p) == {"separations_recorded": 1}
        assert migrate.separate_detached_522(db, p) == {"separations_recorded": 0}
        rec = db.execute(select(NerConsolidation).where(
            NerConsolidation.project_id == pid)).scalars().one()
        assert {rec.primary_asset_id, rec.merged_asset_id} == {row["id"], new_id}
        assert rec.decision == "separate"


def test_a_separated_marker_never_takes_the_groups_code_on_a_later_pass(client):
    """VBB, 3 Nov 2026: NERc_10.1 drawn twice from its TEC code; one marker
    separated. A later *Place NERs from the drawing* must not hand the code
    — and the sheets that print it — to the separated NER."""
    from tests.test_multi_book_410 import _two_books
    from tests.test_qa_fixes_420 import _room_at
    from app import services as S
    from app.models import Drawing, Element

    pid, a, b = _two_books("separate 522 code")
    code = "TEC.010-006"
    with SessionLocal() as db:
        p = db.get(Project, pid)
        S.adopt_drawn_rooms(db, p, db.get(Drawing, a),
                            [_room_at(1, 100, 100, code, True), _room_at(2, 400, 300, code, True)])
        db.commit()
        group = [x for x in db.execute(select(NerAsset).where(NerAsset.project_id == pid)).scalars()
                 if x.deleted_at is None and x.merged_into_id is None]
        assert len(group) == 1 and len(group[0].occurrences) == 2
        gid = group[0].id
        on2 = next(e.id for e in group[0].occurrences if e.page == 2)
    r = client.post(f"/api/projects/{pid}/occurrences/{on2}/detach",
                    params={"reason": "another room", "name": "NER_34.1"})
    assert r.status_code == 200, r.text
    sid = r.json()["new_ner_id"]
    with SessionLocal() as db:
        p = db.get(Project, pid)
        # the same book read again, then the other book that prints the code
        st = S.adopt_drawn_rooms(db, p, db.get(Drawing, a),
                                 [_room_at(1, 100, 100, code, True), _room_at(2, 400, 300, code, True)])
        db.commit()
        assert st["created"] == 0
        S.adopt_drawn_rooms(db, p, db.get(Drawing, b), [_room_at(1, 500, 500, code, True)])
        db.commit()
        sep, grp = db.get(NerAsset, sid), db.get(NerAsset, gid)
        assert sep.space_code is None and grp.space_code == code
        assert [(e.drawing_id, e.page) for e in sep.occurrences] == [(a, 2)]
        assert sorted((e.drawing_id, e.page) for e in grp.occurrences) == [(a, 1), (b, 1)]
        assert grp.canonical_page == 1 and grp.canonical_drawing_id == a
        assert any("separated from it" in t for t in (sep.classification_evidence or []))
        assert db.execute(select(Element).where(Element.project_id == pid,
                                                Element.ner_asset_id == sid)).scalars().all()
