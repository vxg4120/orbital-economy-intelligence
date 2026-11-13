import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import {
  OperatorDetailPanel,
  Operators,
  congestionSentences,
  leagueMeta,
  perObject,
} from "../views/Operators";
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

/* docs/specs/congestion-exposure.md: the panel reads the view's per-operator split. */
const spacexCongestion = {
  fleet_in_field: 10622,
  shells: 35,
  neighbours_per_satellite: 2893.7,
  own_per_satellite: 2755.3,
  others_per_satellite: 138.3,
  unattributed_per_satellite: 0,
  top_neighbours: [
    { operator_id: 9001, canonical_name: "United States", per_satellite: 66.7 },
    { operator_id: 9002, canonical_name: "Planet Labs", per_satellite: 6.9 },
    { operator_id: 9003, canonical_name: "People's Republic of China", per_satellite: 3.8 },
  ],
  busiest_shell: {
    alt_km_from: 450, alt_km_to: 500, inc_deg_from: 50, inc_deg_to: 55,
    operator_objects: 4057, objects: 4177,
  },
  exposure_score: 0.1703,
  field_objects: 16997,
};

describe("Congestion exposure panel", () => {
  it("states the own and other split so that it adds up to the total shown", () => {
    const s = congestionSentences(spacexCongestion);
    expect(s.where).toBe("10,622 of its objects are in the field, in 35 shells of 50 km by 5 degrees.");
    expect(s.share).toBe(
      "Each one shares its shell with about 2,894 other tracked objects on average, 2,755 of them its own and 139 belonging to others.",
    );
    expect(s.busiest).toBe(
      "Its busiest shell is from 450 to 500 km at 50 to 55 degrees of inclination, where 4,057 of its objects share the shell with 120 other objects.",
    );
  });

  it("handles a lone object and small averages", () => {
    const lone = {
      ...spacexCongestion,
      fleet_in_field: 1,
      shells: 1,
      neighbours_per_satellite: 0,
      own_per_satellite: 0,
      others_per_satellite: 0,
      top_neighbours: [],
      busiest_shell: { ...spacexCongestion.busiest_shell, operator_objects: 1, objects: 1 },
    };
    const s = congestionSentences(lone);
    expect(s.where).toBe("1 of its objects is in the field, in 1 shell of 50 km by 5 degrees.");
    expect(s.share).toBe("It has its shell to itself.");
    expect(s.busiest).toContain("where 1 of its objects has the shell to itself.");
    expect(perObject(6.94)).toBe("6.9");
    expect(perObject(66.7)).toBe("67");
  });

  it("renders the sentences, the neighbour links and the density caveat", () => {
    const html = renderDetail(detail({ congestion: spacexCongestion }));
    expect(html).toContain("Congestion exposure");
    expect(html).toContain("2,755 of them its own");
    expect(html).toContain('href="/operators/9001"');
    expect(html).toContain("(<span class=\"num\">67</span> per object)");
    expect(html).toContain("density, not collision risk");
    // The new section adds no en or em dash (the hierarchy above uses one as a placeholder).
    const section = html.slice(
      html.indexOf("Congestion exposure"),
      html.indexOf("Acquisition history"),
    );
    expect(section).toContain("collision risk");
    expect(section).not.toMatch(/[\u2013\u2014]/);
  });

  it("says so when none of the operator's objects is in the field", () => {
    const html = renderDetail(detail({ congestion: null }));
    expect(html).toContain("None of its objects is in the congestion field");
  });

  it("renders nothing for an API that predates the block", () => {
    expect(renderDetail(detail({}))).not.toContain("Congestion exposure");
  });
});
