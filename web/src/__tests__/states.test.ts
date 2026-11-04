import { describe, expect, it } from "vitest";
import { visitorMessage } from "../components/States";

/** The raw error strings come from api/client.ts (realGet) and the browser; a visitor reads a
    sentence, and the raw line stays in the title for whoever debugs. */
describe("visitorMessage", () => {
  it("translates the browser's network failures", () => {
    expect(visitorMessage("Failed to fetch")).toMatch(/did not answer/);
    expect(visitorMessage("NetworkError when attempting to fetch resource.")).toMatch(/did not answer/);
    expect(visitorMessage("Load failed")).toMatch(/did not answer/);
  });
  it("names the timeout the client enforces", () => {
    expect(visitorMessage("No answer from the API within 30s for /stats")).toMatch(/30 seconds/);
  });
  it("maps the status line the client throws", () => {
    expect(visitorMessage("404 Not Found for /satellite/1")).toMatch(/Nothing in the catalog/);
    expect(visitorMessage("503 Service Unavailable for /stats")).toMatch(/on its side/);
    expect(visitorMessage("429 Too Many Requests for /search")).toMatch(/Too many requests/);
    expect(visitorMessage("422 Unprocessable Entity for /x")).toBe(
      "The API turned the request down (status 422).",
    );
  });
  it("passes the API's own detail text through on a 4xx that has one", () => {
    expect(visitorMessage("422 unknown site 'mars' for /passes")).toBe(
      "The API turned the request down: unknown site 'mars'",
    );
  });
  it("explains a body that is not JSON instead of quoting the parser", () => {
    expect(visitorMessage("Not JSON from the API for /stats")).toMatch(/not data/);
    expect(visitorMessage("Unexpected token '<', \"<!doctype \"... is not valid JSON")).toMatch(/not data/);
  });
  it("leaves an unknown message alone", () => {
    expect(visitorMessage("something else entirely")).toBeNull();
  });
});
