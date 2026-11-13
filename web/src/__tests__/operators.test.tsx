import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import {
  OperatorDetailPanel,
  Operators,
  congestionSentences,
  leagueMeta,
  namedNeighbours,
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
  main_shell: {
    alt_km_from: 450, alt_km_to: 500, inc_deg_from: 50, inc_deg_to: 55,
    operator_objects: 4057, objects: 4177,
  },
  exposure_score: 0.1703,
  field_objects: 16997,
};

const split = (
  neighbours: number,
  own: number,
  unattributed = 0,
  over: Partial<typeof spacexCongestion> = {},
) => ({
  ...spacexCongestion,
  neighbours_per_satellite: neighbours,
  own_per_satellite: own,
  others_per_satellite: neighbours - own,
  unattributed_per_satellite: unattributed,
  ...over,
});

const shell = (operator_objects: number, objects: number) => ({
  ...spacexCongestion.main_shell,
  operator_objects,
  objects,
});

describe("Congestion exposure panel", () => {
  it("states the own and other split so that it adds up to the total shown", () => {
    const s = congestionSentences(spacexCongestion);
    expect(s.where).toBe("10,622 of its objects are in the field, in 35 shells of 50 km by 5 degrees.");
    expect(s.share).toBe(
      "Each one shares its shell with about 2,894 other tracked objects on average, 2,755 of them its own and 139 belonging to other operators.",
    );
    expect(s.main).toBe(
      "The shell holding the most of its objects is from 450 to 500 km at 50 to 55 degrees of inclination, where 4,057 of its objects share the shell with 120 other objects.",
    );
  });

  /* Review 2026-11-13: rounded parts were printed as "2.0", and parts under 10 did not add up. */
  it("shows whole numbers from 10 up, with the parts adding up to the total", () => {
    expect(congestionSentences(split(151.5, 1.5)).share).toBe(
      "Each one shares its shell with about 152 other tracked objects on average, 2 of them its own and 150 belonging to other operators.",
    );
    expect(congestionSentences(split(340.6, 335.4)).share).toBe(
      "Each one shares its shell with about 341 other tracked objects on average, 335 of them its own and 6 belonging to other operators.",
    );
  });

  it("shows one decimal below 10, with the parts adding up to the total", () => {
    expect(congestionSentences(split(6.5, 1.25)).share).toBe(
      "Each one shares its shell with about 6.5 other tracked objects on average, 1.3 of them its own and 5.2 belonging to other operators.",
    );
    expect(congestionSentences(split(2.52, 0)).share).toBe(
      "Each one shares its shell with about 2.5 other tracked objects on average, none of them its own and 2.5 belonging to other operators.",
    );
    expect(congestionSentences(split(12.2, 12.0)).share).toBe(
      "Each one shares its shell with about 12 other tracked objects on average, all of them its own.",
    );
  });

  it("does not credit objects with no owner on record to other operators", () => {
    // ALPHA in tests/test_api_operators_congestion.py: 6.5 = 1.5 own + 3.5 named + 1.5 unowned.
    expect(congestionSentences(split(6.5, 1.5, 1.5)).share).toBe(
      "Each one shares its shell with about 6.5 other tracked objects on average, 1.5 of them its own, 3.5 belonging to other operators and 1.5 with no owner on record.",
    );
  });

  it("does not say every object is alone when a few share a shell", () => {
    const sparse = split(0.025, 0, 0, {
      fleet_in_field: 40,
      shells: 39,
      top_neighbours: [{ operator_id: 9004, canonical_name: "Other", per_satellite: 0.025 }],
      main_shell: shell(1, 2),
    });
    const s = congestionSentences(sparse);
    expect(s.share).toBe("Fewer than 1 in 20 of them shares its shell with another tracked object.");
    expect(s.main).toBe(
      "No shell holds more than one of its objects. The most crowded of its shells is from 450 to 500 km at 50 to 55 degrees of inclination, where its object shares the shell with 1 other object.",
    );
    expect(namedNeighbours(sparse)).toEqual([]);
    expect(congestionSentences(split(0, 0, 0, { fleet_in_field: 40 })).share).toBe(
      "Each one has its shell to itself.",
    );
  });

  it("gives exact counts for a lone object", () => {
    const lone = (neighbours: number, unattributed: number, objects: number) =>
      congestionSentences(
        split(neighbours, 0, unattributed, {
          fleet_in_field: 1,
          shells: 1,
          top_neighbours: [],
          main_shell: shell(1, objects),
        }),
      );
    const alone = lone(0, 0, 1);
    expect(alone.where).toBe("1 of its objects is in the field, in 1 shell of 50 km by 5 degrees.");
    expect(alone.share).toBe("It has its shell to itself.");
    expect(alone.main).toBe(
      "Its shell is from 450 to 500 km at 50 to 55 degrees of inclination, and it has the shell to itself.",
    );
    expect(lone(1, 0, 2).share).toBe(
      "It shares its shell with 1 other tracked object, which belongs to another operator.",
    );
    expect(lone(1, 1, 2).share).toBe(
      "It shares its shell with 1 other tracked object, which has no owner on record.",
    );
    expect(lone(3, 1, 4).share).toBe(
      "It shares its shell with 3 other tracked objects, 2 belonging to other operators and 1 with no owner on record.",
    );
    expect(lone(3, 0, 4).main).toBe(
      "Its shell is from 450 to 500 km at 50 to 55 degrees of inclination, and it shares that shell with 3 other objects.",
    );
  });

  it("formats a per-object average at the precision it is shown", () => {
    expect(perObject(6.94)).toBe("6.9");
    expect(perObject(9.96)).toBe("10");
    expect(perObject(66.7)).toBe("67");
  });

  it("names one neighbour in the singular", () => {
    const one = split(5, 3, 0, {
      top_neighbours: [{ operator_id: 9005, canonical_name: "BETA", per_satellite: 2 }],
    });
    const html = renderDetail(detail({ congestion: one }));
    expect(html).toContain("Of those belonging to other operators, the largest number comes from");
    expect(html).toContain("(<span class=\"num\">2.0</span> per object)");
  });

  it("renders the sentences, the neighbour links and the density caveat", () => {
    const html = renderDetail(detail({ congestion: spacexCongestion }));
    expect(html).toContain("Congestion exposure");
    expect(html).toContain("2,755 of them its own");
    expect(html).toContain("Of those belonging to other operators, the largest numbers come from");
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

  it("says why none of the operator's objects is in the field", () => {
    const withFleet = renderDetail(
      detail({
        congestion: null,
        operator: { ...detail({}).operator, fleet_total: 111 },
      } as Partial<OperatorDetail>),
    );
    expect(withFleet).toContain("None of its objects is in the congestion field");
    expect(withFleet).toContain("each of its objects has either reentered or has no element set on record.");
    const noFleet = renderDetail(
      detail({
        congestion: null,
        operator: { ...detail({}).operator, fleet_total: 0 },
      } as Partial<OperatorDetail>),
    );
    expect(noFleet).toContain("No object is on record in its name.");
  });

  it("renders nothing for an API that predates the block", () => {
    expect(renderDetail(detail({}))).not.toContain("Congestion exposure");
  });
});
