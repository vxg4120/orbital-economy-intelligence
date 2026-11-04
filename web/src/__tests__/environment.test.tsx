import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { EnvironmentStrip } from "../components/EnvironmentStrip";
import type { EnvironmentResponse } from "../api/types";

const env = (kp_max: number | null, storm_level: string | null = null): EnvironmentResponse => ({
  rows: [],
  latest_observed: {
    day: "2026-10-01",
    kp_max,
    ap_avg: 27,
    ap_max: 39,
    storm_level,
    f107_obs: null,
    f107_obs_center81: null,
  },
  worst_drag_day: null,
  note: "",
  attribution: "",
});

/** The geomagnetic tile names NOAA's band for the day's peak Kp: a Kp 4.3 day read "quiet"
    before, because only a storm level had a word. */
describe("EnvironmentStrip Kp band", () => {
  const sub = (e: EnvironmentResponse) => {
    const html = renderToStaticMarkup(<EnvironmentStrip env={e} />);
    return /Ap 27 · 2026-10-01|Ap peak 39 · 2026-10-01/.exec(html) ? html : html;
  };
  it("bands quiet, unsettled, active and storm at 3, 4 and 5", () => {
    expect(sub(env(2.7))).toContain("quiet · Ap 27 · 2026-10-01");
    expect(sub(env(3))).toContain("unsettled · Ap 27 · 2026-10-01");
    expect(sub(env(4.3))).toContain("active · Ap 27 · 2026-10-01");
    expect(sub(env(5))).toContain("storm · Ap 27 · 2026-10-01");
  });
  it("keeps the storm-level wording when NOAA assigned one", () => {
    expect(sub(env(5.7, "G1"))).toContain("G1 storm · Ap peak 39 · 2026-10-01");
  });
  it("starts with the Ap when the day has no Kp (no dangling separator)", () => {
    const html = sub(env(null));
    expect(html).toContain(">Ap 27 · 2026-10-01<");
    expect(html).not.toContain("· Ap 27 · 2026-10-01");
  });
});
