#!/usr/bin/env bash
# Build → isolated real backend → six workbench recordings → GIFs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="${AEROAGENTSIM_DOCS_WORK_DIR:-}"
PYTHON="${AEROAGENTSIM_DOCS_PYTHON:-python}"
FFMPEG="${FFMPEG:-}"
GATES=0
SKIP_BUILD=0
SKIP_GIFS=0
PORT="${AEROAGENTSIM_DOCS_PORT:-18960}"
REUSE_CONTEXT=""
FLOW_FILTER=""
MEDIA_ARGS=()
while [ "$#" -gt 0 ]; do
 case "$1" in
  --gates) GATES=1;;
  --skip-build) SKIP_BUILD=1;;
  --skip-gifs) SKIP_GIFS=1;;
  --ffmpeg) FFMPEG="$2"; shift;;
  --work-dir) WORK_DIR="$2"; shift;;
  --python) PYTHON="$2"; shift;;
  --reuse-context) REUSE_CONTEXT="$2"; shift;;
   --flows) FLOW_FILTER="$2"; shift;;
  --port) PORT="$2"; shift;;
  --speed|--trim|--fps|--width|--budget) MEDIA_ARGS+=("$1" "$2"); shift;;
  *) echo "Unknown argument: $1" >&2; exit 2;;
 esac
 shift
done
# Create a scratch directory only when none was chosen.
[ -n "$WORK_DIR" ] || WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aeroagentsim-docs-media.XXXXXX")"
# Resolve a basename default (e.g. "python") via command -v; --python wins.
if command -v "$PYTHON" >/dev/null 2>&1; then
 PYTHON="$(command -v "$PYTHON")"
