#!/usr/bin/env bash
# Build → isolated real backend → completed replay → eight paced recordings → GIFs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR=/tmp/aas-q/e3g
PYTHON="${AEROAGENTSIM_DOCS_PYTHON:-$ROOT/../AeroAgentSim-platform/.venv/bin/python}"
FFMPEG="${FFMPEG:-}"
GATES=0
SKIP_BUILD=0
REPLAY_SOURCE=""
REUSE_CONTEXT=""
MEDIA_ARGS=(--trim 00-overview=0.8:30 --trim 04-aerograph=0.8:30)
while [ "$#" -gt 0 ]; do
 case "$1" in
  --gates) GATES=1;;
  --skip-build) SKIP_BUILD=1;;
  --ffmpeg) FFMPEG="$2"; shift;;
  --work-dir) WORK_DIR="$2"; shift;;
  --python) PYTHON="$2"; shift;;
  --reuse-context) REUSE_CONTEXT="$2"; shift;;
  --replay-source) REPLAY_SOURCE="$2"; shift;;
  --speed|--trim) MEDIA_ARGS+=("$1" "$2"); shift;;
  *) echo "Unknown argument: $1" >&2; exit 2;;
 esac
 shift
done
[ -x "$PYTHON" ] || { echo "Set --python to the installed AeroAgentSim Python environment" >&2; exit 1; }
case "$WORK_DIR" in "$ROOT"|"$ROOT"/*) echo "Scratch must be outside the repository" >&2; exit 1;; esac
mkdir -p "$WORK_DIR"
cd "$ROOT"
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
export AEROAGENTSIM_DOCS_WORKDIR="$WORK_DIR" AEROAGENTSIM_DOCS_ROOT="$WORK_DIR/backend"
export AEROAGENTSIM_DOCS_VIDEO_DIR="$WORK_DIR/videos" AEROAGENTSIM_DOCS_CONTEXT="$WORK_DIR/context.json"
export AEROAGENTSIM_CHROMIUM="${AEROAGENTSIM_CHROMIUM:-$HOME/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell}"
# No implicit private source or asset paths: an unset environment records the
# same packaged snapshot and lite city that a public clone receives.
if [ -n "${AEROAGENTSIM_TRAFFIC_ASSET_ROOT:-}" ]; then
 [ -d "$AEROAGENTSIM_TRAFFIC_ASSET_ROOT" ] || { echo "City asset directory does not exist" >&2; exit 1; }
 echo "Using explicitly configured city assets: $AEROAGENTSIM_TRAFFIC_ASSET_ROOT"
else
 echo "Recording the public lite city and scenario registry snapshot"
fi
[ -x "$AEROAGENTSIM_CHROMIUM" ] || { echo "Set AEROAGENTSIM_CHROMIUM to Chromium" >&2; exit 1; }
if [ "$GATES" -eq 1 ]; then
 (cd frontend && npm run typecheck && npx vitest run)
fi
if [ "$SKIP_BUILD" -eq 0 ]; then
 (cd frontend && npm run build)
else
 [ -f frontend/dist/index.html ] || { echo "No frontend build to reuse" >&2; exit 1; }
 echo "Using existing frontend build (--skip-build)"
fi
if [ -z "$FFMPEG" ]; then
 [ -x "$WORK_DIR/venv/bin/python" ] || "$PYTHON" -m venv "$WORK_DIR/venv"
 "$WORK_DIR/venv/bin/python" -m pip install --quiet imageio-ffmpeg
 FFMPEG="$("$WORK_DIR/venv/bin/python" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
fi
export PLAYWRIGHT_BROWSERS_PATH="$WORK_DIR/browser-cache"
# Playwright's downloadable encoder is unavailable on some older Linux hosts.
# Point its scratch registry at the explicitly selected, GIF-capable ffmpeg.
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
    s.bind((url.hostname,url.port))
print(url.port)
PY
)"
else
PORT="$("$PYTHON" - <<'PY'
import socket
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    print(s.getsockname()[1])
PY
)"
fi
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
PYTHONPATH=src "$PYTHON" -m uvicorn tools.docs.recording_server:app --host 127.0.0.1 --port "$PORT" >"$WORK_DIR/api.log" 2>&1 &
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
PREPARE_ARGS=()
[ -z "$REPLAY_SOURCE" ] || PREPARE_ARGS+=(--replay-source "$REPLAY_SOURCE")
if [ -z "$REUSE_CONTEXT" ]; then
PYTHONPATH=src "$PYTHON" tools/docs/prepare_recording.py --base-url "$AEROAGENTSIM_CONSOLE_URL" --output "$AEROAGENTSIM_DOCS_CONTEXT" "${PREPARE_ARGS[@]}"
else
 [ "$REUSE_CONTEXT" = "$AEROAGENTSIM_DOCS_CONTEXT" ] || cp "$REUSE_CONTEXT" "$AEROAGENTSIM_DOCS_CONTEXT"
fi
rm -f "$AEROAGENTSIM_DOCS_VIDEO_DIR/recordings.json"
(cd frontend && npx playwright test --config playwright.flows.config.ts)
"$PYTHON" tools/docs/make_gifs.py --input "$AEROAGENTSIM_DOCS_VIDEO_DIR" --output docs/media --ffmpeg "$FFMPEG" --report "$WORK_DIR/media-report.json" "${MEDIA_ARGS[@]}"
