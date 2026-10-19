"""Per-route document metadata for the SPA shell, written server-side.

The server sends the same ``index.html`` for every path, and the scrapers behind share cards and
most search crawlers never run the script that would fix the head up. So the shell's title,
description, canonical URL and social tags are rewritten here, per request, from the same table
the client reads: ``web/public/seo-routes.json`` (copied to ``web/dist`` by Vite). An unmatched
path gets a ``noindex`` and a real 404 while the SPA still renders its own NotFound view.

The matcher mirrors react-router's ``matchPath``: static segments compare case-insensitively, a
trailing slash is optional, ``:name`` takes one segment and ``:name?`` may be absent. The
canonical URL is the pattern regenerated with the matched params, so static segments come out
exactly as the pattern spells them and ids keep their case.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode

_ROOT = pathlib.Path(__file__).resolve().parent.parent / "web"
_ROUTE_FILES = (_ROOT / "dist" / "seo-routes.json", _ROOT / "public" / "seo-routes.json")


@dataclass(frozen=True)
class Route:
    pattern: str
    title: str
    description: str
    end: bool = True
    noindex: bool = False
    canonical_params: tuple[str, ...] = ()


@dataclass(frozen=True)
class Routes:
    origin: str
    site_name: str
    routes: tuple[Route, ...]
    not_found: Route | None = None


@dataclass(frozen=True)
class Match:
    route: Route
    params: dict[str, str] = field(default_factory=dict)


def load_routes(path: pathlib.Path | None = None) -> Routes:
    """Read the route table from dist, or from public when the SPA is not built (tests)."""
    candidates = (path,) if path else _ROUTE_FILES
    for p in candidates:
        if p is not None and p.exists():
            return parse_routes(json.loads(p.read_text(encoding="utf-8")))
    raise FileNotFoundError(f"no seo-routes.json at {[str(p) for p in candidates]}")


def parse_routes(data: dict) -> Routes:
    routes: list[Route] = []
    not_found: Route | None = None
    for r in data["routes"]:
        route = Route(
            pattern=r["pattern"],
            title=r["title"],
            description=r["description"],
            end=r.get("end", True),
            noindex=bool(r.get("noindex", False)),
            canonical_params=tuple(r.get("canonicalParams", ())),
        )
        if route.pattern == "*":
            not_found = route
        else:
            routes.append(route)
    site = data["site"]
    return Routes(
        origin=site["origin"].rstrip("/"),
        site_name=site["name"],
        routes=tuple(routes),
        not_found=not_found,
    )


_PARAM = re.compile(r"^:(\w+)(\?)?$")


def _compile(pattern: str, end: bool) -> re.Pattern[str]:
    parts: list[str] = []
    for seg in pattern.strip("/").split("/"):
        if seg == "":
            continue
        m = _PARAM.match(seg)
        if m:
            name, optional = m.group(1), m.group(2)
            parts.append(f"(?:/(?P<{name}>[^/]+))?" if optional else f"/(?P<{name}>[^/]+)")
        else:
            parts.append("/" + re.escape(seg))
    body = "".join(parts) or ""
    tail = r"/?$" if end else r"(?:/|$)"
    return re.compile("^" + body + tail, re.IGNORECASE)


def match(routes: Routes, path: str) -> Match | None:
    """The first route whose pattern matches ``path``, with its params, or None."""
    if not path.startswith("/"):
        path = "/" + path
    for route in routes.routes:
        m = _compile(route.pattern, route.end).match(path)
        if m:
            params = {k: v for k, v in m.groupdict().items() if v is not None}
            return Match(route, params)
    return None


def _generate_path(pattern: str, params: dict[str, str]) -> str:
    out: list[str] = []
    for seg in pattern.strip("/").split("/"):
        if seg == "":
            continue
        m = _PARAM.match(seg)
        if not m:
            out.append(seg)
        elif m.group(1) in params:
            out.append(params[m.group(1)])
    return "/" + "/".join(out)


def canonical_for(routes: Routes, m: Match, query_string: str = "") -> str:
    """Origin plus the regenerated path, plus only the query parameters the route canonicalises on."""
    path = _generate_path(m.route.pattern, m.params)
    kept: list[tuple[str, str]] = []
    if m.route.canonical_params and query_string:
        q = parse_qs(query_string, keep_blank_values=False)
        for key in m.route.canonical_params:
            if q.get(key):
                kept.append((key, q[key][0]))
    return f"{routes.origin}{path}" + (f"?{urlencode(kept)}" if kept else "")


# ---- head rewriting -------------------------------------------------------------------------

_TITLE = re.compile(r"<title>.*?</title>", re.S)
_CANONICAL = re.compile(r'<link\s+rel="canonical"[^>]*>\s*', re.S)
_ROBOTS = re.compile(r'<meta\s+name="robots"[^>]*data-route-meta[^>]*>\s*', re.S)


def _meta_re(attr: str, key: str) -> re.Pattern[str]:
    return re.compile(rf'(<meta\s+{attr}="{re.escape(key)}"\s+content=")[^"]*(")', re.S)


_META = {
    "description": _meta_re("name", "description"),
    "twitter:title": _meta_re("name", "twitter:title"),
    "twitter:description": _meta_re("name", "twitter:description"),
    "og:title": _meta_re("property", "og:title"),
    "og:description": _meta_re("property", "og:description"),
    "og:url": _meta_re("property", "og:url"),
}
_OG_URL_TAG = re.compile(r'<meta\s+property="og:url"[^>]*>\s*', re.S)


def _set(shell: str, key: str, value: str) -> str:
    return _META[key].sub(lambda m: m.group(1) + html.escape(value, quote=True) + m.group(2), shell)


def render_head(shell: str, path: str, query_string: str = "", routes: Routes | None = None) -> tuple[str, int]:
    """The shell with its head rewritten for ``path``: (html, status).

    A matched route gets its title, description, canonical and social tags. An unmatched path
    gets the not-found title and description, no canonical, no og:url, a ``noindex`` and a 404.
    """
    if routes is None:
        routes = load_routes()
    if isinstance(query_string, bytes):
        query_string = query_string.decode("latin-1")
    m = match(routes, path)
    if m is None:
        nf = routes.not_found
        title = nf.title if nf else f"Page not found · {routes.site_name}"
        desc = nf.description if nf else "Nothing resolves at this address."
        out = _apply_text(shell, title, desc)
        out = _CANONICAL.sub("", out)
        out = _OG_URL_TAG.sub("", out)
        out = _ROBOTS.sub("", out)
        out = out.replace(
            "</head>", '<meta name="robots" content="noindex" data-route-meta />\n  </head>', 1
        )
        return out, 404

    canonical = canonical_for(routes, m, query_string)
    out = _apply_text(shell, m.route.title, m.route.description)
    out = _ROBOTS.sub("", out)
    esc = html.escape(canonical, quote=True)
    out = _CANONICAL.sub(f'<link rel="canonical" href="{esc}" />\n    ', out, count=1)
    out = _set(out, "og:url", canonical)
    return out, 200


def _apply_text(shell: str, title: str, description: str) -> str:
    out = _TITLE.sub(f"<title>{html.escape(title)}</title>", shell, count=1)
    out = _set(out, "description", description)
    out = _set(out, "og:title", title)
    out = _set(out, "og:description", description)
    out = _set(out, "twitter:title", title)
    out = _set(out, "twitter:description", description)
    return out
