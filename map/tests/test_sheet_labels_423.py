"""4.2.3 — a sheet key never reaches the person.

Marcelo, 17 Oct 2026, on the NERs screen of a project with the 400 and 600
books: *"Não entendi essa numeração de páginas 10012 20004"*. Since 4.1.0 a sheet
of a multi-book project is named internally by ``drawing_id * 10000 + page``;
the API hands those keys out on purpose and the screens are meant to turn them
into ``400·12``. The *also on* line of the NERs screen printed them raw, and so
did eight other places in the front end (matching table, shafts, scale and
creation notices, the same-name and consolidation dialogs).

Two guards:

* the back end — a two-book project driven through matching, analysis, the NER
  register, consolidation candidates, exceptions, the workbook and the CSV;
  no text a person reads (any string in any response, any workbook cell) may
  carry a sheet key. Numeric ``page`` fields stay keys: that is the contract.
* the front end — every place found on 17 Oct now formats the key, and no raw
  ``pages.join`` / ``.page`` rendering is left in the screens.
"""

from __future__ import annotations

import csv
import pathlib
import re

import openpyxl
import pytest

from tests.test_multi_book_410 import _key, _ner, _room, _teap, _two_books
from tests.test_wireless_tie_250 import client  # noqa: F401
from app.config import settings

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"


def _strings(obj, path=""):
    """Every string in a JSON payload, with where it sits."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")


def _leaks(text: str, keys: set[int]) -> set[int]:
    return {int(t) for t in re.findall(r"(?<![\d.])\d{5,}(?![\d.])", text) if int(t) in keys}


@pytest.fixture(scope="module")
def two_books(client):
    pid, a, b = _two_books("two books labels 423")
    # One space on each book, the same code twice (one room seen on both),
    # a space nobody serves, and one NER drawn on both books.
    _room(pid, a, 1, "VJJ.010", 100, 100)
    _room(pid, b, 2, "VJJ.010", 300, 300)
    _room(pid, b, 1, "VJJ.011", 900, 650)
    _room(pid, a, 2, "VJJ.012", 500, 500)
    for code in ("VJJ.010", "VJJ.011", "VJJ.012", "VJJ.099"):
        _teap(pid, code)
    _ner(pid, a, 1, "NER 30", 120, 100)
    _ner(pid, b, 2, "NER 30", 320, 300)           # same name, another book
    _ner(pid, a, 2, "NER 31", 520, 500)
    assert client.post(f"/api/projects/{pid}/matching/run").status_code == 200
    keys = {_key(d, n) for d in (a, b) for n in (1, 2)}
    return pid, a, b, keys


GETS = [
    "", "/elements", "/drawing-spaces", "/teap-spaces", "/matches", "/zones", "/routes",
    "/verticals", "/spaces/assignments", "/occurrence-links", "/ners",
    "/ners/duplicates/candidates", "/ner-consolidations", "/ner-assets/positions",
    "/criticality/escalations", "/overlaps", "/audit",
]


# ---------------------------------------------------------------------------
# Front end
# ---------------------------------------------------------------------------

def _src(name: str) -> str:
    return (SRC / "pages" / name).read_text(encoding="utf-8")


def test_the_ners_screen_formats_also_on():
    # 4.4.0: each "also on" sheet is a PageButton (a link to the sheet), and
    # the button writes its number with sheetNo — so the label stays formatted.
    # RackReach Map: every page of a NER is a PageButton on the NERs list.
    src = _src("Ners.tsx")
    assert "<PageButton ctx={ctx} page={n}" in src
    pb = _src("panels.tsx")
    j = pb.index("export function PageButton")
    assert "sheetNo(p, page)" in pb[j:j + 1200]


@pytest.mark.parametrize("name", ["Ners.tsx", "panels.tsx", "Viewer.tsx"])
def test_no_raw_sheet_number_is_rendered(name):
    s = _src(name)
    bad = []
    # A list of sheet keys joined as they are.
    bad += re.findall(r"\.pages\??\.join\(", s)
    bad += re.findall(r"\.pages\.filter\((?:(?!\.map\()[^\n])*\)\.join\(", s)
    # A key put straight into JSX or a message.
    bad += re.findall(r"<td>\{m\.page \?\?", s)
    bad += re.findall(r"(?<!SHAFT-)\$\{(?:page(?!\}\$\{sheet\})|d\.lower_page|d\.upper_page|v\.lower_page|v\.upper_page|"
                      r"selected\.data\.lower_page|selected\.data\.upper_page)\}", s)
    bad += re.findall(r"p\.\$\{lower \? v\.upper_page : v\.lower_page\}", s)
    assert not bad, f"{name}: sheet keys rendered raw: {bad}"

