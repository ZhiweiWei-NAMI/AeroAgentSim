import { mountViewerChrome } from "./viewer-chrome";

const root = document.getElementById("app");
if (root === null) throw new Error("Viewer chrome entry requires #app");
const chrome = mountViewerChrome(root, window.location.search);
window.addEventListener("beforeunload", () => chrome.dispose(), { once: true });
