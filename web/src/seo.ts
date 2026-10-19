import { matchPath } from "react-router-dom";

/** One source of truth for what each route tells browsers, crawlers and share cards: the tab
 *  title, the description, the canonical URL, and whether the page may be indexed. The static
 *  head in index.html carries the landing page's values for crawlers that do not run scripts;
 *  RouteMeta rewrites them in place once the router knows where it is. */

export const SITE_NAME = "Orbital Economy Intelligence";
export const SITE_ORIGIN = "https://orbital.vibcreates.com";

export type RouteMeta = {
  /** The react-router patterns this entry covers, in the same form App.tsx declares them. */
  patterns: string[];
  /** The view's name; the full title appends the site name, except on the landing page. */
  view: string | null;
  description: string;
  /** True only for the catch-all: a dead link should not be indexed. */
  noindex?: boolean;
};

export const ROUTE_META: RouteMeta[] = [
  {
    patterns: ["/"],
    view: null,
    description:
      "A read-only public catalog over a satellite identity graph: one resolved object per physical satellite, stitched from SATCAT, GCAT and UCS, with identifier crosswalks, operator attribution, cross-source conflicts, bus benchmarks, reachability and pre-launch FCC filings.",
  },
  {
    patterns: ["/resolver", "/resolver/:satelliteId"],
    view: "Resolver",
    description:
      "Search the resolved satellite catalog and read one object's full identity: every source's identifier crosswalk, its ownership over time, and where SATCAT, GCAT and UCS disagree.",
  },
  {
    patterns: ["/conflicts"],
    view: "Conflicts",
    description:
      "The cross-source disagreements the resolver had to adjudicate, from the data-quality report, with every row linking into the resolver so the underlying claims can be read.",
  },
  {
    patterns: ["/operators", "/operators/:operatorId"],
    view: "Operators",
    description:
      "A league table of satellite operators over the resolved ownership graph, with each operator's hierarchy, fleet mix by status and orbital regime, and acquisition history.",
  },
  {
    patterns: ["/review", "/review/:caseId"],
    view: "Review",
    description:
      "The arbitration workbench for the hard identity cases the graph resolved under conflicting sources, where a recorded verdict turns coverage into per-failure-mode accuracy.",
  },
  {
    patterns: ["/buses", "/buses/:slug"],
    view: "Bus Benchmarks",
    description:
      "A provenance-tracked scoreboard for spacecraft platforms: fleet size, time to operational, station-keeping, lifetime and disposal, per manufacturer and per bus model, with a published methodology.",
  },
  {
    patterns: ["/reachability"],
    view: "Reachability",
    description:
      "Give the terminal a spot on Earth and it computes every satellite above the horizon right now from the latest GP elements, the transmitters each one carries from SatNOGS, and the next passes.",
  },
  {
    patterns: ["/filings"],
    view: "Filings",
    description:
      "Pending FCC space-station applications from IBFS bulk data and harvested ICFS documents: the pre-launch pipeline of satellites that do not yet exist in any tracking catalog.",
  },
  {
    patterns: ["*"],
    view: "Page not found",
    description: "Nothing resolves at this address on Orbital Economy Intelligence.",
    noindex: true,
  },
];

export function titleFor(meta: RouteMeta): string {
  return meta.view ? `${meta.view} · ${SITE_NAME}` : SITE_NAME;
}

/** The entry for a pathname, decided by the same matcher the router uses so the table cannot
 *  drift from App.tsx on parameterised paths. The catch-all is last and always matches. */
export function metaFor(pathname: string): RouteMeta {
  return (
    ROUTE_META.find((m) => m.patterns.some((p) => matchPath({ path: p, end: true }, pathname))) ??
    ROUTE_META[ROUTE_META.length - 1]
  );
}

/** The canonical URL for a pathname: the public origin plus the path with no trailing slash. */
export function canonicalFor(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed ? `${SITE_ORIGIN}${trimmed}` : `${SITE_ORIGIN}/`;
}

function setMeta(selector: string, attr: string, value: string, create: () => HTMLElement) {
  let el = document.head.querySelector<HTMLElement>(selector);
  if (!el) {
    el = create();
    document.head.appendChild(el);
  }
  el.setAttribute(attr, value);
}

/** Write one route's metadata into the document head. */
export function applyRouteMeta(pathname: string): void {
  const meta = metaFor(pathname);
  const title = titleFor(meta);
  const canonical = canonicalFor(pathname);

  document.title = title;
  setMeta('meta[name="description"]', "content", meta.description, () => {
    const m = document.createElement("meta");
    m.name = "description";
    return m;
  });
  setMeta('link[rel="canonical"]', "href", canonical, () => {
    const l = document.createElement("link");
    l.rel = "canonical";
    return l;
  });
  setMeta('meta[property="og:title"]', "content", title, () => {
    const m = document.createElement("meta");
    m.setAttribute("property", "og:title");
    return m;
  });
  setMeta('meta[property="og:url"]', "content", canonical, () => {
    const m = document.createElement("meta");
    m.setAttribute("property", "og:url");
    return m;
  });

  const robots = document.head.querySelector('meta[name="robots"]');
  if (meta.noindex) {
    if (!robots) {
      const m = document.createElement("meta");
      m.name = "robots";
      m.content = "noindex";
      document.head.appendChild(m);
    }
  } else if (robots) {
    robots.remove();
  }
}
