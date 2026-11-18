"""Independent tester, RackReach Map 1.4.0-rc1 (17 Nov 2026) - the findings of
the 5.8.0-rc1 pass that apply to the map edition (same code). Each test FAILS
on 1.4.0-rc1 (commit 2ec61dd). Synthetic data only.

T3-2  the /UserUnit note fix does not hold on a rotated sheet;
T3-3  a merge copies a hand-typed level without "by hand", and undo leaves it;
T3-4  invisible / bidi characters and non-text values accepted as a level;
T3-5  "Floor 2" / "L-1" stored with another spelling than "2" / "-1";
T3-6  a long level cut at 40 characters without a word.
(T3-1, the filtered TEAP, is team only.)
"""
from __future__ import annotations

import os

import pymupdf
import pytest

from app.db import SessionLocal
from app.domain import reports as R
from app.models import Drawing, Page
from tests.test_multi_book_410 import _ner, _two_books
from tests.test_ner_register import client  # noqa: F401


def _row(client, pid, name):
    return next(n for n in client.get(f"/api/projects/{pid}/ners").json()["ners"]
                if n["display_name"] == name)


def _patch(client, pid, nid, v):
    return client.patch(f"/api/projects/{pid}/ners/{nid}", json={"values": {"level": v}})


def _one(client, tag):
    pid, a, b = _two_books(f"140 tester {tag} {os.urandom(3).hex()}")
    _ner(pid, a, 1, "NER 24", 100, 100)
    return pid, a, b, _row(client, pid, "NER 24")


# --- T3-2 -------------------------------------------------------------------

def _note_pixels(rot: int, uu: float):
    doc = pymupdf.open()
    p = doc.new_page(width=800, height=600)
    if uu != 1.0:
        doc.xref_set_key(p.xref, "UserUnit", str(uu))
    if rot:
        p.set_rotation(rot)
    sp = R.PageSpace(p)
    R.draw_note(sp, "word " * 40, centre=300, top=200, above=180)
    sp.commit()
    pix = pymupdf.open("pdf", doc.tobytes())[0].get_pixmap(dpi=72)
    fill, ink = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            r, g, b = pix.pixel(x, y)[:3]
            if r > 235 and g > 235 and b < 238:
                fill.append((x, y))
            elif r < 120 and g < 120 and b < 120:
                ink.append((x, y))

    def bbox(pts):
        return (min(a for a, _ in pts), min(b for _, b in pts),
                max(a for a, _ in pts), max(b for _, b in pts))
    return bbox(fill), bbox(ink)


@pytest.mark.xfail(strict=True, reason="1.4.0 owner's backlog: a rotated sheet with a /UserUnit moves "
                   "every label of the app, not only notes (since 4.8.0); no book has one")
@pytest.mark.parametrize("rot,uu", [(90, 1.19), (90, 2.0), (270, 1.19)])
def test_t3_2_a_note_on_a_rotated_user_unit_sheet_sits_in_its_box(rot, uu):
    ref_box, _ = _note_pixels(0, 1.0)
    box, ink = _note_pixels(rot, uu)
    assert box[0] - 1 <= ink[0] and ink[2] <= box[2] + 1 and box[1] - 1 <= ink[1] \
        and ink[3] <= box[3] + 1, (box, ink)
    w, h = box[2] - box[0], box[3] - box[1]
    rw, rh = ref_box[2] - ref_box[0], ref_box[3] - ref_box[1]
    assert abs(w - rw) <= 0.1 * rw and abs(h - rh) <= 0.1 * rh, (box, ref_box)


# --- T3-3 -------------------------------------------------------------------

def _merge_case(client, tag):
    pid, a, b = _two_books(f"140 tester {tag} {os.urandom(3).hex()}")
    with SessionLocal() as db:                       # sheets that state no level
        for pg in db.query(Page).join(Drawing).filter(Drawing.project_id == pid):
            pg.level = None
        db.commit()
    _ner(pid, a, 1, "NER 24", 100, 100)
    _ner(pid, b, 2, "NER 25", 200, 200)
    p, d = _row(client, pid, "NER 24"), _row(client, pid, "NER 25")
    assert p["level"] is None
    assert _patch(client, pid, d["id"], "3").status_code == 200
    r = client.post(f"/api/projects/{pid}/ners/consolidate",
                    json={"primary_id": p["id"], "duplicate_id": d["id"], "force": True,
                          "justification": "the same NER"})
    assert r.status_code == 200, r.text
    return pid, p["id"], r.json()["consolidation_id"]


def test_t3_3_a_level_typed_by_hand_stays_by_hand_after_a_merge(client):
    pid, primary, _ = _merge_case(client, "merge")
    got = _row(client, pid, "NER 24")
    assert got["level"] == "LEVEL 03"
    assert got["level_source"] == "manual", got


def test_t3_3b_undoing_the_merge_takes_back_the_level_it_brought(client):
    pid, primary, cid = _merge_case(client, "undo")
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text
    assert _row(client, pid, "NER 24")["level"] is None


# --- T3-4 / T3-5 / T3-6 -----------------------------------------------------

@pytest.mark.parametrize("bad", ["​", "‮2", ["x"], {"a": 1}, True])
def test_t3_4_an_invisible_or_non_text_level_is_refused(client, bad):
    pid, _, _, n = _one(client, "bad")
    r = _patch(client, pid, n["id"], bad)
    assert r.status_code in (400, 409, 422), (r.status_code, _row(client, pid, "NER 24")["level"])


@pytest.mark.parametrize("typed,same_as", [("Floor 2", "2"), ("L-1", "-1")])
def test_t3_5_one_level_one_spelling(client, typed, same_as):
    pid, _, _, n = _one(client, "spell")
    assert _patch(client, pid, n["id"], same_as).status_code == 200
    want = _row(client, pid, "NER 24")["level"]
    assert _patch(client, pid, n["id"], typed).status_code == 200
    assert _row(client, pid, "NER 24")["level"] == want


def test_t3_6_a_long_level_is_kept_or_refused_never_cut(client):
    pid, _, _, n = _one(client, "long")
    text = "Mezzanine between the concourse and the north stand"
    r = _patch(client, pid, n["id"], text)
    if r.status_code == 200:
        assert _row(client, pid, "NER 24")["level"] == text.upper()
    else:
        assert r.status_code in (400, 422)
