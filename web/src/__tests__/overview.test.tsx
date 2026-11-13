import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { Overview, UnderTheHood } from "../views/Overview";
import type { Stats } from "../api/types";
import statsFixture from "../api/fixtures/stats.json";

const render = (s: Stats) =>
  renderToStaticMarkup(
    <StaticRouter location="/">
      <UnderTheHood s={s} />
    </StaticRouter>,
  );

const stats = statsFixture as Stats;

/** Audit 2026-11-13: the under-the-hood tiles described numbers they did not hold. The catalog
    total counts reentered objects, the element-set total counts every epoch, and the
    two-source meter counted identifier rows, which one SATCAT record always passes. */
describe("UnderTheHood", () => {
  it("calls the catalog total cataloged, not tracked, and says how much is still up", () => {
    const html = render(stats);
    expect(html).toContain("Cataloged objects");
    expect(html).not.toContain("Tracked objects");
    expect(html).toContain('<span class="num">34,012</span> are still in orbit, including');
    expect(html).toContain('<span class="num">5,332</span> payloads.');
  });

  it("keeps the old payload line when the API has no in-orbit count yet", () => {
    const { in_orbit_objects: _dropped, ...older } = stats;
    const html = render(older as Stats);
    expect(html).toContain("on-orbit payloads");
    expect(html).not.toContain("still in orbit");
  });

  it("says the element-set total is the whole history", () => {
    const html = render(stats);
    expect(html).toContain("Every stored element set is counted, not only the latest per object.");
    expect(html).not.toContain("latest GP per object");
  });

  it("describes merge events without naming only two rules", () => {
    const html = render(stats);
    expect(html).not.toContain("norad + cospar exact");
    expect(html).toContain("an identifier link or a record merge");
  });

  it("meters SATCAT and GCAT membership, not identifier rows", () => {
    const html = render(stats);
    expect(html).toContain("Listed in both SATCAT and GCAT");
    expect(html).toContain("94.5%");
    expect(html).not.toContain("source identifiers");
    expect(html).not.toContain("cross-catalog corroboration");
  });

  it("drops the two-catalog meter rather than show the row count from an older API", () => {
    const { both_catalogs_pct: _dropped, ...coverage } = stats.coverage;
    const html = render({ ...stats, coverage } as Stats);
    expect(html).not.toContain("Listed in both SATCAT and GCAT");
    expect(html).not.toContain("source identifiers");
  });

  it("says placeholder owners count toward the owner meter", () => {
    expect(render(stats)).toContain("Country-level and placeholder owners count as resolved.");
  });

  it("describes the ledger as one row per source, endpoint and state, capped at 20", () => {
    const html = render(stats);
    expect(html).toContain(
      "Each row is the latest run for one source, endpoint and state, and the 20 most recent are shown.",
    );
    expect(html).not.toContain("last run per source · endpoint");
  });
});

/** The page's fixed captions render before any data arrives, so a static render reads them. */
describe("Overview captions", () => {
  const html = renderToStaticMarkup(
    <StaticRouter location="/">
      <Overview />
    </StaticRouter>,
  );
  it("says the congestion field leaves out reentered objects, debris and rocket bodies", () => {
    expect(html).toContain("Each shell counts the objects that have not reentered");
    expect(html).toContain("debris and spent rocket bodies are almost entirely missing from it.");
    expect(html).not.toContain("LEO shell occupancy");
  });
});
