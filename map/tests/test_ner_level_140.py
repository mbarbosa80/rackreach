"""Map 1.4.0 (team 5.8.0) - the NER's level as a field, like on the team edition."""
from __future__ import annotations

import os

from tests.test_ner_register import client  # noqa: F401
from tests.test_multi_book_410 import _ner, _two_books


def _row(client, pid, name):
    return next(n for n in client.get(f"/api/projects/{pid}/ners").json()["ners"]
                if n["display_name"] == name)


def test_a_level_chosen_by_hand_is_kept_and_empty_gives_back_the_sheet_level(client):
    pid, a, b = _two_books(f"140 level {os.urandom(3).hex()}")
    _ner(pid, a, 1, "NER 24", 100, 100)
    n = _row(client, pid, "NER 24")
    r = client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": "b1"}})
    assert r.status_code == 200, r.text
    got = _row(client, pid, "NER 24")
    assert got["level"] == "LEVEL B1" and got["level_source"] == "manual"
    assert got["page_levels"] == ["LEVEL 01"]
    r = client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": None}})
    assert r.status_code == 200, r.text
    got = _row(client, pid, "NER 24")
    assert got["level"] == "LEVEL 01" and got["level_source"] is None
    r = client.patch(f"/api/projects/{pid}/ners/{n['id']}", json={"values": {"level": "1\x00"}})
    assert r.status_code in (400, 409, 422), r.text
