import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import type { ReactElement } from "react";
import {
  ComingDown,
  ConstellationRace,
  FleetShare,
  LatestLaunches,
  weatherLead,
} from "../components/Stories";
import type { EnvironmentResponse, StoriesReentry, StoriesResponse } from "../api/types";
import storiesFixture from "../api/fixtures/stories.json";

const base = storiesFixture as unknown as StoriesResponse;
const html = (el: ReactElement) =>
  renderToStaticMarkup(<StaticRouter location="/">{el}</StaticRouter>);

const withNotable = (notable: StoriesReentry[]): StoriesResponse => ({
  ...base,
  reentries: { ...base.reentries, notable },
});

const leopard: StoriesReentry = {
  satellite_id: 67633,
  name: "LEOPARD",
  date: "2026-11-03",
  launched: "1998-11-20",
  owner: "Kyushu Institute of Technology",
  deployed_from_iss: true,
  released: "2026-02-03",
};

/** Audit 2026-11-13: LEOPARD read "28.0 years up", because SATCAT gives every satellite
    deployed from the ISS the station's 1998-11-20 launch date. */
describe("ComingDown time up", () => {
  it("counts an ISS-deployed satellite from its release by the station", () => {
    const out = html(<ComingDown s={withNotable([leopard])} />);
    expect(out).toContain("273 days up");
    expect(out).not.toContain("28.0 years up");
    expect(out).toContain("counts from its release from the station, as GCAT records it");
  });
  it("names the ISS when GCAT has no release date, rather than a span", () => {
    const out = html(<ComingDown s={withNotable([{ ...leopard, released: null }])} />);
    expect(out).toContain("deployed from the ISS");
    expect(out).not.toContain("years up");
  });
  it("keeps the launch-to-decay span for everything else, with no ISS note", () => {
    const swift: StoriesReentry = {
      satellite_id: 28485,
      name: "Swift",
      date: "2026-11-04",
      launched: "2004-11-20",
      owner: "NASA Goddard Space Flight Center",
      deployed_from_iss: false,
      released: null,
    };
    const out = html(<ComingDown s={withNotable([swift])} />);
    expect(out).toContain("22.0 years up");
    expect(out).not.toContain("release from the station");
  });
});

/** Audit 2026-11-13: the lead said the company-level catalog "runs about three months behind",
    while attribution was complete only through June 2026, more than four months back, and the
    frontier does not move as a fixed lag. The reason now names the month the API computes. */
describe("LatestLaunches lead", () => {
  const allPending = base.recent_launches.every((r) => !r.owner_attributed);
  it("uses a fixture where every launch awaits attribution", () => {
    expect(allPending).toBe(true);
  });
  it("names the month through which attribution is complete", () => {
    const out = html(<LatestLaunches s={base} />);
    expect(out).toContain(
      "because company attribution in the catalog is complete only through June 2026.",
    );
    expect(out).not.toContain("three months");
  });
  it("says so when no scanned month is complete", () => {
    const s = { ...base, attribution: { ...base.attribution, complete_through: null } };
    const out = html(<LatestLaunches s={s} />);
    expect(out).toContain("company attribution in the catalog is incomplete for every month scanned.");
  });
});

/** Audit 2026-11-13: the hint said the lead is the launch's "first named payload", while three
    of that day's ten leads were placeholders (OBJECT A, YAOGAN-51 OBJECT A, QIANFAN 17 OBJECT
    A), which the API falls back to when no payload has a real name yet. */
describe("LatestLaunches hint", () => {
  it("says the lead can be a placeholder name", () => {
    const out = html(<LatestLaunches s={base} />);
    expect(base.recent_launches[0].lead_name).toBe("OBJECT A");
    expect(out).toContain("placeholder name such as OBJECT A.");
    expect(out).not.toContain("first named payload");
  });
});

/** Audit 2026-11-13: the country-only row and its hint called every owner without a company
    class "a country code", but the row also held 35 active payloads owned by "To Be Determined"
    and a few by organizations known only by a SATCAT owner code. */
describe("country or placeholder owners", () => {
  it("labels and explains the fleet row as a country or placeholder", () => {
    const out = html(<FleetShare s={base} />);
    expect(out).toContain("Owner cataloged only as a country or placeholder, or none yet");
    expect(out).toContain("sometimes a placeholder such as To Be Determined.");
    expect(out).not.toContain("owners that are a country code");
  });
  it("says pending constellation payloads carry a country or placeholder code", () => {
    const out = html(<ConstellationRace s={base} />);
    expect(out).toContain("still carry only a country or placeholder code as owner");
    expect(out).not.toContain("only a country code as owner");
  });
});

/** Audit 2026-11-13: "The worst drag day of the last 60 days was October 13" named a quiet
    day (Kp 3.3) whose 28 m fall followed a 23 m rise the day before, which drag cannot cause;
    Starlink's station keeping moves the median. The sentence now states the fall and the day
    without calling it a drag day. */
describe("weatherLead", () => {
  const env = (median: number | null): EnvironmentResponse => ({
    rows: [],
    latest_observed: null,
    worst_drag_day: {
      day: "2026-10-13",
      storm_level: null,
      kp_max: 3.3,
      ap_avg: null,
      sats_observed: 15650,
      median_dsma_m: median,
    },
    note: "",
    attribution: "",
  });
  it("states the largest one-day fall without calling it drag", () => {
    const lead = weatherLead(base, env(-28.4));
    expect(lead).toContain(
      "The largest one-day fall in the median LEO orbit over the last 60 days was 28 meters, on October 13.",
    );
    expect(lead).not.toContain("worst drag day");
  });
  it("adds nothing when the series has no fall", () => {
    expect(weatherLead(base, env(2.6))).not.toContain("largest one-day fall");
    expect(weatherLead(base, null)).not.toContain("largest one-day fall");
  });
});
