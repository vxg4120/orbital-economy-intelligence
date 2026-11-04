import { describe, expect, it } from "vitest";
import { canonicalFor, metaFor, ROUTE_META, SITE_NAME, SITE_ORIGIN } from "../seo";

/** The route table drives the tab title, the share tags and the server-rendered head (api/seo.py
    reads the same JSON), so it has to agree with the paths App.tsx serves. */
describe("route table", () => {
  it("has one entry per view App.tsx routes, plus the catch-all", () => {
    const patterns = ROUTE_META.map((m) => m.pattern);
    for (const p of [
      "/",
      "/resolver/:satelliteId?",
      "/conflicts",
      "/operators/:operatorId?",
      "/review/:caseId?",
      "/buses/:slug?",
      "/reachability",
      "/filings",
      "*",
    ]) {
      expect(patterns).toContain(p);
    }
  });
  it("gives every indexable route a title that names the site and a description", () => {
    // one name everywhere: the plate, the colophon and every tab title say Terminal
    expect(SITE_NAME).toBe("Orbital Economy Terminal");
    for (const m of ROUTE_META) {
      expect(m.title.endsWith(SITE_NAME)).toBe(true);
      expect(m.description.length).toBeGreaterThan(40);
    }
    expect(metaFor("/nope").noindex).toBe(true);
  });
});

describe("metaFor", () => {
  it("resolves a deep link to its view's entry", () => {
    expect(metaFor("/resolver/25544").pattern).toBe("/resolver/:satelliteId?");
    expect(metaFor("/resolver").pattern).toBe("/resolver/:satelliteId?");
    expect(metaFor("/buses/spx").pattern).toBe("/buses/:slug?");
  });
  it("falls back to the catch-all for an unknown path or an extra segment", () => {
    expect(metaFor("/resolver/25544/extra").noindex).toBe(true);
    expect(metaFor("/nope").title).toMatch(/not found/i);
  });
});

describe("canonicalFor", () => {
  it("drops the trailing slash and every query parameter the route does not canonicalise on", () => {
    expect(canonicalFor("/resolver/25544/", "?utm_source=x")).toBe(`${SITE_ORIGIN}/resolver/25544`);
    expect(canonicalFor("/")).toBe(`${SITE_ORIGIN}/`);
  });
  it("keeps the kind parameter on a bus slug, because slugs collide across kinds", () => {
    expect(canonicalFor("/buses/spx", "?kind=manufacturer&tab=x")).toBe(
      `${SITE_ORIGIN}/buses/spx?kind=manufacturer`,
    );
    expect(canonicalFor("/buses/spx", "")).toBe(`${SITE_ORIGIN}/buses/spx`);
  });
});
