import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { LifeTrackFoot } from "../components/LifeTrack";

/** The legend line is shared by the chart and the review case's loading placeholder, so the
    placeholder is the chart's height at every width: the markup must stay the same block. */
describe("LifeTrackFoot", () => {
  it("renders the legend and the right-hand readout in the chart's own foot markup", () => {
    expect(renderToStaticMarkup(<LifeTrackFoot right="NORAD 25544 · 30 d · 29 pts" />)).toBe(
      '<div class="lifetrack__foot"><span class="hint">semi-major axis (mean altitude) · faint band = perigee → apogee</span><span class="hint num">NORAD 25544 · 30 d · 29 pts</span></div>',
    );
  });
});
