import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { AuditSummary } from "../api/types";
import auditFixture from "../api/fixtures/audit_summary.json";
import { AuditStrip } from "../components/AuditStrip";

const summary = auditFixture as unknown as AuditSummary;
const render = (s: AuditSummary) => renderToStaticMarkup(<AuditStrip summary={s} />);
const text = (html: string) => html.replace(/<[^>]+>/g, "").replace(/&#x27;/g, "'");

/** The Kuiper card states the FCC obligation of DA 26-553 (1,616 by 2026-07-30), judges it on
    what was in orbit on that date, and says what the order did and did not do about the miss. */
describe("Kuiper milestone", () => {
  it("measures the July 2026 obligation against the satellites in orbit on the deadline", () => {
    const t = text(render(summary));
    expect(t).toContain("/ 1,616 required");
    expect(t).toContain("missed by 1,224");
    expect(t).toContain("(392 in orbit on that date)");
    expect(t).not.toContain("1,618");
  });
  it("cites the FCC order and says the deadline was not extended", () => {
    const html = render(summary);
    expect(html).toContain('href="https://docs.fcc.gov/public/attachments/DA-26-553A1.pdf"');
    const t = text(html);
    expect(t).toContain("The FCC did not extend this deadline");
    expect(t).toContain("order (DA 26-553) waived");
    expect(t).toContain("until July 30, 2029 to deploy all 3,232");
  });
  it("leaves the order out when the obligation was met", () => {
    const met = {
      ...summary,
      kuiper_milestone: { ...summary.kuiper_milestone, deployed_by_deadline: 1700 },
    };
    const t = text(render(met));
    expect(t).toContain("obligation met with 1,700 in orbit on that date");
    expect(t).not.toContain("DA 26-553");
  });
});

/** The lingering panel counts only the eight fleets whose element history is loaded, and it says
    so, so that an absent operator is not read as one with no lingering payloads. */
describe("Lingering LEO payloads", () => {
  it("names the fleet universe it counts", () => {
    const t = text(render(summary));
    expect(t).toContain("eight fleets");
    expect(t).toContain(
      "Only the eight fleets whose Space-Track element history has been loaded since mid-2025 are counted",
    );
    expect(t).toContain("A fleet with no lingering payloads is not listed.");
  });
  it("says which universe is empty when no fleet has a lingering payload", () => {
    const t = text(render({ ...summary, lingering_leaderboard: [] }));
    expect(t).toContain("None of the eight fleets has an inactive payload loitering above 500 km.");
  });
});
