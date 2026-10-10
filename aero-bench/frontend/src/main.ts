import "./styles.css";
import { initLanguage, subscribeLanguage, t } from "./i18n";

initLanguage();

const root = document.querySelector<HTMLElement>("#app");
if (root === null) {
  throw new Error("AERO-BENCH viewer root is missing");
}

const sceneView = new URLSearchParams(window.location.search).get("scene") === "1";
document.body.classList.toggle("scene-view", sceneView);
const sceneToggle = document.createElement("button");
sceneToggle.className = "scene-view-toggle";
sceneToggle.type = "button";
sceneToggle.textContent = sceneView ? "↙" : "↗";
sceneToggle.title = "Toggle city view";
sceneToggle.setAttribute("aria-label", "Toggle city view");
sceneToggle.setAttribute("aria-pressed", String(sceneView));
sceneToggle.addEventListener("click", () => {
  const active = document.body.classList.toggle("scene-view");
  sceneToggle.textContent = active ? "↙" : "↗";
  sceneToggle.setAttribute("aria-pressed", String(active));
  const url = new URL(window.location.href);
  if (active) url.searchParams.set("scene", "1"); else url.searchParams.delete("scene");
  window.history.replaceState(null, "", url);
});
document.body.append(sceneToggle);

const libraryLink = document.createElement("a");
libraryLink.className = "scene-library-link";
libraryLink.href = "./asset-library.html";
libraryLink.textContent = t("chrome.library");

const studioLink = document.createElement("a");
studioLink.className = "scene-library-link scene-studio-link";
studioLink.href = "./city-studio.html?tab=runtime";
studioLink.textContent = t("chrome.workspace");
// Group localized links so longer labels keep their gap from the language switch.
const sceneNavigation = document.createElement("nav");
sceneNavigation.className = "scene-navigation";
Object.assign(sceneNavigation.style, { position: "fixed", top: "12px", right: "120px", display: "flex", gap: "12px", zIndex: "20" });
libraryLink.style.position = studioLink.style.position = "static";
sceneNavigation.append(studioLink, libraryLink);
document.body.append(sceneNavigation);
const unsubscribeLanguage = subscribeLanguage(() => {
  libraryLink.textContent = t("chrome.library");
  studioLink.textContent = t("chrome.workspace");
});
window.addEventListener("beforeunload", unsubscribeLanguage, { once: true });

const loading = root.querySelector<HTMLElement>(".viewer-startup");
if (loading === null) throw new Error("City viewer startup indicator is missing");

void import("./app").then(({ PublicTraceApp, viewModeFromLocation }) => {
  const app = new PublicTraceApp(root, viewModeFromLocation());
  void app.start();
  window.addEventListener("beforeunload", () => app.dispose(), { once: true });
}).catch(error => {
  loading.textContent = `控制台加载失败：${error instanceof Error ? error.message : String(error)}`;
  loading.setAttribute("role", "alert");
});
