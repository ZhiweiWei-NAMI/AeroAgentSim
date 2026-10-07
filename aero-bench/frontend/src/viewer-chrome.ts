import { subscribeLanguage, t } from "./i18n";

/** DOM-only disclosures. Original application nodes, listeners and authority stay intact. */
export interface ViewerChromeHandle { dispose(): void }

function isProvenanceChip(node: Node): boolean {
  return node instanceof node.ownerDocument!.defaultView!.HTMLElement
    && node.classList.contains("provenance-chip") && node.dataset.provenance !== undefined;
}

export function mountViewerChrome(root: HTMLElement, search: string): ViewerChromeHandle {
  const document = root.ownerDocument;
  const browserWindow = document.defaultView;
  if (browserWindow === null) throw new Error("Viewer chrome requires a browser document");
  const window = browserWindow;
  const body = document.body;
  const disclosures = new Map<HTMLElement, { button: HTMLButtonElement; content: HTMLElement; escape: (event: KeyboardEvent) => void }>();
  let sequence = 0;
  let header: HTMLElement | null = null;
  let visibility: HTMLButtonElement | null = null;
  let provenance: HTMLElement | null = null;
  let note: HTMLElement | null = null;
  let disposed = false;
  const hiddenAtStart = new URLSearchParams(search).get("chrome") === "0";

  function setHidden(hidden: boolean): void {
    const active = document.activeElement;
    body.classList.toggle("viewer-chrome-hidden", hidden);
    if (hidden && provenance !== null) {
      const disclosure = disclosures.get(provenance)!;
      provenance.dataset.chromeCollapsed = "true";
      disclosure.button.setAttribute("aria-expanded", "false");
      disclosure.content!.inert = true;
    }
    if (visibility !== null) {
      visibility.setAttribute("aria-pressed", String(hidden));
      visibility.setAttribute("aria-label", hidden ? "显示界面（H）" : "隐藏界面（H）");
      visibility.title = hidden ? "显示界面（H）" : "隐藏界面（H）；截图模式 ?chrome=0";
      if (hidden && active instanceof window.HTMLElement && root.contains(active)) visibility.focus();
    }
  }

  function disclose(host: HTMLElement, label: string, content?: HTMLElement): HTMLButtonElement {
    host.dataset.chromeCollapsed = "true";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "viewer-chrome-toggle";
    button.textContent = label;
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", label);
    const target = content ?? document.createElement("div");
    target.id ||= `viewer-chrome-content-${++sequence}`;
    if (content === undefined) {
      target.className = "viewer-chrome-content";
      target.append(...host.childNodes);
    }
    target.inert = true;
    button.setAttribute("aria-controls", target.id);
    host.prepend(button);
    host.append(target);
    button.addEventListener("click", () => {
      const expanded = button.getAttribute("aria-expanded") !== "true";
      host.dataset.chromeCollapsed = String(!expanded);
      button.setAttribute("aria-expanded", String(expanded));
      target.inert = !expanded;
    });
    const escape = (event: KeyboardEvent): void => {
      if (event.key !== "Escape" || button.getAttribute("aria-expanded") !== "true") return;
      host.dataset.chromeCollapsed = "true";
      button.setAttribute("aria-expanded", "false");
      target.inert = true;
      button.focus();
      event.stopPropagation();
    };
    host.addEventListener("keydown", escape);
    disclosures.set(host, { button, content: target, escape });
    return button;
  }

  function reconcile(): void {
    if (disposed) return;
    const map = root.querySelector<HTMLElement>(".map");
    if (map === null) return;
    if (header === null) {
      header = document.createElement("div");
      header.className = "viewer-chrome-header";
      visibility = document.createElement("button");
      visibility.type = "button";
      visibility.className = "viewer-chrome-visibility";
      visibility.textContent = "H";
      visibility.setAttribute("aria-keyshortcuts", "H");
      visibility.addEventListener("click", () => setHidden(!body.classList.contains("viewer-chrome-hidden")));
      header.append(visibility);
      map.append(header);
      setHidden(hiddenAtStart);
    }
    // Opening a Control replay rebuilds the application shell. Re-home the chrome on the new
    // map and retire disclosures whose hosts left the document with the old shell.
    if (header.parentElement !== map) map.append(header);
    for (const [host, { escape }] of disclosures) {
      if (host.isConnected) continue;
      host.removeEventListener("keydown", escape);
      disclosures.delete(host);
    }
    for (const dock of map.querySelectorAll<HTMLElement>(".city-preview-controls")) {
      if (!disclosures.has(dock)) disclose(dock, "城市控制");
      // Labelled provenance chips stay visible beside the toggle; controls and late text collapse.
      const { button, content } = disclosures.get(dock)!;
      for (const child of Array.from(dock.childNodes)) {
        if (child === content || child === button) continue;
        if (!isProvenanceChip(child)) content.append(child);
        else if (content.compareDocumentPosition(child) & Node.DOCUMENT_POSITION_FOLLOWING) dock.insertBefore(child, content);
      }
      for (const child of Array.from(content.children)) {
        if (isProvenanceChip(child)) dock.insertBefore(child, content);
      }
    }
    const legend = map.querySelector<HTMLElement>(".map-legend");
    if (legend !== null) {
      if (legend.parentElement?.classList.contains("map-hud")) map.append(legend);
      const label = legend.querySelector(".legend-title")?.textContent?.trim();
      if (!label) throw new Error("Viewer legend is missing its application label");
      const button = disclosures.get(legend)?.button ?? disclose(legend, label);
      if (button.textContent !== label) { button.textContent = label; button.setAttribute("aria-label", label); }
    }
    const freshNote = Array.from(map.querySelectorAll<HTMLElement>(".hud-note")).find(candidate => !header!.contains(candidate)) ?? null;
    if (freshNote !== null && provenance !== null) {
      // The previous shell's note is gone; disclose the replacement shell's note instead.
      provenance.removeEventListener("keydown", disclosures.get(provenance)!.escape);
      disclosures.delete(provenance);
      provenance.remove();
      provenance = null;
    }
    if (freshNote !== null) note = freshNote;
    if (note !== null && provenance === null) {
      provenance = document.createElement("div");
      provenance.className = "viewer-chrome-provenance";
      header.append(provenance);
      disclose(provenance, t("chrome.source"), note);
    }
    if (note !== null && provenance !== null) {
      const text = note.textContent?.trim() ?? "";
      const preview = /ENGINEERING PREVIEW/i.test(text);
      const label = preview ? "ENGINEERING PREVIEW" : t("chrome.source");
      const button = disclosures.get(provenance)!.button;
      if (button.textContent !== label) {
        button.textContent = label;
        button.setAttribute("aria-label", label);
      }
      const kind = preview ? "preview" : "unknown";
      if (provenance.dataset.provenance !== kind) provenance.dataset.provenance = kind;
      const hidden = note.hidden || text.length === 0;
      if (provenance.hidden !== hidden) provenance.hidden = hidden;
    }
  }

  function keyboard(event: KeyboardEvent): void {
    const target = event.target;
    if (event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey
      || event.key.toLowerCase() !== "h" || header === null) return;
    if (target instanceof window.Element && target.closest('input,select,textarea,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
    event.preventDefault();
    setHidden(!body.classList.contains("viewer-chrome-hidden"));
  }
  const observer = new window.MutationObserver(reconcile);
  observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });
  document.addEventListener("keydown", keyboard);
  reconcile();
  const unsubscribeLanguage = subscribeLanguage(reconcile);
  return { dispose() {
    disposed = true;
    observer.disconnect();
    unsubscribeLanguage();
    document.removeEventListener("keydown", keyboard);
    body.classList.remove("viewer-chrome-hidden");
    const hud = root.querySelector(".map-hud");
    if (note !== null && hud !== null) { note.inert = false; hud.append(note); }
    for (const [host, { button, content, escape }] of disclosures) {
      host.removeEventListener("keydown", escape);
      button.remove(); delete host.dataset.chromeCollapsed;
      if (content !== undefined && content !== note) { host.append(...content.childNodes); content.remove(); }
      if (host.classList.contains("map-legend") && hud !== null) hud.append(host);
    }
    header?.remove();
  } };
}
