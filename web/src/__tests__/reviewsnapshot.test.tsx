import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReviewCaseDetail } from "../api/types";
import cases from "../api/fixtures/review_cases.json";
import { drawnSentence } from "../views/Review";
import { type CaseSnapshot, SnapshotNote, sinceDrawnSentence } from "../views/ReviewCase";

type Detail = ReviewCaseDetail & CaseSnapshot;
const base = cases[0] as unknown as Detail;
const text = (html: string) => html.replace(/<[^>]+>/g, "").replace(/&#x27;/g, "'");

/** A review case is a snapshot of the graph on the day it was drawn, so the page dates it and
    names what the graph now holds differently, instead of showing old answers as current. */
describe("SnapshotNote", () => {
  it("dates the case and the source data it was drawn from", () => {
    const t = text(
      renderToStaticMarkup(
        <SnapshotNote
          detail={{ ...base, drawn_at: "2026-07-11T02:16:54Z", observed_through: "2026-07-08T04:24:24Z", since_drawn: [] }}
        />,
      ),
    );
    expect(t).toContain(
      "This case was drawn on 2026-07-11, from source data observed up to 2026-07-08, and it shows the sources and the graph's answer as they stood then.",
    );
    expect(t).not.toContain("Since this case was drawn");
  });
  it("names what changed since the draw", () => {
    const t = text(
      renderToStaticMarkup(
        <SnapshotNote
          detail={{
            ...base,
            drawn_at: "2026-07-11T02:16:54Z",
            observed_through: null,
            since_drawn: [{ field: "status", then: "DECAYED", now: "ACTIVE" }],
          }}
        />,
      ),
    );
    expect(t).toContain("This case was drawn on 2026-07-11, and it shows");
    expect(t).toContain("Since this case was drawn, its resolved status has changed from DECAYED to ACTIVE.");
  });
  it("shows nothing for a case without a draw date", () => {
    expect(renderToStaticMarkup(<SnapshotNote detail={{ ...base, drawn_at: null }} />)).toBe("");
  });
});

describe("sinceDrawnSentence", () => {
  it("joins three changes into one sentence with its connectives", () => {
    expect(
      sinceDrawnSentence([
        { field: "norad_id", then: null, now: 69869 },
        { field: "canonical_name", then: "SENTINEL-1", now: "NEXTSAT-4" },
        { field: "status", then: "DECAYED", now: "ACTIVE" },
      ]),
    ).toBe(
      "Since this case was drawn, the catalog has given this object NORAD 69869, its name has changed from SENTINEL-1 to NEXTSAT-4, and its resolved status has changed from DECAYED to ACTIVE.",
    );
  });
  it("says so when the graph no longer holds the record", () => {
    expect(sinceDrawnSentence([{ field: "record", then: 5, now: null }])).toBe(
      "Since this case was drawn, the graph no longer holds this record.",
    );
  });
});

describe("drawnSentence", () => {
  it("dates a queue drawn on one day", () => {
    expect(drawnSentence({ first: "2026-07-11T02:16:54Z", last: "2026-07-11T02:20:00Z" })).toBe(
      "Every case was drawn on 2026-07-11, and each one shows the sources and the graph's answer as they stood that day.",
    );
  });
  it("gives the span when the queue was drawn over several days", () => {
    expect(drawnSentence({ first: "2026-07-11T00:00:00Z", last: "2026-08-02T00:00:00Z" })).toContain(
      "The cases were drawn between 2026-07-11 and 2026-08-02",
    );
  });
  it("says nothing when the API does not date the queue", () => {
    expect(drawnSentence(undefined)).toBeNull();
    expect(drawnSentence({ first: null, last: null })).toBeNull();
  });
});
