import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Pager } from "../components/DataTable";
import { Async } from "../components/States";

const noop = () => {};

/** The pager prints the range this page really covers; a page past the end (the list shrank
    since it was counted) says so instead of an inverted "51–50". */
describe("Pager", () => {
  it("ends the range where the page ends", () => {
    const html = renderToStaticMarkup(
      <Pager offset={50} limit={50} total={1435} shown={50} onOffset={noop} />,
    );
    expect(html).toContain("51–100 of 1,435");
  });
  it("names an empty page instead of printing an inverted range", () => {
    const html = renderToStaticMarkup(
      <Pager offset={50} limit={50} total={1435} shown={0} onOffset={noop} />,
    );
    expect(html).toContain("nothing on this page · 1,435 total");
    expect(html).not.toContain("51–50");
  });
  it("prints 0–0 of 0 for an empty list", () => {
    const html = renderToStaticMarkup(<Pager offset={0} limit={50} total={0} shown={0} onOffset={noop} />);
    expect(html).toContain("0–0 of 0");
  });
  // the Prev just pressed to reach page one stays focusable while it goes inert: a control
  // disabled under the keyboard focus drops it to the body
  it("marks the end buttons aria-disabled, never disabled", () => {
    const first = renderToStaticMarkup(<Pager offset={0} limit={50} total={60} shown={50} onOffset={noop} />);
    expect(first).toContain('aria-disabled="true">‹ Prev');
    expect(first).toContain('<button class="btn">Next ›');
    expect(first).not.toContain(" disabled");
    const last = renderToStaticMarkup(<Pager offset={50} limit={50} total={60} shown={10} onOffset={noop} />);
    expect(last).toContain('<button class="btn">‹ Prev');
    expect(last).toContain('aria-disabled="true">Next ›');
  });
});

/** A list refetching with useApi's `keep` stays on screen, dimmed and marked busy, so the pager
    that asked for the next page is never unmounted under the keyboard focus. */
describe("Async with a kept list", () => {
  const list = (rows: string[]) => (
    <ul>
      {rows.map((r) => (
        <li key={r}>{r}</li>
      ))}
    </ul>
  );
  it("keeps the previous rows mounted inside a busy wrapper while reloading", () => {
    const state = { data: ["row-a", "row-b"], loading: true, error: null, reload: noop, keep: true };
    const html = renderToStaticMarkup(<Async state={state}>{list}</Async>);
    expect(html).toContain('<div class="reloading" aria-busy="true">');
    expect(html).toContain("<li>row-a</li>");
  });
  it("renders the same wrapper, no longer busy, once the page lands (no remount under focus)", () => {
    const state = { data: ["row-a"], loading: false, error: null, reload: noop, keep: true };
    const html = renderToStaticMarkup(<Async state={state}>{list}</Async>);
    expect(html).toBe("<div><ul><li>row-a</li></ul></div>");
  });
  it("adds no wrapper to a one-shot fetch", () => {
    const state = { data: ["row-a"], loading: false, error: null, reload: noop };
    const html = renderToStaticMarkup(<Async state={state}>{list}</Async>);
    expect(html).toBe("<ul><li>row-a</li></ul>");
  });
  it("shows the loader, not a wrapper, when there is nothing to keep yet", () => {
    const state = { data: null, loading: true, error: null, reload: noop, keep: true };
    const html = renderToStaticMarkup(<Async state={state}>{list}</Async>);
    expect(html).not.toContain("reloading");
    expect(html).toContain("state--loading");
  });
});