fi
[ -x "$PYTHON" ] || { echo "Set --python or AEROAGENTSIM_DOCS_PYTHON to the installed Python with uvicorn and the backend dependencies" >&2; exit 1; }
case "$WORK_DIR" in "$ROOT"|"$ROOT"/*) echo "Scratch must be outside the repository" >&2; exit 1;; esac
mkdir -p "$WORK_DIR/tmp"
export TMPDIR="$WORK_DIR/tmp"
cd "$ROOT"
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
export AEROAGENTSIM_DOCS_WORKDIR="$WORK_DIR" AEROAGENTSIM_DOCS_ROOT="$WORK_DIR/backend"
export AEROAGENTSIM_DOCS_FRONTEND="$WORK_DIR/dist"
export AEROAGENTSIM_DOCS_VIDEO_DIR="$WORK_DIR/videos" AEROAGENTSIM_DOCS_CONTEXT="$WORK_DIR/context.json"
# Resolve the installed browser before selecting a scratch video-encoder cache.
if [ -z "${AEROAGENTSIM_CHROMIUM:-}" ]; then
 AEROAGENTSIM_CHROMIUM="$(cd frontend && node -p 'require("playwright").chromium.executablePath()')"
 export AEROAGENTSIM_CHROMIUM
 echo "Using Playwright's Chromium: $AEROAGENTSIM_CHROMIUM"
fi
[ -x "$AEROAGENTSIM_CHROMIUM" ] || { echo "AEROAGENTSIM_CHROMIUM is not an executable: $AEROAGENTSIM_CHROMIUM (try 'npx playwright install chromium')" >&2; exit 1; }
# No implicit private source or asset paths: an unset environment records the
# same packaged snapshot and lite city that a public clone receives.
if [ -n "${AEROAGENTSIM_TRAFFIC_ASSET_ROOT:-}" ]; then
 [ -d "$AEROAGENTSIM_TRAFFIC_ASSET_ROOT" ] || { echo "City asset directory does not exist" >&2; exit 1; }
 echo "Using explicitly configured city assets: $AEROAGENTSIM_TRAFFIC_ASSET_ROOT"
else
 echo "Recording with the service’s resolved catalog and installed or packaged city assets"
fi
if [ "$GATES" -eq 1 ]; then
 (cd frontend && npm run typecheck && npx vitest run)
fi
if [ "$SKIP_BUILD" -eq 0 ]; then
 (cd frontend && npx vite build --outDir "$WORK_DIR/dist" --emptyOutDir)
else
 [ -f "$WORK_DIR/dist/index.html" ] || { echo "No frontend build to reuse" >&2; exit 1; }
 echo "Using scratch frontend build (--skip-build)"
fi
if [ -z "$FFMPEG" ]; then
 [ -x "$WORK_DIR/venv/bin/python" ] || "$PYTHON" -m venv "$WORK_DIR/venv"
 "$WORK_DIR/venv/bin/python" -m pip install --quiet --no-cache-dir imageio-ffmpeg
 FFMPEG="$("$WORK_DIR/venv/bin/python" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
fi
# Playwright's downloadable encoder is unavailable on some older Linux hosts.
# Point its scratch registry at the explicitly selected, GIF-capable ffmpeg.
# Only linked after the browser is resolved: the scratch registry must not
# pre-create a browsers path that would defeat Playwright's own resolution.
export PLAYWRIGHT_BROWSERS_PATH="$WORK_DIR/browser-cache"
FFMPEG_REVISION="$(cd frontend && node -p "require('./node_modules/playwright-core/browsers.json').browsers.find(b=>b.name==='ffmpeg').revision")"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH/ffmpeg-$FFMPEG_REVISION"
ln -sfn "$FFMPEG" "$PLAYWRIGHT_BROWSERS_PATH/ffmpeg-$FFMPEG_REVISION/ffmpeg-linux"
echo "Using $FFMPEG for Playwright WebM recording and GIF conversion"
if [ -n "$REUSE_CONTEXT" ]; then
 PORT="$("$PYTHON" - "$REUSE_CONTEXT" <<'PY'
import json,sys,socket,urllib.parse
url=urllib.parse.urlparse(json.load(open(sys.argv[1]))["baseURL"])
if url.hostname != "127.0.0.1" or url.port is None:
    raise SystemExit("Context must use a local recording server")
with socket.socket() as s:
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind((url.hostname,url.port))
print(url.port)
PY
)"
fi
# Bind-check the owned port before starting; never attach to another server.
"$PYTHON" - "$PORT" <<'PYPORT'
import socket,sys
port=int(sys.argv[1])
if not 18960 <= port <= 18969:
    raise SystemExit('Recording port must be in 18960–18969')
with socket.socket() as s:
    s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.bind(('127.0.0.1',port))
PYPORT
export AEROAGENTSIM_CONSOLE_URL="http://127.0.0.1:$PORT"
export AEROAGENTSIM_DOCS_URL="$AEROAGENTSIM_CONSOLE_URL"
SERVER_PID=""
cleanup() {
 if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
  kill "$SERVER_PID"
  wait "$SERVER_PID" || true
 fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
PYTHONPATH=aerokernel:src "$PYTHON" -m uvicorn tools.docs.recording_server:app --host 127.0.0.1 --port "$PORT" >"$WORK_DIR/api.log" 2>&1 &
SERVER_PID=$!
"$PYTHON" - "$AEROAGENTSIM_CONSOLE_URL" <<'PY'
import sys,time,urllib.request,urllib.error
for _ in range(60):
    try:
        with urllib.request.urlopen(sys.argv[1]+'/v1/runs',timeout=2) as r:
            if r.status == 200:
                break
    except (OSError,urllib.error.URLError):
        time.sleep(.5)
else:
    raise SystemExit('Recording backend did not become ready; inspect api.log')
PY
if [ -z "$REUSE_CONTEXT" ]; then
 PYTHONPATH=aerokernel:src "$PYTHON" tools/docs/prepare_recording.py --base-url "$AEROAGENTSIM_CONSOLE_URL" --output "$AEROAGENTSIM_DOCS_CONTEXT"
else
 [ "$REUSE_CONTEXT" = "$AEROAGENTSIM_DOCS_CONTEXT" ] || cp "$REUSE_CONTEXT" "$AEROAGENTSIM_DOCS_CONTEXT"
fi
FLOW_ARGS=()
if [ -n "$FLOW_FILTER" ]; then
 FLOW_ARGS+=("$FLOW_FILTER")
 echo "Re-recording matching flows; preserving other successful recordings: $FLOW_FILTER"
else
 rm -f "$AEROAGENTSIM_DOCS_VIDEO_DIR/recordings.json"
fi
(cd frontend && npx playwright test --config playwright.flows.config.ts "${FLOW_ARGS[@]}")
if [ "$SKIP_GIFS" -eq 1 ]; then
 echo "Skipping GIF conversion (--skip-gifs); videos remain in $AEROAGENTSIM_DOCS_VIDEO_DIR"
else
 for name in workbench-food workbench-food-zh workbench-city; do
  cp "$WORK_DIR/screenshots/$name.png" "docs/media/$name.png"
 done
 "$PYTHON" tools/docs/make_gifs.py --input "$AEROAGENTSIM_DOCS_VIDEO_DIR" --output docs/media --ffmpeg "$FFMPEG" --report "$WORK_DIR/media-report.json" "${MEDIA_ARGS[@]}"
fi
