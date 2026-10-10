// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("project favicon", () => {
  it.each(["index.html", "city-studio.html"])("links a published SVG diamond from %s", entry => {
    const html = readFileSync(new URL(`../${entry}`, import.meta.url), "utf8");
    expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/aero-bench-icon.svg" />');
    const icon = readFileSync(new URL("../public/aero-bench-icon.svg", import.meta.url), "utf8");
    expect(icon).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(icon).toContain('d="M32 8 56 32 32 56 8 32Z"');
    expect(icon).not.toMatch(/<script|https?:\/\/(?!www.w3.org)/);
  });
});
