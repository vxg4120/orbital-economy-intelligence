import { afterEach, describe, expect, it, vi } from "vitest";
import { coarsePointer } from "../lib/pointer";

/** The copy that names keyboard keys (c/i/p/u, n) is kept for a fine pointer and dropped for a
    touch device; without matchMedia (SSR, a test) it stays keyboard-first. */
describe("coarsePointer", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("is false where matchMedia does not exist", () => {
    expect(typeof matchMedia).toBe("undefined");
    expect(coarsePointer()).toBe(false);
  });
  it("follows the (pointer: coarse) media query", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(pointer: coarse)" }));
    expect(coarsePointer()).toBe(true);
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    expect(coarsePointer()).toBe(false);
  });
});
