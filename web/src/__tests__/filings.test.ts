import { describe, expect, it } from "vitest";
import { emptyCopy } from "../lib/filingsCopy";

/** The empty-state copy for the pending-filings list is chosen from the filter the page answered
    for; the three branches read differently and only a search offers "clear search". */
describe("emptyCopy", () => {
  it("names the query (and the cohort) and offers to clear the search", () => {
    const c = emptyCopy("acme", "spacex");
    expect(c.title).toBe("No pending applications match");
    expect(c.message).toContain("Nothing matches “acme” in cohort spacex");
    expect(c.clearable).toBe(true);
    expect(emptyCopy("acme", "").message).toContain("Nothing matches “acme”.");
  });
  it("points at the cohort filter when only the cohort is set", () => {
    const c = emptyCopy("", "spacex");
    expect(c.title).toBe("No pending applications match");
    expect(c.message).toBe("Nothing is pending from cohort spacex. Clear the cohort filter to see every application.");
    expect(c.clearable).toBe(false);
  });
  it("says the queue is empty when nothing is filtered", () => {
    expect(emptyCopy("", "")).toEqual({
      title: "No pending applications",
      message: "The pending queue is empty.",
      clearable: false,
    });
  });
});
