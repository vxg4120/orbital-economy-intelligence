import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { AssertionLine, ResultsList } from "../views/Resolver";

const noop = () => {};
const render = (props: Partial<Parameters<typeof ResultsList>[0]>) =>
  renderToStaticMarkup(
    <StaticRouter location="/resolver">
      <ResultsList
        debounced=""
        loading={false}
        error={null}
        reload={noop}
        results={[]}
        activeId={null}
        onPick={noop}
        {...props}
      />
    </StaticRouter>,
  );

/** The result panel's three states: a failed search keeps a Retry, a fruitless one echoes the
    query and brings the examples back, and an empty box shows the examples alone. */
describe("ResultsList", () => {
  it("offers Retry when the search failed", () => {
    const html = render({ debounced: "star", error: "Failed to fetch" });
    expect(html).toContain("Signal lost");
    expect(html).toContain(">Retry<");
  });
  it("echoes a query that matched nothing and restores the examples", () => {
    const html = render({ debounced: "zzzzqqq" });
    expect(html).toContain('No matches for <q class="state__q">zzzzqqq</q>');
    expect(html).toContain('class="example-list"');
  });
  it("shows the examples alone while the box is empty", () => {
    const html = render({ debounced: "" });
    expect(html).toContain("Try an example");
    expect(html).not.toContain("No matches");
  });
});

/** GCAT's date is the end of a phase of the object's history. Under a docking or a transfer it
    is not a decay date, and the claims grid has to say so beside the value, because the row
    sits under the heading "decay_date". */
describe("AssertionLine", () => {
  const claim = { attribute: "decay_date", source: "gcat", value: "2015 Feb 18", observed_at: "2026-11-13T19:10:38Z" };
  it("says that a phase-end date is not a decay", () => {
    const html = renderToStaticMarkup(<AssertionLine a={{ ...claim, phase_end: true }} />);
    expect(html).toContain("2015 Feb 18");
    expect(html).toContain("end of a phase, not a decay");
  });
  it("adds nothing to a claim that is a decay date", () => {
    const html = renderToStaticMarkup(<AssertionLine a={claim} />);
    expect(html).toContain("2015 Feb 18");
    expect(html).not.toContain("end of a phase");
  });
});
