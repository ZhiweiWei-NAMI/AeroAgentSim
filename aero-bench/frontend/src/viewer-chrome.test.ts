import { afterEach, describe, expect, it, vi } from "vitest";
import { mountViewerChrome, type ViewerChromeHandle } from "./viewer-chrome";

let handle: ViewerChromeHandle | undefined;
function viewer(search = "") {
  const root = document.createElement("div");
  root.innerHTML = `<section class="map"><div class="city-map"></div>
    <div class="city-preview-controls"><div class="city-preview-row"><button id="existing">切换夜景</button></div><span>原始来源</span></div>
    <div class="city-preview-controls" hidden><span>备用控制</span></div>
    <div class="map-hud"><div class="hud-note" role="status">ENGINEERING PREVIEW · 原始事实 · 非正式执行</div>
      <div class="map-legend"><div class="legend-title">图例</div><div class="legend-kinds">无人机</div><div class="attribution-note">© OpenStreetMap contributors</div></div></div></section>`;
  document.body.append(root);
  handle = mountViewerChrome(root, search);
  return root;
}
afterEach(() => { handle?.dispose(); handle = undefined; document.body.replaceChildren(); });

describe("compact viewer chrome", () => {
  it("starts with collapsed docks and legend, preserving application hidden state", () => {
    const root = viewer();
    const buttons = root.querySelectorAll(".viewer-chrome-toggle");
    expect(buttons).toHaveLength(4);
    for (const button of buttons) {
      expect(button.getAttribute("aria-expanded")).toBe("false");
      expect(root.querySelector(`#${button.getAttribute("aria-controls")}`)?.hasAttribute("id")).toBe(true);
      expect(button.getAttribute("aria-label")).toBeTruthy();
    }
    expect(root.querySelectorAll(".city-preview-controls")[1]?.hasAttribute("hidden")).toBe(true);
  });
  it("does not clone application controls or lose their event listeners", () => {
    const root = viewer();
    const original = root.querySelector<HTMLButtonElement>("#existing")!;
    const action = vi.fn(); original.addEventListener("click", action);
    root.querySelector<HTMLButtonElement>(".city-preview-controls > .viewer-chrome-toggle")!.click();
    expect(root.querySelector("#existing")).toBe(original);
    expect(root.querySelector<HTMLElement>(".city-preview-controls")?.dataset.chromeCollapsed).toBe("false");
    expect(original.closest<HTMLElement>(".viewer-chrome-content")?.inert).toBe(false);
    original.click(); expect(action).toHaveBeenCalledOnce();
  });
  it("keeps the original full ENGINEERING PREVIEW text reachable without changing authority", () => {
    const root = viewer();
    const host = root.querySelector<HTMLElement>(".viewer-chrome-provenance")!;
    const button = host.querySelector<HTMLButtonElement>("button")!;
    const note = host.querySelector<HTMLElement>(".hud-note")!;
    expect(button.textContent).toBe("ENGINEERING PREVIEW");
    expect(host.dataset.provenance).toBe("preview");
    button.click();
    expect(note.textContent).toBe("ENGINEERING PREVIEW · 原始事实 · 非正式执行");
    expect(note.inert).toBe(false);
    expect(button.getAttribute("aria-expanded")).toBe("true");
  });
  it("does not invent formal or preview provenance for unrelated source text", async () => {
    const root = viewer();
    root.querySelector(".hud-note")!.textContent = "Native city: public state only";
    await vi.waitFor(() => expect(root.querySelector(".viewer-chrome-provenance")?.getAttribute("data-provenance")).toBe("unknown"));
    expect(root.querySelector(".viewer-chrome-provenance button")?.textContent).toBe("来源说明");
  });
  it("tracks source updates and hidden notes without replacing the original node", async () => {
    const root = viewer();
    const note = root.querySelector<HTMLElement>(".hud-note")!;
    note.hidden = true;
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>(".viewer-chrome-provenance")?.hidden).toBe(true));
    note.hidden = false; note.textContent = "ENGINEERING PREVIEW · 新来源";
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>(".viewer-chrome-provenance")?.hidden).toBe(false));
    expect(root.querySelector(".hud-note")).toBe(note);
  });
  it("incorporates late-published unlabelled text inside their dock disclosure", async () => {
    const root = viewer();
    const dock = root.querySelector<HTMLElement>(".city-preview-controls")!;
    const text = document.createElement("span"); text.textContent = "渲染状态";
    dock.append(text);
    await vi.waitFor(() => expect(text.parentElement?.className).toBe("viewer-chrome-content"));
  });
  it("keeps labelled provenance chips visible outside the collapsed dock content", async () => {
    const root = viewer();
    const dock = root.querySelector<HTMLElement>(".city-preview-controls")!;
    const chip = document.createElement("span"); chip.className = "provenance-chip";
    chip.dataset.provenance = "osm"; chip.textContent = "OSM 标签地表 · 19 块";
    dock.append(chip);
    await vi.waitFor(() => expect(chip.parentElement).toBe(dock));
    expect(dock.dataset.chromeCollapsed).toBe("true");
    expect(dock.querySelector(".viewer-chrome-content")?.contains(chip)).toBe(false);
    expect(chip.previousElementSibling?.classList.contains("viewer-chrome-toggle")).toBe(true);
  });
  it("tracks the existing localized legend label", async () => {
    const root = viewer(); root.querySelector(".legend-title")!.textContent = "Legend";
    await vi.waitFor(() => expect(root.querySelector(".map-legend button")?.textContent).toBe("Legend"));
  });
  it("mounts after an asynchronous application shell appears", async () => {
    const root = document.createElement("div"); document.body.append(root);
    handle = mountViewerChrome(root, "?chrome=0");
    expect(root.querySelector(".viewer-chrome-header")).toBeNull();
    root.innerHTML = '<section class="map"><div class="map-hud"><div class="hud-note">ENGINEERING PREVIEW</div></div></section>';
    await vi.waitFor(() => expect(root.querySelector(".viewer-chrome-header")).not.toBeNull());
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(true);
  });
  it("re-homes the header and discloses the new note when the application replaces its shell", async () => {
    const root = viewer();
    await vi.waitFor(() => expect(root.querySelector(".map .viewer-chrome-provenance")).not.toBeNull());
    root.innerHTML = `<section class="map"><div class="map-hud"><div class="hud-note">来源：密封回放</div>
      <div class="map-legend"><div class="legend-title">图例</div></div></div></section>`;
    await vi.waitFor(() => expect(root.querySelector(".map > .viewer-chrome-header .hud-note")?.textContent).toBe("来源：密封回放"));
    expect(root.querySelectorAll(".viewer-chrome-header")).toHaveLength(1);
    expect(root.querySelectorAll(".viewer-chrome-provenance")).toHaveLength(1);
    expect(root.querySelector(".map > .map-legend > .viewer-chrome-toggle")?.textContent).toBe("图例");
    expect(root.querySelector(".map-hud .hud-note")).toBeNull();
  });
  it.each(["", "?chrome=1", "?chrome=false", "?chrome=00"])("does not hide chrome for %s", search => {
    viewer(search); expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(false);
  });
  it("supports explicit chrome=0 and an accessible restore button", () => {
    const root = viewer("?chrome=0");
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(true);
    const button = root.querySelector<HTMLButtonElement>(".viewer-chrome-visibility")!;
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.getAttribute("aria-keyshortcuts")).toBe("H");
    button.click(); expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(false);
  });
  it("toggles with H without deleting disclosure or preview states", () => {
    const root = viewer();
    const dock = root.querySelector<HTMLButtonElement>(".city-preview-controls > button")!; dock.click();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "H", bubbles: true, cancelable: true }));
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(true);
    expect(root.querySelector(".viewer-chrome-provenance button")?.textContent).toBe("ENGINEERING PREVIEW");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "h", bubbles: true }));
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(false);
    expect(dock.getAttribute("aria-expanded")).toBe("true");
  });
  it("collapses open provenance on entering H mode while keeping the source text reachable", () => {
    const root = viewer();
    const button = root.querySelector<HTMLButtonElement>(".viewer-chrome-provenance button")!;
    button.click(); expect(button.getAttribute("aria-expanded")).toBe("true");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "h", bubbles: true }));
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.textContent).toBe("ENGINEERING PREVIEW");
    button.click(); expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(root.querySelector(".hud-note")?.textContent).toContain("非正式执行");
  });
  it.each(['input', 'select', 'textarea', '[contenteditable="true"]', '[role="textbox"]'])("leaves typing in %s alone", selector => {
    const root = viewer();
    const input = document.createElement(selector.startsWith("[") ? "div" : selector);
    if (selector.startsWith("[contenteditable")) input.setAttribute("contenteditable", "true");
    if (selector.startsWith("[role")) input.setAttribute("role", "textbox");
    root.append(input); input.dispatchEvent(new KeyboardEvent("keydown", { key: "h", bubbles: true }));
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(false);
  });
  it.each([{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { repeat: true }])("ignores modified or repeated H: %j", options => {
    viewer(); document.dispatchEvent(new KeyboardEvent("keydown", { key: "h", bubbles: true, ...options }));
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(false);
  });
  it("Escape collapses a disclosure and restores focus to its button", () => {
    const root = viewer(); const button = root.querySelector<HTMLButtonElement>(".city-preview-controls > button")!;
    button.click(); root.querySelector("#existing")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(button.getAttribute("aria-expanded")).toBe("false"); expect(document.activeElement).toBe(button);
  });
  it("returns focus to the visible restore control when hiding a focused dock", () => {
    const root = viewer(); const existing = root.querySelector<HTMLButtonElement>("#existing")!; existing.focus();
    existing.dispatchEvent(new KeyboardEvent("keydown", { key: "h", bubbles: true }));
    expect(document.activeElement).toBe(root.querySelector(".viewer-chrome-visibility"));
  });
  it("disposes its observer, shortcut, wrappers and hidden mode without removing original controls", () => {
    const root = viewer("?chrome=0"); const original = root.querySelector("#existing");
    handle!.dispose(); handle = undefined;
    expect(root.querySelector(".viewer-chrome-header")).toBeNull();
    expect(root.querySelector(".viewer-chrome-content")).toBeNull();
    expect(root.querySelector("#existing")).toBe(original);
    expect(root.querySelector(".map-hud .hud-note")?.textContent).toContain("ENGINEERING PREVIEW");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "h", bubbles: true }));
    expect(document.body.classList.contains("viewer-chrome-hidden")).toBe(false);
  });
  it("removes its disclosure key handlers on disposal", () => {
    const root = viewer();
    root.querySelector<HTMLButtonElement>(".city-preview-controls > button")!.click();
    const event = vi.fn(); root.addEventListener("keydown", event);
    handle!.dispose(); handle = undefined;
    root.querySelector("#existing")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(event).toHaveBeenCalledOnce();
  });
});
