"""5.3.0 - every sortable heading sorts by something the row actually has.

``useSort`` sorts by ``row[k]`` unless the heading passes ``get``. In
September 2026 several tables had headings keyed by their *label* ("Code",
"Page", "Status"): the arrow turned and nothing moved, on Demand per space,
Scale, Drawings, Access and the NER history. Nothing caught it. This test
reads every ``<x.Th k=...>`` without ``get`` and asks two things of the key:
it is a field name (lower case, no spaces - a label never is), and the same
file reads that field off a row somewhere else (``r.key``, ``key:`` ...).
A heading that sorts by a computed value must pass ``get``.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
TH = re.compile(r"<(\w+)\.Th\b([^>]*)>", re.S)
KEY = re.compile(r"""\bk=(?:"([^"]*)"|'([^']*)'|\{\s*["'`]([^"'`]*)["'`]\s*\})""")
FIELD = re.compile(r"^[a-z_][A-Za-z0-9_]*$")


def _headings():
    for f in sorted(SRC.rglob("*.tsx")):
        text = f.read_text(encoding="utf-8")
        for m in TH.finditer(text):
            attrs = m.group(2)
            if re.search(r"\bget\s*=", attrs):
                continue
            km = KEY.search(attrs)
            if not km:
                continue        # a computed key: not checkable here
            key = next(g for g in km.groups() if g is not None)
            yield f, text, m, key


def test_there_are_headings_to_check():
    assert sum(1 for _ in _headings()) > 30


def test_every_key_is_a_field_name():
    bad = [f"{f.name}: k={key!r}" for f, _, _, key in _headings() if not FIELD.match(key)]
    assert not bad, "a heading keyed by a label, not a field - pass get=: " + "; ".join(bad)


def test_every_key_is_read_from_a_row_in_the_same_file():
    bad = []
    for f, text, m, key in _headings():
        rest = text[:m.start()] + text[m.end():]
        rest = TH.sub("", rest)
        pat = re.compile(rf"(\.{key}\b|\b{key}\s*[:?]|\[\s*[\"']{key}[\"']\s*\]|[{{,]\s*{key}\s*[,}}])")
        if not pat.search(rest):
            bad.append(f"{f.name}: k={key!r}")
    assert not bad, ("a sortable heading whose key the file never reads off a row "
                     "(sorting by it would do nothing) - fix the key or pass get=: "
                     + "; ".join(sorted(set(bad))))
