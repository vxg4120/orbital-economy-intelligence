import { matchPath } from "react-router-dom";
import seoRoutes from "../public/seo-routes.json";

/** What each route tells browsers, crawlers and share cards. The table itself lives in
 *  public/seo-routes.json so the API can read the same file from dist and write a correct head
 *  into the shell before any script runs (api/seo.py). This module is the client half: on every
 *  navigation it rewrites the head in place for the new location. */

export type RouteMeta = {
  /** A react-router pattern, in the same form App.tsx declares them. */
  pattern: string;
  end?: boolean;
  title: string;
  description: string;
  /** Query parameters that select a distinct entity and so belong in the canonical URL. */
  canonicalParams?: string[];
  /** True only for the catch-all: a dead link should not be indexed. */
  noindex?: boolean;
};

type SeoConfig = {
  site: { name: string; origin: string; description: string };
  routes: RouteMeta[];
};

const CONFIG = seoRoutes as SeoConfig;

export const SITE_NAME = CONFIG.site.name;
export const SITE_ORIGIN = CONFIG.site.origin;
export const ROUTE_META = CONFIG.routes;

const NOT_FOUND = ROUTE_META.find((m) => m.pattern === "*") ?? ROUTE_META[ROUTE_META.length - 1];

/** The entry for a pathname, decided by the same matcher the router uses so the table cannot
 *  drift from App.tsx on parameterised paths. The catch-all is the fallback. */
export function metaFor(pathname: string): RouteMeta {
  return (
    ROUTE_META.find(
      (m) => m.pattern !== "*" && matchPath({ path: m.pattern, end: m.end ?? true }, pathname),
    ) ?? NOT_FOUND
  );
}

/** The canonical URL: the public origin, the path with no trailing slash, and only those query
 *  parameters the route lists as selecting a distinct entity. */
export function canonicalFor(pathname: string, search = ""): string {
  const trimmed = pathname.replace(/\/+$/, "");
  const meta = metaFor(pathname);
  const params = new URLSearchParams(search);
  const kept = new URLSearchParams();
  for (const key of meta.canonicalParams ?? []) {
    const v = params.get(key);
    if (v) kept.set(key, v);
  }
  const query = kept.toString();
  return `${SITE_ORIGIN}${trimmed || "/"}${query ? `?${query}` : ""}`;
}

function setMeta(selector: string, attr: string, value: string, create: () => HTMLElement) {
  let el = document.head.querySelector<HTMLElement>(selector);
  if (!el) {
    el = create();
    document.head.appendChild(el);
  }
  el.setAttribute(attr, value);
}

function namedMeta(name: string) {
  return () => {
    const m = document.createElement("meta");
    m.name = name;
    return m;
  };
}

function propertyMeta(property: string) {
  return () => {
    const m = document.createElement("meta");
    m.setAttribute("property", property);
    return m;
  };
}

/** Write one location's metadata into the document head. */
export function applyRouteMeta(pathname: string, search = ""): void {
  const meta = metaFor(pathname);
  const canonical = canonicalFor(pathname, search);

  document.title = meta.title;
  setMeta('meta[name="description"]', "content", meta.description, namedMeta("description"));
  setMeta('meta[property="og:title"]', "content", meta.title, propertyMeta("og:title"));
  setMeta(
    'meta[property="og:description"]',
    "content",
    meta.description,
    propertyMeta("og:description"),
  );
  setMeta('meta[name="twitter:title"]', "content", meta.title, namedMeta("twitter:title"));
  setMeta(
    'meta[name="twitter:description"]',
    "content",
    meta.description,
    namedMeta("twitter:description"),
  );

  // A dead link gets no canonical and a noindex; everything else the reverse.
  const link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  const robots = document.head.querySelector('meta[name="robots"][data-route-meta]');
  if (meta.noindex) {
    link?.remove();
    document.head.querySelector('meta[property="og:url"]')?.remove();
    if (!robots) {
      const m = document.createElement("meta");
      m.name = "robots";
      m.content = "noindex";
      m.setAttribute("data-route-meta", "");
      document.head.appendChild(m);
    }
    return;
  }
  robots?.remove();
  setMeta('link[rel="canonical"]', "href", canonical, () => {
    const l = document.createElement("link");
    l.rel = "canonical";
    return l;
  });
  setMeta('meta[property="og:url"]', "content", canonical, propertyMeta("og:url"));
}
