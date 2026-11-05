"""The public edition's words (5.4.0).

Marcelo, 5 Nov 2026: no NER, TER, TEAP, VDB or DIVA anywhere a member of the
public reads - screens, reports, marked-up PDFs, manuals. The racks the app
creates are RKc_01.1 / RK_20.1. Code, file and database names are not read
and stay as they are.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
BAD = re.compile(r"\b(NERs?|NERc|TERs?|TEAPs?|VDBs?|DIVA|VTMs?|VTCMs?)\b")
TASK_NAMES = ("NER Map Analyzer watchdog", "NER Map Analyzer")


def test_public_terms_read_as_general_words():
    from app.edition import public_terms
    assert public_terms("TEAP by NER") == "Demand by rack"
    assert public_terms("NERs and ports") == "Racks and ports"
    assert public_terms("Place the NERs the drawings state") == "Place the racks the drawings state"
    assert public_terms("a TER") == "an equipment room"
    assert public_terms("TER") == "Equipment room"
    assert public_terms("DIVA node") == "AV network node"
    assert public_terms("NER COVERAGE") == "RACK COVERAGE"
    # a rack name is a name: joined by an underscore, it is never touched
    assert public_terms("NERc_01.1 serves it") == "NERc_01.1 serves it"


def test_the_edition_decides(monkeypatch):
    from app import edition
    monkeypatch.setenv("RR_EDITION", "team")
    assert edition.term("TEAP by NER") == "TEAP by NER"
    monkeypatch.setenv("RR_EDITION", "public")
    assert edition.term("TEAP by NER") == "Demand by rack"


def test_public_rack_names(monkeypatch):
    from app import ner_register
    monkeypatch.setenv("RR_EDITION", "public")
    assert ner_register.sequential_name(1, True) == "RKc_01.1"
    assert ner_register.sequential_name(20, False) == "RK_20.1"
    assert ner_register.SEQUENTIAL_NAME_RX.match("RK_21.1").group(2) == "21"
    assert ner_register.SEQUENTIAL_NAME_RX.match("RKc_03.1").group(1) == "c"
    monkeypatch.setenv("RR_EDITION", "team")
    assert ner_register.sequential_name(1, True) == "NERc_01.1"


def test_the_public_interface_passes_every_string_through_the_words():
    i18n = (ROOT / "frontend/src/lib/i18n.ts").read_text(encoding="utf-8")
    assert "publicTerms(english" in i18n and "publicTerms(PT[english]" in i18n
    assert (ROOT / "frontend/src/lib/publicTerms.ts").exists()


def test_the_public_manuals_carry_no_internal_word():
    for rel in ("docs/MANUAL.html", "docs/INSTALL.html", "docs/QUICK-INSTALL.html",
                "docs/HISTORY.html", "CHANGELOG.md", "README.md", "NOTICE.txt"):
        p = ROOT / rel
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8")
        for k in TASK_NAMES:
            t = t.replace(k, "")
        t = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", t, flags=re.S)
        assert not BAD.search(t), (rel, BAD.findall(t)[:5])
