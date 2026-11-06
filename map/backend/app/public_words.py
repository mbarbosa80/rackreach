"""The public edition's words on what the server writes (5.5.0).

The screens pass every piece of interface copy through ``t()``, but some text
is written by the server: the reasons and notes of the analysis, the evidence
of a detection, the explanation of a demand line, a job's stage, an error.
In the public edition this middleware passes those fields of every JSON answer
under ``/api/`` through ``edition.public_terms`` on their way out.

Only those fields: a key, a code, a name or a kind is never touched, so
nothing the screens compare or send back changes. What a person typed into
a free-text field that is one of them (an audit justification) is worded the
same way; that is the price of a public edition that does not speak the
team's vocabulary. Nothing in the database changes.
"""

from __future__ import annotations

import json

from .edition import is_public, public_terms

#: The fields that hold text the server wrote for a person.
TEXT_FIELDS = frozenset({
    "reasons", "notes", "explanation", "warnings", "evidence", "classification_evidence",
    "exception", "reason", "group_label", "group_labels", "justification", "detail",
    "document_kind", "assumptions", "message", "error", "stage", "notice", "summary",
    "hint", "why", "messages", "problems",
})


def _words(v):
    if isinstance(v, str):
        return public_terms(v)
    if isinstance(v, list):
        return [_words(x) for x in v]
    if isinstance(v, dict):
        return {k: _words(x) for k, x in v.items()}
    return v


def reword(v):
    """``v`` with every text field (at any depth) in the public words."""
    if isinstance(v, dict):
        return {k: (_words(x) if k in TEXT_FIELDS else reword(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [reword(x) for x in v]
    return v


class PublicWords:
    """Pure ASGI middleware: JSON answers under /api/ only, public edition only."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/api/") \
                or not is_public():
            await self.app(scope, receive, send)
            return
        start: dict | None = None
        chunks: list[bytes] = []
        passthrough = False

        async def _send(message):
            nonlocal start, passthrough
            if message["type"] == "http.response.start":
                ctype = b""
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        ctype = v
                if not ctype.startswith(b"application/json"):
                    passthrough = True
                    await send(message)
                    return
                start = message
                return
            if passthrough or start is None:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            try:
                body = json.dumps(reword(json.loads(body)), ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
            except (ValueError, TypeError):
                pass
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send(dict(start, headers=headers))
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, _send)
