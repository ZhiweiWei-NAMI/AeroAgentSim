import "./styles.css";

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
libraryLink.textContent = "返回素材库";
document.body.append(libraryLink);

const studioLink = document.createElement("a");
studioLink.className = "scene-library-link scene-studio-link";
studioLink.href = "./city-studio.html?tab=runtime";
studioLink.textContent = "城市运行工作区";
studioLink.style.right = "164px";
document.body.append(studioLink);

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
