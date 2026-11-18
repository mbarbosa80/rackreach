"""An export that could not be WRITTEN, said in words (5.7.0-rc2, T1-5;
rc3, T2-1 / T2-2 / T2-8).

On Windows the last export of the same name, still open in a PDF reader or
in Excel, cannot be replaced: PyMuPDF raises ``FzErrorSystem`` ("cannot
remove file ..."), openpyxl a ``PermissionError``. Worded as a damaged book,
the message sent the person to re-import a good one. A failure to READ the
book (its own name in the message) is never called a locked export.
"""
from __future__ import annotations

from typing import Optional


def _name(path) -> str:
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


def output_locked(out, exc: BaseException, *, src=None):
    """A ``ValidationError`` in words when ``exc`` says ``out`` could not be
    written, ``None`` when the failure is something else."""
    from .services import ValidationError
    kind = getattr(exc, "kind", "") or type(exc).__name__
    msg = str(exc)
    if msg.startswith(f"{kind}: "):
        msg = msg[len(kind) + 2:]
    low = msg.lower()
    out_name, src_name = _name(out).lower(), _name(src).lower()
    if src_name and src_name in low and not (out_name and out_name in low):
        return None                               # the book itself, not the export
    filename = _name(getattr(exc, "filename", "") or "").lower()
    names_out = bool(out_name) and (out_name in low or filename == out_name)
    write_words = any(w in low for w in ("cannot remove file", "permission denied",
                                         "being used by another process",
                                         "access is denied", "cannot open file"))
    if names_out and (write_words or kind == "PermissionError"):
        return ValidationError(
            f"the export could not be saved as {_name(out)} ({kind}: {msg[:160]}). "
            f"The drawing and the TEAP are not the problem: the last export of this "
            f"file is probably still open (PDF reader or Excel) - close it and export "
            f"again.")
    return None


def saving(out, fn, *args, **kw):
    """Run ``fn`` (a save to ``out``); a locked output comes out in words."""
    try:
        return fn(*args, **kw)
    except Exception as exc:                          # noqa: BLE001 - FzErrorSystem too
        locked = output_locked(out, exc)
        if locked is None:
            raise
        raise locked from exc
