import { describe, expect, it } from "vitest";
import { compact, DASH, fmtDate, fmtDateTime, fmtInt, fmtNum, fmtPct, fmtRangeEnd, fmtYear } from "../lib/format";

describe("fmtInt / compact", () => {
  it("renders null as the em dash, never a blank", () => {
    expect(fmtInt(null)).toBe(DASH);
    expect(compact(undefined)).toBe(DASH);
  });
  it("keeps the thousands separator below ten thousand and compacts above it", () => {
    expect(fmtInt(1284)).toBe("1,284");
    expect(compact(1284)).toBe("1,284");
    expect(compact(9999)).toBe("9,999");
    expect(compact(12_900)).toBe("12.9K");
    expect(compact(418_086)).toBe("418.1K");
    expect(compact(4_200_000)).toBe("4.2M");
    expect(compact(1_300_000_000)).toBe("1.3B");
  });
  it("promotes a value that rounds to 1000 of a unit to the next unit", () => {
    expect(compact(999_950)).toBe("1M");
    expect(compact(999_949)).toBe("999.9K");
    expect(compact(999_950_000)).toBe("1B");
  });
  it("drops a trailing .0 so a round figure reads 70K, not 70.0K", () => {
    expect(compact(70_000)).toBe("70K");
    expect(compact(-70_000)).toBe("-70K");
  });
});

describe("fmtNum / fmtPct", () => {
  it("fixes the decimals and separates thousands", () => {
    expect(fmtNum(1234.5)).toBe("1,234.50");
    expect(fmtNum(0.042, 3)).toBe("0.042");
    expect(fmtPct(92.44)).toBe("92.4%");
    expect(fmtPct(null)).toBe(DASH);
  });
});

describe("dates", () => {
  it("fmtDate keeps the calendar date of an ISO timestamp", () => {
    expect(fmtDate("2026-07-08T04:24:00+00:00")).toBe("2026-07-08");
    expect(fmtDate(null)).toBe(DASH);
  });
  it("fmtDateTime renders in UTC regardless of the host zone and passes junk through", () => {
    expect(fmtDateTime("2026-07-08T04:24:00+02:00")).toBe("2026-07-08 02:24 UTC");
    expect(fmtDateTime("not a date")).toBe("not a date");
  });
  it("fmtYear keeps the year alone for a narrow timeline segment", () => {
    expect(fmtYear("2020-03-21")).toBe("2020");
    expect(fmtYear("2026-07-08T04:24:00+00:00")).toBe("2026");
    expect(fmtYear(null)).toBe(DASH);
  });
  it("an open-ended range end reads as present", () => {
    expect(fmtRangeEnd(null)).toBe("present");
    expect(fmtRangeEnd("2027-01-01")).toBe("2027-01-01");
  });
});
