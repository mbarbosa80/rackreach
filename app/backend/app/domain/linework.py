"""The strokes of a sheet, read without holding all of them (5.5.0).

Why
---
``page.get_drawings()`` returns every path of the page at once, each point
turned into a ``Point`` object. On an ordinary sheet that is a few thousand
paths. On a 900-series master plan (VDD_900, Nov 2026: four sheets of 8.6 m
by 5.6 m) it is 330,000 paths and 2.7 million strokes: 15 s and about 2 GB
for the list alone, and the leader index built over it (every thin stroke,
indexed by its ends) took another minute and more memory. On the work laptop,
with little RAM free, the import of that one book took 37 minutes.

What
----
:func:`stream_drawings` asks MuPDF for the same paths one at a time
(``get_cdrawings(callback=...)``) and keeps only those a ``keep`` test
accepts, converted exactly as ``get_drawings()`` converts them. What the
import needs from the strokes is small: the filled shapes that can be
arrowheads or the NER block, the tiny strokes that can be its lettering, and
the thin strokes near the labels that may carry a leader. The rest is
counted and let go.

One difference had to be undone: in the list form, a fill followed at once by
a stroke of the same path (PDF ``B``: fill and stroke) comes back as one
path of type ``"fs"``; in the callback form they arrive as two. They are
merged here the same way, so a kept path is identical to the one
``get_drawings()`` returns (checked page by page on every real book).
"""

from __future__ import annotations

from typing import Callable, Optional

#: The keys ``get_drawings()`` adds, as ``None``, to a fill or a stroke that
#: does not carry them.
_ALL_KEYS = ("closePath", "fill", "color", "width", "lineCap", "lineJoin", "dashes",
             "stroke_opacity", "fill_opacity", "even_odd")


def _same_path(fill: dict, stroke: dict) -> bool:
    """PyMuPDF's own rule (``jm_append_merge``): a stroke right after a fill
    in the list, with the same items, is that fill's path stroked. Text or an
    image drawn between them does not part them (independent tester, rc1)."""
    return stroke.get("type") == "s" and stroke.get("items") == fill.get("items")


def _merge(fill: dict, stroke: dict) -> dict:
    """The merged path as PyMuPDF makes it: the fill's keys kept, the
    stroke's added only where the fill has none, type "fs"."""
    out = dict(fill)
    for k, v in stroke.items():
        if k not in out:
            out[k] = v
    out["type"] = "fs"
    return out


def as_drawing(raw: dict) -> dict:
    """One raw path in the form ``page.get_drawings()`` gives it."""
    import pymupdf
    npath = dict(raw)
    kind = npath.get("type") or ""
    if not kind.startswith("clip"):
        npath["rect"] = pymupdf.Rect(npath["rect"])
    else:
        npath["scissor"] = pymupdf.Rect(npath["scissor"])
    if kind != "group":
        items = []
        for item in npath["items"]:
            cmd, rest = item[0], item[1:]
            if cmd == "re":
                item = ("re", pymupdf.Rect(rest[0]).normalize(), rest[1])
            elif cmd == "qu":
                item = ("qu", pymupdf.Quad(rest[0]))
            else:
                item = tuple([cmd] + [pymupdf.Point(i) for i in rest])
            items.append(item)
        npath["items"] = items
    if kind in ("f", "s"):
        for k in _ALL_KEYS:
            npath[k] = npath.get(k)
    return npath


#: How often (paths read) ``tick`` is told how far the reading is.
TICK_EVERY = 20000


def stream_drawings(page, keep: Optional[Callable[[dict], bool]] = None,
                    tick: Optional[Callable[[int], None]] = None
                    ) -> tuple[list[dict], int]:
    """The paths of ``page`` that ``keep`` accepts, as ``get_drawings()``
    would give them, in the same order, and how many paths the page has in
    all (``len(page.get_drawings())``). ``keep`` sees the raw path (rect and
    points as tuples, unrotated page coordinates); ``None`` keeps them all.
    ``tick(n)`` is called every ``TICK_EVERY`` paths with the number read so
    far — a sheet of 330,000 paths takes several seconds — and may raise to
    stop the reading (a cancelled job)."""
    out: list[dict] = []
    count = 0
    pending: Optional[dict] = None
    # An exception must never leave the callback: MuPDF's C++ loop does not
    # pass it on, the process crashes (a cancel during the read did, rc3). It
    # is kept, the rest of the page is let through untouched, and it is raised
    # once MuPDF has finished.
    failed: list[BaseException] = []

    def emit(p: dict) -> None:
        nonlocal count
        count += 1
        if tick is not None and count % TICK_EVERY == 0:
            tick(count)
        if keep is None or keep(p):
            out.append(as_drawing(p))

    def take(p: dict) -> None:
        if failed:
            return
        try:
            _take(p)
        except BaseException as exc:                   # noqa: BLE001 — raised below
            failed.append(exc)

    def _take(p: dict) -> None:
        nonlocal pending
        if pending is not None:
            held, pending = pending, None
            if _same_path(held, p):
                emit(_merge(held, p))
                return
            emit(held)
        if p.get("type") == "f":
            pending = p
            return
        emit(p)

    page.get_cdrawings(callback=take)
    if failed:
        raise failed[0]
    if pending is not None:
        emit(pending)
    return out, count
