"""FastAPI application entrypoint. ``uvicorn api.main:app`` / ``TestClient(app)``.

Wires the per-domain routers under ``/api`` and, when a built SPA exists at ``web/dist``, serves
it statically at ``/`` for single-process demo mode. The static mount is added AFTER the API
routers so ``/api/*`` always wins the route match (the SPA only catches everything else).
"""

import pathlib

from fastapi import FastAPI

from api.routers import conflicts, congestion, operators, satellites, stats

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


_WEB_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
if (_WEB_DIST / "index.html").exists():
    # Imported lazily so the API has no hard dependency on the SPA being built.
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="spa")
