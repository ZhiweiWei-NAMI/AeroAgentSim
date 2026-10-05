import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve("src/styles.css"), "utf8");
function token(name: string): string {
  const value = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`))?.[1];
  if (value === undefined) throw new Error(`Missing opaque colour token: ${name}`);
  return value;
}
function luminance(hex: string): number {
  const values = [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return values[0]! * .2126 + values[1]! * .7152 + values[2]! * .0722;
}
function contrast(text: string, background: string): number {
  const values = [luminance(text), luminance(background)].sort((a, b) => b - a);
  return (values[0]! + .05) / (values[1]! + .05);
}
const inks = ["ink-strong", "ink-default", "ink-dim", "ink-muted", "ink-faint", "accent-primary", "accent-bright",
  "status-positive", "status-warning", "status-danger", "status-info"];
const surfaces = ["surface-canvas", "surface-raised", "surface-panel", "surface-panel-strong", "surface-control", "surface-hover"];
describe("viewer and shared Studio token contrast", () => {
  it.each(inks.flatMap(ink => surfaces.map(surface => [ink, surface])))("%s on %s meets 4.5:1", (ink, surface) => {
    expect(contrast(token(ink!), token(surface!))).toBeGreaterThanOrEqual(4.5);
  });
  it("preview label meets 4.5:1 on its opaque warning surface", () => {
    expect(contrast(token("status-warning"), "#211e16")).toBeGreaterThanOrEqual(4.5);
  });
  it("keeps authored, preview and formal colours separate, with no authority inferred by CSS", () => {
    expect(css).toContain('[data-provenance="preview"]');
    expect(css).toContain('[data-provenance="formal"]');
    expect(css).toContain('[data-provenance="authored"]');
  });
});
