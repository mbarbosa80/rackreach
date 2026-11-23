"""Deciding whether two things on the drawings are the same physical NER.

The problem this solves
-----------------------
A venue design book draws the same equipment room many times: once on the level
key plan, again on a zone detail, again on a services overlay. V1 recorded one
element per marker, so ``Critical NER 3`` on pages 5, 12 and 27 looked like three
rooms. Coverage then had three NERs where the building has one, and every number
downstream — port load, redundancy, "areas without a NER" — inherited the error.

The rule this module follows
----------------------------
Merge on proof, ask on suspicion, never assume. Concretely:

* An **identical normalised label** is necessary but not sufficient. Real venues
  do reuse a numbering series across buildings.
* Agreement on **level and building** is what turns a matching label into proof.
  Where the page metadata does not state a level, the score is capped below the
  automatic threshold — an unknown is not an agreement.
* A **conflict** on level or building is disqualifying no matter how well the
  labels match.
* Everything between "proof" and "no relation" becomes a question with its
  evidence attached, because a score with no sentences behind it is not something
  an engineer can review.

Every comparison returns the evidence *and* the differences. The screen shows
both. A reviewer who only ever sees the reasons to merge will merge.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .sheetnames import page_label

#: score at or above which the tool links without asking
AUTO_MERGE_THRESHOLD = 0.97
#: score below which two things are not even worth showing as a question
CANDIDATE_FLOOR = 0.55


def normalise_label(label: str) -> str:
    """``Critical NER 3`` / ``CRITICAL-NER-03`` / ``critical  ner 3`` → one form.

    Numeric tokens are stripped of leading zeros because real sheets tag the same
    room as both ``NER 03`` and ``NER 3``.
    """
    if not label:
        return ""
    s = unicodedata.normalize("NFKD", str(label))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    tokens: list[str] = []
    buf: list[str] = []
    for ch in s:
        if ch.isalnum():
            buf.append(ch)
        elif buf:
            tokens.append("".join(buf))
            buf = []
    if buf:
        tokens.append("".join(buf))
    return " ".join(str(int(t)) if t.isdigit() else t for t in tokens)


def label_series(label: str) -> tuple[str, Optional[int]]:
    """Split ``critical ner 3`` into its series (``critical ner``) and index (3)."""
    n = normalise_label(label)
    if not n:
        return "", None
    parts = n.split()
    if parts and parts[-1].isdigit():
        return " ".join(parts[:-1]), int(parts[-1])
    return n, None


@dataclass
class Candidate:
    """Everything the comparison is allowed to look at.

    Deliberately a flat value object rather than an ORM row so the same scoring
    runs over V1 elements during migration, over canonical assets in the review
    queue, and over TVDB detections later, without three copies of the rule.
    """

    ref: object
    label: str
    kind: str = ""
    page: Optional[int] = None
    level: Optional[str] = None
    building: Optional[str] = None
    document_id: Optional[int] = None
    space_code: Optional[str] = None
    space_name: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    nearby_text: str = ""
    confirmed_separate_from: frozenset = field(default_factory=frozenset)


@dataclass
class Comparison:
    score: float
    evidence: list[str]
    differences: list[str]
    blocking: Optional[str] = None

    @property
    def auto_mergeable(self) -> bool:
        return self.blocking is None and self.score >= AUTO_MERGE_THRESHOLD

    @property
    def is_candidate(self) -> bool:
        return self.blocking is None and self.score >= CANDIDATE_FLOOR

    def to_dict(self) -> dict:
        return {"score": round(self.score, 3), "evidence": self.evidence,
                "differences": self.differences, "blocking": self.blocking,
                "auto_mergeable": self.auto_mergeable}


def _norm(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


# The word in front and what separates it: "LEVEL 02", "LEVEL: 2", "LVL-3"
# (a dash straight after the word). A minus after a space is a sign:
# "LEVEL -1" is a basement, not level 1 (rc3, T2-3).
# 5.9.0 (F2-2): the word may touch the number ("LEVEL2", "FLOOR2", "LVL2", as
# a title block can print it) and "L-1" is the basement, as normalise_level
# reads them - the two must agree, or the composite map groups two sheets the
# merge calls different floors.
# 5.9.0-rc3 (T4-3): also a lettered level touching the word or after a dash
# ("LEVELB1", "FLOORP2", "LEVEL-B1"), read as "LEVEL B1" by normalise_level.
_LEVEL_WORD_RX = re.compile(
    r"^(?:(?:LEVEL|LVL|FLOOR|NIVEL|NÍVEL)(?:\s*:\s*|-(?=[A-Z]?\d)|\s+|(?=-?[A-Z]?\d))|L(?=-?\d))",
    re.I)
_LEVEL_NUM_RX = re.compile(r"^(-?)([A-Z]?)0*(\d+)([A-Z]?)$")


def level_key(v: Optional[str]) -> Optional[str]:
    """A level as something to compare (5.7.0-rc2, T1-4): ``LEVEL 02``,
    ``Level 2``, ``L2`` and ``2`` are one level. The word in front and the
    leading zeros do not count; ``B1``, ``01M`` and ``MEZZANINE`` keep their
    letters."""
    s = " ".join(str(v or "").split()).upper()
    if not s:
        return None
    s = _LEVEL_WORD_RX.sub("", s).replace(" ", "")
    m = _LEVEL_NUM_RX.match(s)
    if m:
        s = f"{m.group(1)}{m.group(2)}{int(m.group(3))}{m.group(4)}"
    return s or None


#: A level typed on the Scale screen: "02", "B1", "L3", "level 2", "LEVEL 02",
#: "Mezzanine" - kept as the drawing titles write it, "LEVEL <x>".
_LEVEL_SHORT_RX = re.compile(
    r"^(?:(?:LEVEL|LVL|FLOOR|NIVEL|NÍVEL)(?:\s*:\s*|-(?=[A-Z]?\d)|\s+)?|L(?=-?\d))?(-?[A-Z]?\d{1,3}[A-Z]?)$",
    re.I)


#: The longest level a person may type (5.8.0, T3-6), counted as stored.
LEVEL_MAX = 40

#: Characters that show as nothing but are not format characters (5.9.0-rc3,
#: T4-4): Hangul fillers, the blank braille pattern, the combining grapheme
#: joiner, Khmer inherent vowels, Mongolian free variation selectors. The
#: variation selectors (U+FE00-FE0F, U+E0100-E01EF) are refused by range.
#: Kept as typed by the NFKC step (5.9.0-rc5): the ordinal indicators ª º
#: and the superscript digits ¹ ² ³.
_KEEP_AS_TYPED = frozenset({0x00AA, 0x00BA, 0x00B9, 0x00B2, 0x00B3})

_BLANK_LOOKING = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x034F,
                            0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F})


def checked_level(raw) -> Optional[str]:
    """A level a person typed, checked and written as stored (5.9.0).

    One rule for the NER's Level field and the Scale screen's page level
    (F2-1): no control or invisible character (a zero-width space looks
    blank, a direction override turns the text around), and at most
    :data:`LEVEL_MAX` characters *as stored* - refused, never cut, also when
    upper-casing makes the text longer ("ß" -> "SS", F2-6). Empty -> ``None``.
    Raises ``ValueError`` with the message to show."""
    import unicodedata
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("a level is text")
    # Compatibility forms written plainly first (5.9.0-rc3): a full-width
    # "０２" is "02", so it is LEVEL 02 for the composite map as well. Not the
    # ordinal indicators, superscripts or squared units (5.9.0-rc5, T5-1):
    # "2ª" would become "2A", a real lettered level - a false pairing; and
    # "2º" / "1º andar" would gain a letter O.
    # Composed first (5.9.0-rc6, T6-1): a decomposed "Ni\u0301vel" (a macOS
    # file name, some PDF text) is "Nível", so it is LEVEL 02 / the same
    # "TÉRREO" a person types; the per-character step below cannot compose.
    raw = unicodedata.normalize("NFC", raw)
    raw = "".join(ch if ord(ch) in _KEEP_AS_TYPED or 0x2070 <= ord(ch) <= 0x209F
                  or 0x3300 <= ord(ch) <= 0x33FF else unicodedata.normalize("NFKC", ch)
                  for ch in raw)
    if any(ord(ch) < 32 or 127 <= ord(ch) < 160
           or unicodedata.category(ch) in ("Cf", "Co", "Cs")
           or ord(ch) in _BLANK_LOOKING
           or 0xFE00 <= ord(ch) <= 0xFE0F or 0xE0100 <= ord(ch) <= 0xE01EF
           for ch in raw):
        raise ValueError("a level cannot contain control or invisible characters")
    full = normalise_level(raw, cut=False)
    if full is not None and len(full) > LEVEL_MAX:
        raise ValueError(f"a level has at most {LEVEL_MAX} characters")
    return full


def normalise_level(text: Optional[str], *, cut: bool = True) -> Optional[str]:
    """A level as the title blocks write it (``LEVEL 02``), or ``None``.
    ``cut`` keeps the old 40-character cut for text read off a drawing."""
    v = " ".join(str(text or "").split()).strip()
    if not v:
        return None
    m = _LEVEL_SHORT_RX.match(v)
    if m:
        # 5.7.0-rc2 (T1-4): "2" is written "LEVEL 02", as the title blocks
        # and the drawing numbers write it, so the two read the same.
        part = m.group(1).upper()
        d = re.match(r"^([A-Z]?)(\d+)([A-Z]?)$", part)     # "-1" stays "-1"
        if d and not d.group(1) and len(d.group(2)) == 1:
            part = f"{d.group(1)}0{d.group(2)}{d.group(3)}"
        return f"LEVEL {part}"
    return v.upper()[:LEVEL_MAX] if cut else v.upper()


def compare(a: Candidate, b: Candidate) -> Comparison:
    """Score whether ``a`` and ``b`` are the same physical room."""
    evidence: list[str] = []
    differences: list[str] = []

    if b.ref in a.confirmed_separate_from or a.ref in b.confirmed_separate_from:
        return Comparison(0.0, [], ["a reviewer has already declared these different NERs"],
                          blocking="declared_separate")

    la, lb = normalise_label(a.label), normalise_label(b.label)
    if not la or not lb:
        return Comparison(0.0, [], ["one of the markers carries no label"],
                          blocking="no_label")

    series_a, idx_a = label_series(a.label)
    series_b, idx_b = label_series(b.label)

    if la == lb:
        evidence.append(f"identical normalised label “{la}”")
        score = 0.90
    elif series_a and series_a == series_b and idx_a is not None and idx_b is not None:
        # Same series, different number: NER 4 and NER 5 are two rooms, not one.
        return Comparison(
            0.0, [f"same label series “{series_a}”"],
            [f"different index in the series: {idx_a} vs {idx_b}"],
            blocking="different_index")
    else:
        return Comparison(0.0, [], [f"labels differ: “{a.label}” vs “{b.label}”"],
                          blocking="different_label")

    # --- level and building: what turns a matching label into proof ----------
    lvl_a, lvl_b = level_key(a.level), level_key(b.level)
    bld_a, bld_b = _norm(a.building), _norm(b.building)

    if bld_a and bld_b and bld_a != bld_b:
        return Comparison(0.0, evidence,
                          [f"different buildings: {a.building} vs {b.building}"],
                          blocking="different_building")
    if lvl_a and lvl_b and lvl_a != lvl_b:
        return Comparison(0.0, evidence,
                          [f"different levels: {a.level} vs {b.level}"],
                          blocking="different_level")

    if lvl_a and lvl_b:
        evidence.append(f"both on level {a.level}")
        score += 0.05
    else:
        differences.append("level not stated on at least one page — agreement unproven")
    if bld_a and bld_b:
        evidence.append(f"both in building {a.building}")
        score += 0.02

    # --- supporting signals --------------------------------------------------
    ca, cb = _norm(a.space_code), _norm(b.space_code)
    if ca and cb:
        if ca == cb:
            evidence.append(f"same space code {a.space_code}")
            score += 0.04
        else:
            differences.append(f"space codes differ: {a.space_code} vs {b.space_code}")
            score -= 0.20

    if a.kind and b.kind:
        if a.kind == b.kind:
            evidence.append(f"same marker kind ({a.kind})")
            score += 0.02
        else:
            differences.append(f"marker kind differs: {a.kind} vs {b.kind}")
            score -= 0.15

    if a.page is not None and b.page is not None:
        if a.page == b.page:
            evidence.append(f"both tagged on {page_label(a.page)}")
            if None not in (a.x, a.y, b.x, b.y):
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d < 1.0:
                    evidence.append("markers sit on top of each other (duplicate tag)")
                    score += 0.03
                else:
                    differences.append(
                        f"{d:.0f} drawing units apart on the same page — could be two rooms")
                    score -= 0.10
        else:
            evidence.append(f"appears on {page_label(a.page)} and {page_label(b.page)}")

    if a.document_id is not None and b.document_id is not None:
        if a.document_id == b.document_id:
            evidence.append("same source document")
        else:
            differences.append("different source documents")

    na, nb = _norm(a.space_name), _norm(b.space_name)
    if na and nb and na == nb:
        evidence.append(f"same room name “{a.space_name}”")
        score += 0.03

    if not (lvl_a and lvl_b):
        # An unknown is never an agreement. Hold below the automatic threshold so
        # the pair is asked about instead of assumed.
        score = min(score, AUTO_MERGE_THRESHOLD - 0.01)

    return Comparison(max(0.0, min(1.0, score)), evidence, differences)


def group_candidates(items: Sequence[Candidate]) -> list[tuple[Candidate, Candidate, Comparison]]:
    """Every pair worth showing a human, strongest first.

    Only pairs sharing a normalised label are compared, so this is linear in the
    number of NERs rather than quadratic — which matters on a venue with 26 rooms
    drawn across 34 pages, and much more on a games-wide project.
    """
    buckets: dict[str, list[Candidate]] = {}
    for c in items:
        key = normalise_label(c.label)
        if key:
            buckets.setdefault(key, []).append(c)

    out: list[tuple[Candidate, Candidate, Comparison]] = []
    for bucket in buckets.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                cmp_ = compare(bucket[i], bucket[j])
                if cmp_.is_candidate:
                    out.append((bucket[i], bucket[j], cmp_))
    out.sort(key=lambda t: -t[2].score)
    return out
