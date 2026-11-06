"""Where the tiles are drawn (5.5.0, rc4): a small pool of their own.

The viewer and the composite map ask for tiles in bursts — dozens at once, at
every zoom and pan. Drawn on the server's general thread pool, a burst took
every thread of it, each waiting for the renderer (one tile at a time), and
everything else that needs a thread waited behind them: the independent tester
of rc3 saw sign-in take 32 s and the NER list 43 s behind 40 slow tiles.
MuPDF also keeps Python's interpreter lock for the whole of a tile, so a tile
that takes seconds to draw cannot be shared with anything; that is bounded by
drawing one tile at a time on a few threads of their own, never on the
general ones.

A tile whose request is gone (the person zoomed on, the browser dropped the
request) is not drawn if it has not started: the queue is checked each second.
(That needs the request's own ``receive``: behind ``@app.middleware("http")``
the hang-up was never seen — rc5 made that middleware a plain ASGI one.)

A tile already on disk is read before the queue (rc5): on the server's
general threads, which it holds for the few milliseconds of a database read
and a file read, never for a drawing. Queued with the others it waited behind
every undrawn tile asked before it (22 s, independent tester, rc4).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

#: Threads for tiles to be drawn: the renderer draws one at a time
#: (``_TILE_LOCK``); a tile drawn meanwhile by another request is read off
#: the disk by the next one waiting.
TILE_THREADS = 3
_pool = ThreadPoolExecutor(max_workers=TILE_THREADS, thread_name_prefix="ner-tiles")


async def run_tile(request: Request, fn: Callable[..., Any], *args,
                   cached: Optional[Callable[..., Any]] = None) -> Any:
    """``fn(*args)`` on the tile pool. A request gone before its turn comes
    gets nothing drawn (status 499, which nobody reads). ``cached(*args)``,
    when given, is asked first, off the pool: a result other than None is the
    answer."""
    if cached is not None:
        hit = await run_in_threadpool(cached, *args)
        if hit is not None:
            return hit
    fut = _pool.submit(fn, *args)
    waiting = asyncio.wrap_future(fut)
    while not waiting.done():
        await asyncio.wait({waiting}, timeout=1.0)
        if not waiting.done() and await request.is_disconnected():
            if fut.cancel():
                return Response(status_code=499)
    return waiting.result()
