"""Which edition this installation is, and the public vocabulary (5.4.0).

The team edition speaks the vocabulary of the project it was built for: NER,
TER, TEAP, VDB, DIVA. The public edition must not (Marcelo, 5 Nov 2026): its
screens, reports, marked-up PDFs and manuals say *rack*, *equipment room*,
*demand list*, *design book* and *AV network*. File, function and database
names in the code are not part of what is seen and stay as they are.

The edition is read from ``EDITION`` beside ``VERSION`` (the packager writes
``public`` into a public package; a team package and a development checkout
have none and are the team edition). ``RR_EDITION`` in the environment wins
over the file: the test suite pins ``team`` so the same tests hold inside a
public installation, and a public check can pin ``public``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EDITION_FILE = ROOT / "EDITION"


def edition() -> str:
    env = (os.environ.get("RR_EDITION") or "").strip().lower()
    if env in ("public", "team"):
        return env
    try:
        text = EDITION_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "team"
    return "public" if text == "public" else "team"


def is_public() -> bool:
    return edition() == "public"


#: Whole phrases first (they read better than word by word), then words.
PHRASES = [
    ("NER Spatial Coverage Analyzer", "RackReach"),
    ("NER Standardized Naming Structure", "rack naming structure"),
    ("Network Equipment Rack (NER)", "Network Equipment Rack"),
    ("network equipment rack (NER)", "network rack"),
    ("TEAP × drawing", "Demand × drawing"),
    ("TEAP x drawing", "Demand x drawing"),
    ("TEAP by NER", "Demand by rack"),
    ("NERs and ports", "Racks and ports"),
    ("TEAP and coverage", "Demand and coverage"),
    ("TEAP spaces with no ports", "Demand spaces with no ports"),
    ("TEAP files", "Demand files"),
]
WORDS = {
    "NERc": "critical rack", "NERs": "racks", "NER": "rack",
    "TERs": "equipment rooms", "TER": "equipment room",
    "TEAPs": "demand lists", "TEAP": "demand list",
    "VDBs": "design books", "VDB": "design book",
    "DIVA": "AV network",
    "VTMs": "technology managers", "VTCMs": "technology managers",
    "VTM": "technology manager", "VTCM": "technology manager",
}
_WORD_RX = re.compile(r"\b(" + "|".join(sorted(WORDS, key=len, reverse=True)) + r")\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?:]\s+|\n\s*)$")
_ARTICLES = [
    (re.compile(r"\b([Aa]) (equipment room|AV network)"), r"\1n \2"),
    (re.compile(r"\b([Aa])n (rack|critical rack|demand list|design book|technology manager)"), r"\1 \2"),
]


def _cap(prefix: str) -> bool:
    return not re.search(r"[A-Za-z]", prefix) or bool(_SENTENCE_START.search(prefix))


#: Acronyms that do not make a string a capitalised title on their own.
_ACRONYMS = {"XLSX", "XLSM", "XLS", "CSV", "PDF", "IDF", "MDF", "COW", "AP", "LLD", "URL", "OK", "ID"}


def _shouting(s: str) -> bool:
    """A title written in capitals (``NER COVERAGE``), not a label that merely
    holds acronyms (``2 · TEAP (XLSX / XLSM / CSV)``)."""
    words = [w for w in re.findall(r"[A-Za-z]+", s) if w not in WORDS]
    return bool(words) and all(w.isupper() for w in words) and any(w not in _ACRONYMS for w in words)


def public_terms(s: str) -> str:
    """The public wording of one piece of text (always applied, edition aside)."""
    if not s or not isinstance(s, str):
        return s
    upper = _shouting(s)
    for a, b in PHRASES:
        s = s.replace(a, b)

    def word(m: re.Match) -> str:
        w = WORDS[m.group(1)]
        if _cap(s[:m.start()]):
            w = w[0].upper() + w[1:]
        return w

    out, last = [], 0
    for m in _WORD_RX.finditer(s):
        out.append(s[last:m.start()])
        out.append(word(m))
        last = m.end()
    out.append(s[last:])
    s = "".join(out)
    for rx, rep in _ARTICLES:
        s = rx.sub(rep, s)
    return s.upper() if upper else s


def term(s):
    """``public_terms`` in the public edition, the text unchanged in the team edition."""
    return public_terms(s) if is_public() and isinstance(s, str) else s


#: Names the app gives the racks it creates (``NERc_01.1`` / ``NER_20.1`` in the
#: team edition, ``RKc_01.1`` / ``RK_20.1`` in the public one).
def rack_prefix(critical: bool) -> str:
    if is_public():
        return "RKc" if critical else "RK"
    return "NERc" if critical else "NER"
