import { describe, expect, it } from "vitest";
import { applyStudioRunReturnLinks, studioConfigurationUrl } from "./p02-studio-navigation";

describe("P02 actual Studio navigation", () => {
  it("opens the editable workspace while keeping the exact Run source for return", () => {
    const run = new URL("http://127.0.0.1:5413/?view=replay&trace=%2Ftrace.json&city=%2Fcity.json#run");
    const studio = studioConfigurationUrl(run);
    expect(studio.pathname).toBe("/city-studio.html");
    expect(studio.searchParams.get("tab")).toBe("runtime");
    const root = document.createElement("div");
    root.innerHTML = '<a href="./?scene=1">Run</a><a href="./asset-library.html">Assets</a>';
    applyStudioRunReturnLinks(root, studio);
    expect(root.querySelector<HTMLAnchorElement>('a[data-p02-return="run"]')!.href).toBe(run.href);
    expect(root.querySelector<HTMLAnchorElement>('a[href="./asset-library.html"]')).not.toBeNull();
  });

  it("does not redirect a direct Studio visit or accept an external Run origin", () => {
    const root = document.createElement("div");
    root.innerHTML = '<a href="./?scene=1">Run</a>';
    applyStudioRunReturnLinks(root, new URL("http://127.0.0.1:5413/city-studio.html"));
    expect(root.firstElementChild!.getAttribute("href")).toBe("./?scene=1");
    expect(() => applyStudioRunReturnLinks(root,
      new URL("http://127.0.0.1:5413/city-studio.html?return=https%3A%2F%2Fexample.com"))).toThrow("same application origin");
  });
});
