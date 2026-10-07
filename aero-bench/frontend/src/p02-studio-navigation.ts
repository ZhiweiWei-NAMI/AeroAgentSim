/** Run and authoring have separate clocks; navigation preserves the Run source. */
export function studioConfigurationUrl(runUrl: URL): URL {
  const studio = new URL("./city-studio.html", runUrl);
  studio.searchParams.set("tab", "runtime");
  studio.searchParams.set("return", `${runUrl.pathname}${runUrl.search}${runUrl.hash}`);
  return studio;
}

export function applyStudioRunReturnLinks(root: HTMLElement, studioUrl: URL): void {
  const value = studioUrl.searchParams.get("return");
  if (value === null) return;
  const destination = new URL(value, studioUrl);
  if (destination.origin !== studioUrl.origin) {
    throw new Error("Studio Run return must use the same application origin");
  }
  for (const link of root.querySelectorAll<HTMLAnchorElement>('a[href="./?scene=1"]')) {
    link.href = destination.href;
    link.dataset.p02Return = "run";
  }
}
