/**
 * Write a contact sheet (evenly spaced frames in a grid) for a captured WebM so the clip can
 * be inspected as a still image. The host has no ffmpeg; Chromium decodes the video and a
 * 2D canvas composes the sheet. The sheet is an inspection aid, not new evidence.
 *
 * Usage:
 *   node frontend/scripts/extract-video-contact-sheet.mjs <video.webm> <sheet.png> [--frames=8] [--columns=4] [--width=480] [--tail-seconds=S]
 *
 * --tail-seconds limits the sampled window to the final S seconds (e.g. the follow portion
 * of a clip whose beginning records the scene load).
 */
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const positional = process.argv.slice(2).filter(arg => !arg.startsWith("--"));
const options = Object.fromEntries(process.argv.slice(2).filter(arg => arg.startsWith("--"))
  .map(arg => { const [key, value] = arg.slice(2).split("="); return [key, value]; }));
const [videoArg, sheetArg] = positional;
if (videoArg === undefined || sheetArg === undefined) throw new Error("Usage: <video.webm> <sheet.png> [--frames=8]");
const tailSeconds = options['tail-seconds'] === undefined ? null : Number(options['tail-seconds']);
if (tailSeconds !== null && !(tailSeconds > 0)) throw new Error('--tail-seconds must be positive');
const frameCount = Number(options.frames ?? 8), columns = Number(options.columns ?? 4), width = Number(options.width ?? 480);
if (![frameCount, columns, width].every(value => Number.isInteger(value) && value > 0)) throw new Error("Invalid sheet options");

const bytes = await readFile(resolve(videoArg));
const browser = await chromium.launch({ channel: "chromium", headless: true });
try {
  const page = await browser.newPage();
  await page.setContent("<html><body></body></html>");
  const dataUrl = await page.evaluate(async ({ base64, frameCount, columns, width, tailSeconds }) => {
    const blob = new Blob([Uint8Array.from(atob(base64), char => char.charCodeAt(0))], { type: "video/webm" });
    const video = document.createElement("video");
    video.muted = true; video.src = URL.createObjectURL(blob);
    await new Promise((resolve, reject) => { video.onloadedmetadata = resolve; video.onerror = () => reject(new Error("video decode failed")); });
    const seek = time => new Promise(resolve => { video.onseeked = resolve; video.currentTime = time; });
    // MediaRecorder WebM files often report an infinite duration until the end is seeked.
    if (!Number.isFinite(video.duration)) await seek(1e7);
    const duration = video.duration;
    if (!(duration > 0)) throw new Error(`video duration is unavailable: ${duration}`);
    const height = Math.round(width * video.videoHeight / video.videoWidth);
    const rows = Math.ceil(frameCount / columns);
    const canvas = document.createElement("canvas");
    canvas.width = width * columns; canvas.height = (height + 18) * rows;
    const context = canvas.getContext("2d");
    context.fillStyle = "#000"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.font = "14px monospace"; context.fillStyle = "#ff0";
    for (let index = 0; index < frameCount; index++) {
      const windowStart = tailSeconds === null ? 0 : Math.max(0, duration - tailSeconds);
      const time = windowStart + (duration - windowStart) * (index + 0.5) / frameCount;
      await seek(time);
      const x = (index % columns) * width, y = Math.floor(index / columns) * (height + 18);
      context.drawImage(video, x, y + 18, width, height);
      context.fillText(`t=${time.toFixed(2)}s / ${duration.toFixed(2)}s`, x + 4, y + 14);
    }
    return canvas.toDataURL("image/png");
  }, { base64: bytes.toString("base64"), frameCount, columns, width, tailSeconds });
  await writeFile(resolve(sheetArg), Buffer.from(dataUrl.split(",")[1], "base64"));
  console.log(resolve(sheetArg));
} finally {
  await browser.close();
}
