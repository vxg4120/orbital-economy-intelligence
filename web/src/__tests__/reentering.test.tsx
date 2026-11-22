import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { ReenteringSoon, soonLead } from "../components/Stories";
import type { UpcomingReentries } from "../api/types";
import fixture from "../api/fixtures/reentries_upcoming.json";

const base = fixture as UpcomingReentries;
const html = (r: UpcomingReentries) =>
  renderToStaticMarkup(
    <StaticRouter location="/">
      <ReenteringSoon r={r} />
    </StaticRouter>,
  );

const empty: UpcomingReentries = {
  ...base,
  counts: { payload: 0, rocket_body: 0, debris: 0, unknown: 0, starlink: 0 },
  rows: [],
};

/** docs/specs/spacetrack-reentry-predictions.md: the panel says the dates are predictions, whose
    source is Space-Track, and how old the set is. */
describe("ReenteringSoon", () => {
  it("leads with the counts by type and the Starlink share", () => {
    expect(soonLead(base)).toBe(
      "Space-Track predicts that 8 objects will reenter in the next 60 days: 4 payloads, " +
        "1 rocket body, 2 pieces of debris and 1 object of unknown type. 2 of the payloads " +
        "are Starlink satellites.",
    );
  });

  it("lists the payloads other than Starlink, linked to the resolver", () => {
    const out = html(base);
    expect(out).toContain("The payloads expected first, other than Starlink");
    expect(out).toContain('href="/resolver/30211"');
    expect(out).toContain("SAOCOM 1A");
    expect(out).toContain("CUBESAT XI");
    expect(out).not.toContain("STARLINK-1009");
    expect(out).not.toContain("CZ-2C R/B");
    expect(out).toContain("predicted");
  });

  it("falls back to every object, and names one the graph has not matched", () => {
    const rows = base.rows.filter((x) => x.object_type !== "PAYLOAD");
    const out = html({ ...base, rows });
    expect(out).toContain("The objects expected first");
    expect(out).toContain("OBJECT A");
    expect(out).toContain("not in the catalog graph yet");
    expect(out).not.toContain('href="/resolver/null"');
  });

  it("says the dates are predictions and when the set was issued", () => {
    const out = html(base);
    expect(out).toContain("These are predictions, not reentries");
    expect(out).toContain("this set was issued on October 14");
    expect(out).toContain("A prediction never changes a decay date on this site.");
  });

  it("is honest before the first pull and after an empty one", () => {
    expect(soonLead({ ...empty, as_of: null, pulled_at: null })).toBe(
      "Space-Track's weekly reentry predictions have not been pulled yet.",
    );
    expect(soonLead(empty)).toBe("Space-Track predicts no reentry in the next 60 days.");
    expect(html({ ...empty, as_of: null, pulled_at: null })).not.toContain("was issued on");
  });

  it("phrases a single object and an all-Starlink set", () => {
    const one = {
      ...empty,
      counts: { ...empty.counts, payload: 1, starlink: 1 },
      rows: [base.rows[0]],
    };
    expect(soonLead(one)).toBe(
      "Space-Track predicts that 1 object will reenter in the next 60 days: 1 payload. " +
        "The payload is a Starlink satellite.",
    );
  });

  it("uses no en or em dashes in its copy", () => {
    expect(html(base)).not.toMatch(/[\u2013\u2014]/);
  });
});
