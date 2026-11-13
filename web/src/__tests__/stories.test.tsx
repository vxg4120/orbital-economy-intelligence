import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import type { ReactElement } from "react";
import { ComingDown, LatestLaunches } from "../components/Stories";
import type { StoriesReentry, StoriesResponse } from "../api/types";
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
