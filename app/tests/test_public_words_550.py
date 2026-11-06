"""5.5.0 — the public edition's last internal words.

Marcelo, 5 Nov 2026: nothing of the project the tool was built for (NER, TER,
TEAP, VDB, DIVA and the like) on the site, the public GitHub or a public
version. Found while capturing the article's screens: the Scale screen's help
named NERc_01.1 / NER_20.1 and the TEC.010 room codes, the Inspector said
"DWISA", and the reasons the server writes still said TEAP / NER.

What this file holds:

* the interface copy, read the way ``t()`` reads it in the public edition,
  carries none of those words, codes or rack names (every English string the
  screens use, as listed in the Portuguese table);
* ``app/public_words.py`` words the text fields of a JSON answer under /api/
  in the public edition only — never a key, a code, a kind or a name, and a
  rack's name inside a reason stays the rack's name;
* answers that are not JSON (a tile, a file) and paths outside /api/ pass
  untouched, with a correct Content-Length.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.edition import example_names, public_terms
from app.public_words import PublicWords, reword

ROOT = Path(__file__).resolve().parents[1]
BAD = re.compile(r"\b(NERs?|NERc|TERs?|TEAPs?|VDBs?|DIVA|VTMs?|VTCMs?|DWISA|AAM)\b|NERc?_\d|TEC\.0\d\d")


def _interface_strings() -> list[str]:
    text = (ROOT / "frontend" / "src" / "lib" / "strings.pt.ts").read_text(encoding="utf-8")
    keys = re.findall(r'^\s{2}"((?:[^"\\]|\\.)*)":\s*$|^\s{2}"((?:[^"\\]|\\.)*)":\s*"', text, flags=re.M)
    out = []
    for a, b in keys:
        s = a or b
        out.append(s.encode("utf-8").decode("unicode_escape").encode("latin-1", "ignore").decode("utf-8", "ignore")
                   if "\\u" in s else s.replace('\\"', '"'))
    return out


def test_the_interface_copy_carries_no_internal_word_code_or_rack_name():
    strings = _interface_strings()
    assert len(strings) > 900
    left = [public_terms(example_names(s)) for s in strings]
    left = [s for s in left if BAD.search(s)]
    assert not left, "\n".join(x[:160] for x in left[:20])


def test_the_new_words():
    assert public_terms("its port is the space's DWISA line") == "its port is the space's wireless line"
    assert public_terms("Space code (AAM / VDB) — goes to the workbook") == \
        "Space code (design book) — goes to the workbook"
    assert "TEC." not in public_terms(
        "the TEAP's team annotations, TEC.003 / TEC.010 / TEC.020 rooms, NER room labels")
    assert example_names("NERc_01.1, NER_20.1, VHH_NERc_01.1") == "RKc_01.1, RK_20.1, VHH_RKc_01.1"


def test_a_rack_name_in_what_the_server_writes_is_the_racks_name():
    assert public_terms("NERc_01.1 serves it") == "NERc_01.1 serves it"
    out = reword({"reasons": ["nearest NER is NERc_01.1, within 90 m of the TEAP space"]})
    assert out["reasons"] == ["nearest rack is NERc_01.1, within 90 m of the demand list space"]


def _app() -> FastAPI:
    a = FastAPI()
    a.add_middleware(PublicWords)

    @a.get("/api/x")
    def x():
        return {"reasons": ["no room on the drawing for this TEAP space"],
                "name": "NER 7", "kind": "TER", "space_code": "TEC.010-001",
                "detail": "Upload the programme's Critical / Non-Critical NER sheet first.",
                "group_labels": {"pending": "Pending — needs a room or a NER"},
                "spaces": [{"notes": ["capacity unknown for this NER"], "ner_label": "NER 7"}],
                "job": {"stage": "looking for the NER symbol", "error": None}}

    @a.get("/api/png")
    def png():
        return Response(b"\x89PNG NER", media_type="image/png")

    @a.get("/other")
    def other():
        return {"reasons": ["the NER"]}

    return a


def test_the_public_edition_words_the_text_fields_only(monkeypatch):
    monkeypatch.setenv("RR_EDITION", "public")
    c = TestClient(_app())
    r = c.get("/api/x")
    j = r.json()
    assert int(r.headers["content-length"]) == len(r.content)
    assert j["reasons"] == ["no room on the drawing for this demand list space"]
    assert j["detail"] == "Upload the programme's Critical / Non-Critical rack sheet first."
    assert j["group_labels"] == {"pending": "Pending — needs a room or a rack"}
    assert j["spaces"][0]["notes"] == ["capacity unknown for this rack"]
    assert j["job"]["stage"] == "looking for the rack symbol"
    # keys, kinds, codes and names are data: untouched
    assert (j["name"], j["kind"], j["space_code"], j["spaces"][0]["ner_label"]) == \
        ("NER 7", "TER", "TEC.010-001", "NER 7")
    assert c.get("/api/png").content == b"\x89PNG NER"
    assert c.get("/other").json() == {"reasons": ["the NER"]}


def test_the_team_edition_is_untouched(monkeypatch):
    monkeypatch.setenv("RR_EDITION", "team")
    j = TestClient(_app()).get("/api/x").json()
    assert j["reasons"] == ["no room on the drawing for this TEAP space"]
    assert j["job"]["stage"] == "looking for the NER symbol"
