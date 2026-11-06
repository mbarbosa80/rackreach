"""ASGI entry point.

The compiled front-end is served from ``backend/app/static`` so that running the
tool needs nothing but Python — no Node, no separate dev server, no Docker.
"""

from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

import math

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fastapi import Depends

from .api.auth_routes import current_user, project_guard, router as auth_router, tile_guard
from .api.composite_routes import router as composite_router
from .api.composite_routes import tiles_router as composite_tiles_router
from .api.ner_routes import router as ner_router
from .api.routes import router
from .api.routes import tiles_router as page_tiles_router
from .config import settings
from .db import init_db

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Python guesses a file's type from the Windows registry. On many machines
# (an editor or a runtime rewrote HKCR\.js) ``.js`` comes back as
# ``text/plain``, the browser refuses the module script and the page stays
# white with no error on screen. The types the interface needs are fixed here,
# whatever the registry says.
for _type, _ext in (("text/javascript", ".js"), ("text/javascript", ".mjs"),
                    ("text/css", ".css"), ("image/svg+xml", ".svg"),
                    ("application/wasm", ".wasm"), ("application/json", ".json")):
    mimetypes.add_type(_type, _ext)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # The drawing numbers of books imported before 10 Sep 2026 are read off
    # their title blocks after the application is up, not before: it is a
    # second a page on a real book, and the watchdog restarts anything that
    # has not answered for a minute. Each book commits on its own.
    import threading

    from . import migrate
    from .db import SessionLocal

    def _sheet_numbers() -> None:
        totals = migrate.backfill_all_sheet_numbers(SessionLocal)
        if any(totals.values()):
            print(f"sheet numbers read from the title blocks: {totals}", flush=True)
        # Same thread, same reason: a book imported before 2.4.1 that yielded
        # no space is read once more for its room-name labels.
        spaces = migrate.backfill_all_name_only_spaces(SessionLocal)
        if any(spaces.values()):
            print(f"name-only spaces: {spaces}", flush=True)
        # 2.14.0: a key plan has no spaces and a name-only label is a space
        # only when it reads as a known space name — once per book.
        cleaned = migrate.backfill_all_spaces_2140(SessionLocal)
        if any(cleaned.values()):
            print(f"2.14.0 spaces pass: {cleaned}", flush=True)

    threading.Thread(target=_sheet_numbers, name="sheet-numbers", daemon=True).start()

    # Folders a project removal moved aside and the server did not live to
    # delete (rc7) - in the background: a big one takes a while.
    def _leftovers() -> None:
        from .services import delete_removal_leftovers
        gone = delete_removal_leftovers()
        if gone:
            print(f"deleted what project removals left: {gone}", flush=True)

    threading.Thread(target=_leftovers, name="removal-leftovers", daemon=True).start()
    yield


app = FastAPI(title=settings.app_name, version=settings.version,
              description=settings.disclaimer, lifespan=lifespan)


def _finite(v):
    """NaN and Infinity are not JSON: echoed back in a 422 they made it a 500."""
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc: RequestValidationError):  # noqa: ARG001
    return JSONResponse(status_code=422,
                        content={"detail": jsonable_encoder(_finite(list(exc.errors())))})

# 1.2.0: the public edition's words on what the server writes (no-op in the team edition).
from .public_words import PublicWords  # noqa: E402
app.add_middleware(PublicWords)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class _WhoIsAsking:
    """Put the signed-in username where the audit log reads it (4.2.0).

    A plain ASGI middleware (5.5.0-rc5), not ``@app.middleware("http")``:
    behind Starlette's BaseHTTPMiddleware ``request.is_disconnected()`` never
    turns True, so a tile whose client had hung up was drawn anyway
    (independent tester, rc4: 96 abandoned tiles drawn, the next one waited
    66 s). Here the request's own ``receive`` reaches the route untouched."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import HTTPConnection

        from . import services as _S
        token = HTTPConnection(scope).cookies.get("ner_session")
        name = None
        if token:
            from . import auth as _A
            from .db import SessionLocal as _SL

            def _resolve():
                db = _SL()
                try:
                    u = _A.resolve_session(db, token)
                    return u.username if u is not None else None
                except Exception:                      # noqa: BLE001 — never block a request on this
                    return None
                finally:
                    db.close()
            # Off the event loop (5.5.0): waiting here for a database connection
            # held the loop itself, and with it every request, /healthz included.
            from starlette.concurrency import run_in_threadpool as _rt
            name = await _rt(_resolve)
        reset = _S.CURRENT_ACTOR.set(name)
        try:
            await self.app(scope, receive, send)
        finally:
            _S.CURRENT_ACTOR.reset(reset)


# Added last, so outermost — where the decorator put it before.
app.add_middleware(_WhoIsAsking)


# Sign-in and the data notice are reachable without an account; everything else
# requires one, and anything with a project in its path is additionally checked
# against who owns it.
app.include_router(auth_router, prefix="/api")
_guarded = [Depends(current_user), Depends(project_guard)]
app.include_router(router, prefix="/api", dependencies=_guarded)
app.include_router(ner_router, prefix="/api", dependencies=_guarded)
# 5.0.0: the composite map — one map per level of a book, built on request.
app.include_router(composite_router, prefix="/api", dependencies=_guarded)
# The tiles of the viewer and of the composite map (1.2.0), guarded without
# holding a database connection while they are drawn.
app.include_router(page_tiles_router, prefix="/api", dependencies=[Depends(tile_guard)])
app.include_router(composite_tiles_router, prefix="/api", dependencies=[Depends(tile_guard)])


@app.get("/healthz")
async def healthz() -> dict:
    # async (5.5.0): answered on the event loop, never queued behind work
    # waiting for a thread of the pool.
    from app.version import __build__
    return {"status": "ok", "version": settings.version, "build": __build__}


if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Serve the single-page app, letting the client router own the URL."""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
