"""Active Impact API assembly.

The FastAPI app serves the JSON API under /api and the no-build PWA from
public/. Routers are included first; StaticFiles is mounted last at / so the API
always wins and every other path falls through to the static shell.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.tokens import InsufficientBalance
from app.auth import router as auth_router
from app.users import router as users_router
from app.projects import router as projects_router
from app.checkin import router as checkin_router
from app.tokens import router as tokens_router
from app.catalog import router as catalog_router
from app.images import router as images_router

PUBLIC = Path(__file__).resolve().parent.parent / "public"

# Changes on every (re)start; the PWA polls it and reloads open tabs on a new build.
STARTED_AT = str(int(time.time()))


class NoCacheStatic(StaticFiles):
    """Static files that must be revalidated on every load.

    The PWA's own service worker handles offline/perf caching (cache-first, bumped
    by version). At the HTTP layer we send `Cache-Control: no-cache` so browsers
    always revalidate and pick up new app code immediately after a deploy — no
    stale bundle when the service worker isn't active (first visit / dev / update).
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app = FastAPI(title="Active Impact")


@app.exception_handler(InsufficientBalance)
async def _insufficient_balance(request, exc):
    """The ledger's overdraft guard surfaces uniformly as 409."""
    return JSONResponse({"detail": "insufficient_balance"}, status_code=409)


api = APIRouter(prefix="/api")


@api.get("/version")
def version():
    """Build token — the PWA reloads open tabs when this changes (fresh code)."""
    return {"version": STARTED_AT}


@api.get("/health")
def health():
    """Liveness + DB probe. Target of the compose healthcheck and smoke test."""
    try:
        db.query("SELECT 1")
        return {"ok": True, "db": True}
    except Exception:
        return JSONResponse({"ok": False, "db": False}, status_code=503)


# Each module owns full sub-paths (no per-router prefix) so cross-cutting paths
# like /participations and /claims can live with their domain module.
api.include_router(auth_router)
api.include_router(users_router)
api.include_router(projects_router)
api.include_router(checkin_router)
api.include_router(tokens_router)
api.include_router(catalog_router)
api.include_router(images_router)

app.include_router(api)

# The PWA. html=True serves index.html at /. Hash routing means the browser only
# ever requests / from the server, so no SPA fallback route is needed.
app.mount("/", NoCacheStatic(directory=str(PUBLIC), html=True), name="static")
