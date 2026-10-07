import { t } from "./i18n";

/** Presentation-only removal of absent fields; failures and zero/false remain visible. */
export function showAvailableContent(root: HTMLElement): void {
  root.querySelectorAll(".empty-row").forEach(node => node.remove());
  const absent = new Set([t("label.undeclared"), t("label.assetUnresolved"), "—", ""]);
  for (const node of root.querySelectorAll<HTMLElement>(".kv > strong, .status-line > strong")) {
    if (absent.has(node.textContent?.trim() ?? "")) node.parentElement?.remove();
  }
  for (const node of root.querySelectorAll<HTMLElement>(".scene-row > span")) {
    if (absent.has(node.textContent?.trim() ?? "")) node.remove();
  }
  for (const panel of root.querySelectorAll<HTMLElement>("section.panel")) {
    if ([...panel.children].every(node => node.matches(".panel-head, .control-note, .section-title"))) panel.remove();
  }
}
