"""FastAPI application entrypoint. ``uvicorn api.main:app`` / ``TestClient(app)``.

Wires the per-domain routers under ``/api`` and, when a built SPA exists at ``web/dist``, serves
it statically at ``/`` for single-process demo mode. The static mount is added AFTER the API
routers so ``/api/*`` always wins the route match (the SPA only catches everything else).
"""

import pathlib

from fastapi import FastAPI

from api.routers import conflicts, congestion, operators, review, satellites, stats

app = FastAPI(
    title="Orbital Economy Terminal API",
    description="Read-only JSON API over the satellite identity graph and fact layer.",
    version="1.0.0",
)

app.include_router(stats.router, prefix="/api")
app.include_router(satellites.router, prefix="/api")
app.include_router(conflicts.router, prefix="/api")
app.include_router(operators.router, prefix="/api")
app.include_router(congestion.router, prefix="/api")
app.include_router(review.router, prefix="/api")


_WEB_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
if (_WEB_DIST / "index.html").exists():
    # Imported lazily so the API has no hard dependency on the SPA being built.
    from fastapi.staticfiles import StaticFiles

    class _SpaStaticFiles(StaticFiles):
        """StaticFiles that falls back to index.html so SPA deep links survive hard refresh.

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
                return await super().get_response("index.html", scope)
            if response.status_code == 404 and not is_api:
                response = await super().get_response("index.html", scope)
            return response

    app.mount("/", _SpaStaticFiles(directory=str(_WEB_DIST), html=True), name="spa")
