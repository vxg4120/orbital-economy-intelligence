import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { OperatorDetailPanel, Operators, leagueMeta } from "../views/Operators";
import type { OperatorDetail, OperatorsResponse } from "../api/types";

const league = (extra: Partial<OperatorsResponse>): OperatorsResponse => ({
  rows: [],
  total: 1444,
  ...extra,
});

/** Audit 2026-11-13: "1,392 of 1,444 operators hold a current fleet" counted 413 operators
    with nothing left in orbit, because a fleet includes reentered objects. */
describe("leagueMeta", () => {
  it("says how many operators own a cataloged object and how many still have one up", () => {
    expect(leagueMeta("fleet", league({ with_fleet: 1392, with_on_orbit: 979 }))).toBe(
      "sorted by fleet · 1,392 of 1,444 operators own at least one cataloged object, and 979 of them still have one in orbit.",
    );
  });
  it("drops the in-orbit clause when an older API does not send it", () => {
    expect(leagueMeta("active", league({ with_fleet: 1392 }))).toBe(
      "sorted by active · 1,392 of 1,444 operators own at least one cataloged object.",
    );
  });
  it("never claims a current fleet", () => {
    expect(leagueMeta("fleet", league({ with_fleet: 1392, with_on_orbit: 979 }))).not.toContain(
      "current fleet",
    );
    expect(leagueMeta("name", null)).toBe("sorted by name");
  });
});

const detail = (over: Partial<OperatorDetail>): OperatorDetail => ({
  operator: { operator_id: 1, canonical_name: "SpaceX", country: "US", operator_class: "commercial" },
  parents: [],
  children: [],
  fleet_by_status: {},
  fleet_by_regime: {},
  acquisitions: [],
  top_satellites: [],
  ...over,
} as OperatorDetail);

const renderDetail = (d: OperatorDetail) =>
  renderToStaticMarkup(
    <StaticRouter location="/operators/1">
      <OperatorDetailPanel detail={d} />
    </StaticRouter>,
  );

/** Audit 2026-11-13: SpaceX listed Swarm Technologies as a child from 2021 next to "No
    acquisitions on record", because the panel shows only curated merger and acquisition edges
    and the GCAT parent links do not say how a link began. */
describe("Acquisition history panel", () => {
  const swarm = {
    operator_id: 2705,
    canonical_name: "Swarm Technologies (SpaceX)",
    relationship: "subsidiary_of",
    valid_from: "2021-08-01",
    valid_to: null,
  };
  it("says the panel is curated and explains the links above when there are any", () => {
    const html = renderDetail(detail({ children: [swarm] }));
    expect(html).toContain("Only curated mergers and acquisitions are listed.");
    expect(html).toContain("No curated merger or acquisition on record");
    expect(html).toContain("do not record whether a link began as an acquisition.");
    expect(html).not.toContain("No acquisitions on record");
    expect(html).not.toContain("ownership relationships");
  });
  it("adds no note about links when the operator has none", () => {
    const html = renderDetail(detail({}));
    expect(html).toContain("No curated merger or acquisition on record");
    expect(html).not.toContain("links above");
  });
});

describe("Operators view", () => {
  it("captions the congestion field with what it counts", () => {
    const html = renderToStaticMarkup(
      <StaticRouter location="/operators">
        <Operators />
      </StaticRouter>,
    );
    expect(html).toContain("Each shell counts the objects that have not reentered");
    expect(html).toContain("Debris and spent rocket bodies are almost entirely missing");
  });
});
