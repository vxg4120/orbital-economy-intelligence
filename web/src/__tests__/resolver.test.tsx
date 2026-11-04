import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { ResultsList } from "../views/Resolver";

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
