"""Independent tester, RackReach 5.8.0-rc1 (17 Nov 2026).

Every test here FAILS on 5.8.0-rc1 (commit df1a6ed). Synthetic data only
(app.sample_data.build_all and the suite's own fixtures).

T3-1  The filtered-TEAP fix looks at every TEAP file the project ever had. A
      project that first imported its TEAP with the default venue filter and
      later imported a TEAP of every venue (allow_all_venues) still has the
      first file's filter on record, so the other venue's rows of the current
      TEAP are no longer said to be "another venue" on the Reconciliation
      sheet - they are filed under "no room on the drawing".
T3-2  The /UserUnit note fix does not hold on a rotated sheet: the box is
      drawn larger than the text and away from it, and the text runs out of
      its box.
T3-3  Merging a NER whose level was typed by hand into one with no level
      copies the level but not "by hand" (level_source); undoing the merge
      leaves the copied level on the primary.
T3-4  Invisible and bidi-control characters (U+200B zero width space, U+202E
      right-to-left override) and non-text JSON values (a list, an object,
      true) are accepted as a level and stored ("['X']", "TRUE").
T3-5  "Floor 2" and "L-1" are stored as typed ("FLOOR 2", "L-1") while the
      same levels typed as "2" / "-1" are "LEVEL 02" / "LEVEL -1": one level,
      two spellings in the Level column of the workbook.
T3-6  A long level is cut at 40 characters without a word.
"""
from __future__ import annotations

import io
import os

import openpyxl
import pymupdf
import pytest

from app.domain import reports as R
from tests.test_ner_register import TMP, client, samples  # noqa: F401
from tests.test_tester_570 import _full_project, _live_ners


def _ner(client, pid, nid):
    return next(n for n in client.get(f"/api/projects/{pid}/ners").json()["ners"]
                if n["id"] == nid)


def _patch(client, pid, nid, v):
    return client.patch(f"/api/projects/{pid}/ners/{nid}", json={"values": {"level": v}})


# ---------------------------------------------------------------------------
# T3-1
# ---------------------------------------------------------------------------

def _teap_two_venues(src: str, dst: str) -> str:
    """The synthetic TEAP plus three rows of another venue (OTH)."""
    wb = openpyxl.load_workbook(src)
    ws = wb["TEAP v2.0"]
    rows = [list(r) for r in ws.iter_rows(min_row=3, values_only=True) if r[0]]
    for i, r in enumerate(rows[:3]):
        r[0], r[1], r[2], r[6] = f"OTH{i:05d}", "OTH", "Other Venue", f"OTX.00{i}"
        ws.append(r)
    wb.save(dst)
    return dst


def _reconciliation(client, pid) -> str:
    r = client.post(f"/api/projects/{pid}/reports/xlsx")
    assert r.status_code == 200, r.text
    wb = openpyxl.load_workbook(io.BytesIO(client.get(r.json()["download"]).content))
    return " | ".join(str(c.value) for row in wb["Reconciliation"].iter_rows() for c in row
                      if c.value)


def test_t3_1_an_all_venue_teap_after_a_filtered_one_still_names_the_other_venue(client, samples):
    # _full_project imports the TEAP with the default filter (the project's SYN)
    pid = _full_project(client, samples, f"T580 venue {os.urandom(3).hex()}")
    path = _teap_two_venues(samples["teap"], os.path.join(TMP, f"two_venues_{pid}.xlsx"))
    with open(path, "rb") as fh:
        r = client.post(f"/api/projects/{pid}/teap",
                        files={"file": ("teap-all.xlsx", fh, "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet")})
    assert r.status_code == 201, r.text
    tid, proposed = r.json()["id"], r.json()["proposed"]
    # the newer TEAP, every venue, replaces the older one
    r = client.post(f"/api/projects/{pid}/teap/{tid}/import",
                    json={"sheet": proposed["sheet"], "header_row": proposed["header_row"],
                          "mapping": proposed["mapping"], "allow_all_venues": True})
    assert r.status_code == 200 and r.json()["venue_filter"] is None, r.text
    client.post(f"/api/projects/{pid}/matching/run")
    assert client.post(f"/api/projects/{pid}/analyse").status_code == 200
    text = _reconciliation(client, pid)
    # rc1: "no room on the drawing | 5 | ..." - the OTH rows among the rooms to find
    assert "another venue (OTH)" in text, text[text.find("Pending"):][:400]


# ---------------------------------------------------------------------------
# T3-2
# ---------------------------------------------------------------------------

def _note_pixels(rot: int, uu: float):
    """The pale fill of the note box and the dark text, as pixel boxes."""
    doc = pymupdf.open()
    p = doc.new_page(width=800, height=600)
    if uu != 1.0:
        doc.xref_set_key(p.xref, "UserUnit", str(uu))
    if rot:
        p.set_rotation(rot)
    sp = R.PageSpace(p)
    R.draw_note(sp, "word " * 40, centre=300, top=200, above=180)
    sp.commit()
    pg = pymupdf.open("pdf", doc.tobytes())[0]
    pix = pg.get_pixmap(dpi=72)
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


