import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import {
  OperatorDetailPanel,
  Operators,
  congestionSentences,
  leagueMeta,
  namedNeighbours,
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
  others_per_satellite: 138.4,
  unattributed_per_satellite: 0,
  top_neighbours: [
    { operator_id: 9001, canonical_name: "United States", per_satellite: 66.7 },
    { operator_id: 9002, canonical_name: "Planet Labs", per_satellite: 6.9 },
    { operator_id: 9003, canonical_name: "People's Republic of China", per_satellite: 3.8 },
  ],
  main_shell: {
    alt_km_from: 450, alt_km_to: 500, inc_deg_from: 50, inc_deg_to: 55,
    operator_objects: 4057, objects: 4177, tied_shells: 1, tied_crowded_shells: 1,
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

const shell = (operator_objects: number, objects: number, tied_shells = 1, tied_crowded_shells = 1) => ({
  ...spacexCongestion.main_shell,
  operator_objects,
  objects,
  tied_shells,
  tied_crowded_shells,
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
      "Each one shares its shell with about 341 other tracked objects on average, 336 of them its own and 5 belonging to other operators.",
    );
  });

  it("shows one decimal below 10, with the parts adding up to the total", () => {
    expect(congestionSentences(split(6.5, 1.25)).share).toBe(
      "Each one shares its shell with about 6.5 other tracked objects on average, 1.3 of them its own and 5.2 belonging to other operators.",
    );
    expect(congestionSentences(split(2.52, 0)).share).toBe(
      "Each one shares its shell with about 2.5 other tracked objects on average, all of them belonging to other operators.",
    );
    expect(congestionSentences(split(12.2, 12.0)).share).toBe(
      "Each one shares its shell with about 12 other tracked objects on average, nearly all of them its own.",
    );
  });

  /* Review 2026-11-13, second pass: the other operators' part took every rounding error. */
  it("never gives a unit to a part that is 0, and never hides a part that rounds up", () => {
    // No other operator in its shells: own 2.4 and no owner 8.4, nothing left for others.
    const s = congestionSentences(split(10.8, 2.4, 8.4)).share;
    expect(s).not.toContain("other operators");
    expect(s).toMatch(/about 11 other tracked objects on average, \d+ of them its own and \d+ with no owner on record\.$/);
    const [own, unowned] = s.match(/(\d+) of them its own and (\d+) with/)!.slice(1).map(Number);
    expect(own + unowned).toBe(11);
    // 0.87 per object belong to another operator, more than the 0.53 left over from own.
    expect(congestionSentences(split(11.4, 10.53)).share).toBe(
      "Each one shares its shell with about 11 other tracked objects on average, 10 of them its own and 1 belonging to other operators.",
    );
  });

  it("says none and all only of a part that is truly 0", () => {
    expect(congestionSentences(split(4.84, 0.04)).share).toBe(
      "Each one shares its shell with about 4.8 other tracked objects on average, nearly all of them belonging to other operators.",
    );
    expect(congestionSentences(split(4.84, 0.04, 2.0)).share).toBe(
      "Each one shares its shell with about 4.8 other tracked objects on average, almost none of them its own, 2.8 belonging to other operators and 2.0 with no owner on record.",
    );
    expect(congestionSentences(split(12, 12)).share).toBe(
      "Each one shares its shell with about 12 other tracked objects on average, all of them its own.",
    );
  });

  it("names neighbours at the precision of the split and never above the part they come from", () => {
    expect(namedNeighbours(spacexCongestion).map((n) => n.shown)).toEqual(["67", "7", "4"]);
    const capped = split(10.49, 4.5, 3.5, {
      top_neighbours: [{ operator_id: 9006, canonical_name: "OP0", per_satellite: 2.49 }],
    });
    expect(congestionSentences(capped).share).toContain("2 belonging to other operators");
    expect(namedNeighbours(capped).map((n) => n.shown)).toEqual(["2"]);
    // When the other operators' part rounds to 0, nobody is named under it.
    const hidden = split(12.2, 12.0, 0, {
      top_neighbours: [{ operator_id: 9007, canonical_name: "OP1", per_satellite: 0.2 }],
    });
    expect(namedNeighbours(hidden)).toEqual([]);
  });

  it("says when several shells tie for the most of its objects", () => {
    const tied = (n: number, objects: number, shells: number, crowded: number) =>
      congestionSentences(split(20, 10, 0, { main_shell: shell(n, objects, shells, crowded) })).main;
    expect(tied(7, 127, 3, 1)).toBe(
      "3 shells each hold 7 of its objects, and none holds more. The most crowded of them is from 450 to 500 km at 50 to 55 degrees of inclination, where 7 of its objects share the shell with 120 other objects.",
    );
    expect(tied(7, 7, 2, 2)).toBe(
      "2 shells each hold 7 of its objects, and none holds more. One of the most crowded of them is from 450 to 500 km at 50 to 55 degrees of inclination, where 7 of its objects have the shell to themselves.",
    );
    expect(tied(1, 3, 5, 2)).toBe(
      "No shell holds more than one of its objects. One of the most crowded of its shells is from 450 to 500 km at 50 to 55 degrees of inclination, where its object shares the shell with 2 other objects.",
    );
    // Every object alone: the split says so, and no shell is singled out as crowded.
    const allAlone = split(0, 0, 0, { fleet_in_field: 5, main_shell: shell(1, 1, 5, 5) });
    expect(congestionSentences(allAlone).main).toBe("");
    expect(renderDetail(detail({ congestion: allAlone }))).not.toContain("most crowded");
  });

  /* Review 2026-11-13, third pass: below 1 per object a tenth is too coarse to split. */
  it("gives the split in tenths of the total below 1 per object", () => {
    // 40% own: one decimal would have printed "almost none of them its own".
    expect(congestionSentences(split(0.1, 0.04)).share).toBe(
      "Each one shares its shell with about 0.1 other tracked objects on average, 4 in 10 of them its own and 6 in 10 belonging to other operators.",
    );
    expect(congestionSentences(split(0.1, 0.06)).share).toBe(
      "Each one shares its shell with about 0.1 other tracked objects on average, 6 in 10 of them its own and 4 in 10 belonging to other operators.",
    );
    expect(congestionSentences(split(0.5, 0.5)).share).toBe(
      "Each one shares its shell with about 0.5 other tracked objects on average, all of them its own.",
    );
    // A part that takes all ten tenths is "nearly all" beside a part that is not truly 0.
    expect(congestionSentences(split(0.414, 0.0069, 0.0069)).share).toBe(
      "Each one shares its shell with about 0.4 other tracked objects on average, nearly all of them belonging to other operators.",
    );
    expect(congestionSentences(split(0.566, 0, 0.0053)).share).toBe(
      "Each one shares its shell with about 0.6 other tracked objects on average, nearly all of them belonging to other operators.",
    );
    // The neighbours are named without numbers there.
    const small = split(0.1, 0.04, 0, {
      top_neighbours: [{ operator_id: 9008, canonical_name: "OP2", per_satellite: 0.06 }],
    });
    expect(namedNeighbours(small)).toEqual([{ operator_id: 9008, canonical_name: "OP2", shown: null }]);
    const html = renderDetail(detail({ congestion: small }));
    expect(html).toContain('the largest number comes from <span><a href="/operators/9008">OP2</a></span>.');
  });

  it("rounds the named shares down when rounding would make them exceed their part", () => {
    // 11 = 1 own + 3 named (1.5 each) + 7 with no owner on record; 2 + 2 would exceed 3.
    const over = split(11, 1, 7, {
      top_neighbours: [
        { operator_id: 9009, canonical_name: "OP3", per_satellite: 1.5 },
        { operator_id: 9010, canonical_name: "OP4", per_satellite: 1.5 },
      ],
    });
    expect(congestionSentences(over).share).toContain("3 belonging to other operators");
    expect(namedNeighbours(over).map((n) => n.shown)).toEqual(["1", "1"]);
    // Equal shares that fit stay equal, as in the ALPHA seed.
    const fits = split(6.5, 1.5, 1.5, {
      top_neighbours: [
        { operator_id: 70, canonical_name: "BETA", per_satellite: 1.25 },
        { operator_id: 30, canonical_name: "GAMMA", per_satellite: 0.75 },
        { operator_id: 50, canonical_name: "THETA", per_satellite: 0.75 },
      ],
    });
    expect(namedNeighbours(fits).map((n) => n.shown)).toEqual(["1.3", "0.8", "0.8"]);
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
    expect(lone(3, 2, 4).share).toBe(
      "It shares its shell with 3 other tracked objects, 1 belonging to another operator and 2 with no owner on record.",
    );
    expect(lone(3, 0, 4).main).toBe(
      "Its shell is from 450 to 500 km at 50 to 55 degrees of inclination, and it shares that shell with 3 other objects.",
    );
  });

  it("switches to whole numbers where the total would print as 10.0", () => {
    expect(congestionSentences(split(9.96, 0)).share).toContain("about 10 other tracked objects");
    expect(congestionSentences(split(9.94, 0)).share).toContain("about 9.9 other tracked objects");
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
