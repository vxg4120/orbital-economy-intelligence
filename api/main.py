"""FastAPI application entrypoint. ``uvicorn api.main:app`` / ``TestClient(app)``.

Wires the per-domain routers under ``/api`` and, when a built SPA exists at ``web/dist``, serves
it statically at ``/`` for single-process demo mode. The static mount is added AFTER the API
routers so ``/api/*`` always wins the route match (the SPA only catches everything else).
"""

import contextlib
import pathlib

from fastapi import FastAPI

from api import cache
from api.routers import (
    audit,
    buses,
    conflicts,
    congestion,
    environment,
    filings,
    operators,
    reachability,
    review,
    satellites,
    stats,
    stories,
)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start the background refreshers for the whole-catalog aggregates (see api.cache).

    Each runs on its own daemon thread, so startup is never delayed by a first compute and the
    process still exits cleanly. Under pytest the caches are disabled by an autouse fixture, so
    the threads idle without affecting what the endpoint tests observe.
    """
    cache.start_all()
    yield


app = FastAPI(
    title="Orbital Economy Terminal API",
    description="Read-only JSON API over the satellite identity graph and fact layer.",
    version="1.0.0",
    lifespan=_lifespan,
)

app.include_router(stats.router, prefix="/api")
app.include_router(satellites.router, prefix="/api")
app.include_router(conflicts.router, prefix="/api")
app.include_router(operators.router, prefix="/api")
app.include_router(buses.router, prefix="/api")
app.include_router(congestion.router, prefix="/api")
app.include_router(review.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(reachability.router, prefix="/api")
app.include_router(filings.router, prefix="/api")
app.include_router(environment.router, prefix="/api")
app.include_router(stories.router, prefix="/api")


@app.get("/api/health", tags=["health"])
def health():
    """Liveness for uptime checks: the process is up and routing. It deliberately does not touch
    the database, so a probe stays cheap and a slow query can never make the service look down.
    Mirrors exodossier's /api/health so one monitor config covers both apps."""
    return {"status": "ok", "service": "oei-api"}


_WEB_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
if (_WEB_DIST / "index.html").exists():
    # Imported lazily so the API has no hard dependency on the SPA being built.
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    from api import seo

    # The shell and the route table are read once: every fallback response is a rewrite of
    # the same string, and a deploy ships a new image rather than editing dist in place.
    _SHELL = (_WEB_DIST / "index.html").read_text(encoding="utf-8")
    _ROUTES = seo.load_routes()

    class _SpaStaticFiles(StaticFiles):
        """StaticFiles that falls back to the SPA shell so deep links survive hard refresh.

        The fallback is not the shell verbatim: api.seo rewrites its head for the requested
        path (title, description, canonical, social tags), and a path no route matches gets a
        noindex and a real 404 while the SPA still renders its NotFound view.

        Unknown ``/api/*`` paths are exempt: they keep their real 404 (a JSON error) instead of
        being swallowed into the SPA shell, so a mistyped/removed endpoint surfaces as an error
        rather than a 200 HTML body the JSON client can't parse.
        """

        async def get_response(self, path, scope):
            from starlette.exceptions import HTTPException as StarletteHTTPException

            # ``path`` is relative to the mount ('/'), so /api/nope -> 'api/nope'.
            is_api = path == "api" or path.startswith("api/")
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if is_api or exc.status_code != 404:
                    raise
                return self._shell(path, scope)
            if response.status_code == 404 and not is_api:
                response = self._shell(path, scope)
            return response

        @staticmethod
        def _shell(path, scope):
            body, status = seo.render_head(
                _SHELL, "/" + path, scope.get("query_string", b""), _ROUTES
            )
            return HTMLResponse(body, status_code=status)

    app.mount("/", _SpaStaticFiles(directory=str(_WEB_DIST), html=True), name="spa")