@pytest.mark.xfail(strict=True, reason="5.8.0 owner's backlog: a rotated sheet with a /UserUnit moves "
                   "every label of the app, not only notes (since 4.8.0); no book has one")
@pytest.mark.parametrize("rot,uu", [(90, 1.19), (90, 2.0), (270, 1.19)])
def test_t3_2_a_note_on_a_rotated_user_unit_sheet_sits_in_its_box(rot, uu):
    ref_box, _ = _note_pixels(0, 1.0)
    box, ink = _note_pixels(rot, uu)
    # the text inside its box
    assert box[0] - 1 <= ink[0] and ink[2] <= box[2] + 1 and box[1] - 1 <= ink[1] \
        and ink[3] <= box[3] + 1, (box, ink)
    # and the box the size it has on an ordinary sheet (rc1, 90 deg at 2.0:
    # 510 x 283 px instead of 145 x 41)
    w, h = box[2] - box[0], box[3] - box[1]
    rw, rh = ref_box[2] - ref_box[0], ref_box[3] - ref_box[1]
    assert abs(w - rw) <= 0.1 * rw and abs(h - rh) <= 0.1 * rh, (box, ref_box)


# ---------------------------------------------------------------------------
# T3-3
# ---------------------------------------------------------------------------

def _merge(client, pid, primary, dup):
    r = client.post(f"/api/projects/{pid}/ners/consolidate",
                    json={"primary_id": primary, "duplicate_id": dup, "force": True,
                          "justification": "the same NER"})
    assert r.status_code == 200, r.text
    return r.json()["consolidation_id"]


def test_t3_3_a_level_typed_by_hand_stays_by_hand_after_a_merge(client, samples):
    pid = _full_project(client, samples, f"T580 merge {os.urandom(3).hex()}")
    ns = {n["display_name"]: n for n in _live_ners(client, pid)}
    a, b = ns["NER_20.1"], ns["NER_22.1"]          # sheet 1, sheet 2; no level on either
    assert _patch(client, pid, b["id"], "3").status_code == 200
    _merge(client, pid, a["id"], b["id"])
    got = _ner(client, pid, a["id"])
    assert got["level"] == "LEVEL 03"
    # rc1: level_source None - the level the analyst chose is now unprotected
    assert got["level_source"] == "manual", got


def test_t3_3b_undoing_the_merge_takes_back_the_level_it_brought(client, samples):
    pid = _full_project(client, samples, f"T580 undo {os.urandom(3).hex()}")
    ns = {n["display_name"]: n for n in _live_ners(client, pid)}
    a, b = ns["NER_20.1"], ns["NER_22.1"]
    assert _ner(client, pid, a["id"])["level"] is None
    assert _patch(client, pid, b["id"], "3").status_code == 200
    cid = _merge(client, pid, a["id"], b["id"])
    r = client.post(f"/api/projects/{pid}/ner-consolidations/{cid}/undo")
    assert r.status_code == 200, r.text
    # rc1: NER_20.1 keeps LEVEL 03, typed on NER_22.1 only
    assert _ner(client, pid, a["id"])["level"] is None


# ---------------------------------------------------------------------------
# T3-4 / T3-5 / T3-6
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["\u200b", "\u202e2", ["x"], {"a": 1}, True])
def test_t3_4_an_invisible_or_non_text_level_is_refused(client, samples, bad):
    pid = _full_project(client, samples, f"T580 bad {os.urandom(3).hex()}")
    n = _live_ners(client, pid)[0]
    r = _patch(client, pid, n["id"], bad)
    assert r.status_code in (400, 409, 422), (r.status_code, _ner(client, pid, n["id"])["level"])


@pytest.mark.parametrize("typed,same_as", [("Floor 2", "2"), ("L-1", "-1")])
def test_t3_5_one_level_one_spelling(client, samples, typed, same_as):
    pid = _full_project(client, samples, f"T580 spell {os.urandom(3).hex()}")
    n = _live_ners(client, pid)[0]
    assert _patch(client, pid, n["id"], same_as).status_code == 200
    want = _ner(client, pid, n["id"])["level"]
    assert _patch(client, pid, n["id"], typed).status_code == 200
    assert _ner(client, pid, n["id"])["level"] == want


def test_t3_6_a_long_level_is_kept_or_refused_never_cut(client, samples):
    pid = _full_project(client, samples, f"T580 long {os.urandom(3).hex()}")
    n = _live_ners(client, pid)[0]
    text = "Mezzanine between the concourse and the north stand"   # 51 characters
    r = _patch(client, pid, n["id"], text)
    if r.status_code == 200:
        assert _ner(client, pid, n["id"])["level"] == text.upper()   # rc1: cut at 40
    else:
        assert r.status_code in (400, 422)
