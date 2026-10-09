"""Convert real Playwright flow recordings into documentation GIFs.

Reads a Playwright video output tree (one directory per flow, each holding
``video.webm``, or a root ``recordings.json`` manifest listing the productive
segments per flow) and produces ``docs/media/<flow>.gif`` with a two-pass
ffmpeg palettegen/paletteuse conversion. Long idle waits are removed by
concatenating only the manifest-marked segments; no synthetic footage is ever
generated. A hard per-flow size budget is enforced at the requested width and
frame rate. Use explicit speed/trim options to shorten oversized footage. Failed
recordings are reported and fail the run; they are never skipped silently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

HERO_PATTERN = "overview"
DEFAULT_FPS = 12
DEFAULT_WIDTH = 960
BUDGET_BYTES = 6_000_000
HERO_BUDGET_BYTES = 8_000_000
MAX_COLORS = 48
RUN_TIMEOUT_S = 900.0
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Segment:
    """One productive span of a real recording."""

    path: Path
    start: float
    duration: float | None  # None: keep the whole recording.


@dataclass
class Flow:
    """A named flow and its real source segments."""

    name: str
    segments: list[Segment]
    speed: float = 1.0
    black_prelude: bool = False


@dataclass
class FlowResult:
    """Actual conversion outcome, recorded verbatim in the report."""

    flow: str
    gif: str
    bytes: int
    duration_s: float
    fps: int
    width: int
    speed: float
    budget_bytes: int
    attempts: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)


def parse_overrides(pairs: Sequence[str], kind: str) -> dict[str, Any]:
    """Parse repeated ``flow=value`` CLI options."""
    result: dict[str, Any] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name or not raw:
            raise SystemExit(f"--{kind} expects flow=value, got: {pair}")
        if kind == "trim":
            match = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", raw)
            if match is None:
                raise SystemExit(f"--trim expects flow=START:END seconds, got: {pair}")
            start, end = float(match.group(1)), float(match.group(2))
            if end <= start:
                raise SystemExit(f"--trim end must exceed start, got: {pair}")
            result[name] = (start, end)
        elif kind == "speed":
            try:
                factor = float(raw)
            except ValueError:
                factor = 0.0
            if not factor > 0.0:
                raise SystemExit(f"--speed expects a positive factor, got: {pair}")
            result[name] = factor
        else:
            try:
                budget = float(raw)
            except ValueError:
                budget = 0.0
            if not budget > 0.0:
                raise SystemExit(f"--budget expects MB, got: {pair}")
            result[name] = int(budget * 1_000_000)
    return result


def discover_flows(input_dir: Path) -> list[Flow]:
    """Real recordings only: manifest segments, or per-flow video.webm files."""
    manifest_path = input_dir / "recordings.json"
    if manifest_path.is_file():
        manifest: dict[str, Any] = json.loads(manifest_path.read_text())
        flows: list[Flow] = []
        for name, entry in manifest.items():
            speed = float(entry.get("speed", 1.0))
            if not speed > 0.0:
                raise SystemExit(f"Manifest speed must be positive for {name}")
            segments = []
            for raw in entry["segments"]:
                path = Path(raw["path"])
                if not path.is_absolute():
                    path = input_dir / path
                if not path.is_file():
                    raise SystemExit(f"Manifest segment is missing: {path}")
                segments.append(
                    Segment(
                        path=path,
                        start=float(raw.get("start", 0.0)),
                        duration=(
                            None
                            if raw.get("duration") is None
                            else float(raw["duration"])
                        ),
                    )
                )
            if not segments:
                raise SystemExit(f"Manifest flow has no segments: {name}")
            flows.append(Flow(name=name, segments=segments, speed=speed, black_prelude=entry.get("blackPrelude") is True))
        return flows
    flows = []
    for video in sorted(input_dir.glob("*/video.webm")):
        flows.append(
            Flow(
                name=video.parent.name,
                segments=[Segment(path=video, start=0.0, duration=None)],
            )
        )
    return flows


def find_missing(input_dir: Path, flows: Sequence[Flow]) -> list[str]:
    """Flow directories that produced neither a manifest entry nor a video."""
    known = {flow.name for flow in flows}
    missing = []
    for entry in sorted(input_dir.iterdir()):
        if (
            entry.is_dir()
            and entry.name not in known
            and not (entry / "video.webm").is_file()
        ):
            missing.append(entry.name)
    return missing


def require_gif_support(ffmpeg: str, runner: Runner) -> None:
    """The mandatory binary must actually support the GIF muxer."""
    probe = runner(
        [ffmpeg, "-hide_banner", "-nostdin", "-muxers"],
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_S,
    )
    if probe.returncode != 0:
        raise SystemExit(f"ffmpeg is not runnable ({ffmpeg}): {probe.stderr[-400:]}")
    for line in probe.stdout.splitlines():
        columns = line.split()
        if len(columns) >= 2 and columns[0] == "E" and columns[1] == "gif":
            return
    raise SystemExit(f"ffmpeg lacks GIF muxer support: {ffmpeg}")


def build_filter_complex(
    segment_count: int,
    speed: float,
    fps: int,
    width: int,
    trim: tuple[float, float] | None,
) -> tuple[str, str]:
    """Two-pass filter graphs sharing one real-pixel base chain.

    Segments are trimmed, speed-adjusted, scaled and fps-normalised, then
    concatenated. Pass one renders the base chain into a palette; pass two
    reuses the base chain with paletteuse.
    """
    parts = []
    for index in range(segment_count):
        parts.append(
            f"[{index}:v]setpts=(PTS-STARTPTS)/{speed:g},"
            f"scale={width}:-2:flags=lanczos,fps={fps}[v{index}]"
        )
    base = ";".join(parts)
    if segment_count > 1:
        base += (
            ";"
            + "".join(f"[v{i}]" for i in range(segment_count))
            + f"concat=n={segment_count}:v=1:a=0[cat]"
        )
        source = "[cat]"
    else:
        source = "[v0]"
    if trim is not None:
        base += (
            f";{source}trim=start={trim[0]:.3f}:end={trim[1]:.3f},"
            "setpts=PTS-STARTPTS[base]"
        )
    else:
        base += f";{source}null[base]"
    pass_one = base + f";[base]palettegen=max_colors={MAX_COLORS}[p]"
    pass_two = (
        base + f";[base][{segment_count}:v]paletteuse=dither=none:diff_mode=rectangle[out]"
    )
    return pass_one, pass_two


def build_inputs(flow: Flow) -> list[str]:
    """ffmpeg input options carrying each real segment's trim."""
    inputs: list[str] = []
    for segment in flow.segments:
        if segment.start > 0.0:
            inputs += ["-ss", f"{segment.start:.3f}"]
        if segment.duration is not None:
            inputs += ["-t", f"{segment.duration:.3f}"]
        inputs += ["-threads", "1", "-i", str(segment.path)]
    return inputs


def align_prelude(ffmpeg: str, flow: Flow, runner: Runner) -> None:
    """Align wall-clock marks with the first actual black calibration frame."""
    if not flow.black_prelude:
        return  # Existing recordings explicitly retain their original timebase.
    result = runner(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(flow.segments[0].path),
         "-t", "5", "-vf", "blackdetect=d=0.1:pix_th=0.05:pic_th=0.99",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False, timeout=RUN_TIMEOUT_S,
    )
    match = re.search(r"black_start:(\d+(?:\.\d+)?)", result.stderr)
    if result.returncode != 0 or match is None:
        raise RuntimeError(f"{flow.name}: recording calibration frame is missing")
    offset = float(match.group(1))
    flow.segments = [replace(segment, start=segment.start + offset) for segment in flow.segments]


def decode_duration(runner: Runner, ffmpeg: str, media: Path) -> float:
    """Actual seconds, from ffmpeg's own decoding loop."""
    result = runner(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(media), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {media.name}: {result.stderr[-400:]}")
    stamps = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not stamps:
        raise RuntimeError(f"ffmpeg reported no duration for {media.name}")
    hours, minutes, seconds = map(float, stamps[-1])
    return hours * 3600 + minutes * 60 + seconds



class BudgetExceeded(RuntimeError):
    """The hard budget survived every real-pixel retry."""

    def __init__(self, flow: str, budget: int, trace: list[dict[str, Any]]) -> None:
        smallest = trace[-1] if trace else {}
        super().__init__(
            f"{flow}: smallest GIF {smallest.get('bytes')} bytes at "
            f"{smallest.get('fps')}fps/{smallest.get('width')}px still exceeds "
            f"the hard budget {budget} bytes; shorten with --speed or --trim"
        )
        self.flow = flow


def convert_flow(
    ffmpeg: str,
    flow: Flow,
    target: Path,
    budget: int,
    fps: int,
    width: int,
    trim: tuple[float, float] | None,
    runner: Runner,
    palette: Path,
) -> FlowResult:
    """Encode with a two-pass palette at the requested dimensions and frame rate."""
    target.parent.mkdir(parents=True, exist_ok=True)
    inputs = build_inputs(flow)
    trace: list[dict[str, Any]] = []
    for attempt_fps, attempt_width in [(fps, width)]:
        pass_one, pass_two = build_filter_complex(
            len(flow.segments), flow.speed, attempt_fps, attempt_width, trim
        )
        encoded = runner(
            [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-filter_complex_threads", "1", "-y"]
            + inputs
            + ["-filter_complex", pass_one, "-map", "[p]", "-frames:v", "1", str(palette)],
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
        if encoded.returncode != 0:
            raise RuntimeError(
                f"ffmpeg palette pass failed for {flow.name}: {encoded.stderr[-800:]}"
            )
        encoded = runner(
            [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-filter_complex_threads", "1", "-y"]
            + inputs
            + [
                "-i",
                str(palette),
                "-filter_complex",
                pass_two,
                "-map",
                "[out]",
                "-an",
                str(target),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
        if encoded.returncode != 0:
            raise RuntimeError(
                f"ffmpeg gif pass failed for {flow.name}: {encoded.stderr[-800:]}"
            )
        if not target.exists():
            raise RuntimeError(
                f"ffmpeg produced no output for {flow.name} "
                f"({attempt_fps}fps/{attempt_width}px)"
            )
        payload = target.read_bytes()
        trace.append(
            {
                "fps": attempt_fps,
                "width": attempt_width,
                "bytes": len(payload),
                "within_budget": len(payload) <= budget,
            }
        )
        if len(payload) <= budget:
            duration = round(decode_duration(runner, ffmpeg, target), 3)
            if not 10 <= duration <= 30:
                raise RuntimeError(f"{flow.name}: {duration}s is outside the 10–30s story length; adjust --speed or --trim")
            return FlowResult(
                flow=flow.name,
                gif=str(target),
                bytes=len(payload),
                duration_s=duration,
                fps=attempt_fps,
                width=attempt_width,
                speed=flow.speed,
                budget_bytes=budget,
                attempts=trace,
                segments=[
                    {
                        "path": str(segment.path),
                        "start": segment.start,
                        "duration": segment.duration,
                        "speed": flow.speed,
                    }
                    for segment in flow.segments
                ],
            )
    raise BudgetExceeded(flow.name, budget, trace)


def main(argv: Sequence[str] | None = None, runner: Runner = subprocess.run) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Playwright output tree of flow recordings",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="docs/media directory for the GIFs"
    )
    parser.add_argument(
        "--ffmpeg",
        default=os.environ.get("FFMPEG", ""),
        help="ffmpeg binary (or set FFMPEG); must support GIF",
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument(
        "--speed",
        action="append",
        default=[],
        help="flow=FACTOR, applied to that flow's timeline",
    )
    parser.add_argument(
        "--trim",
        action="append",
        default=[],
        help="flow=START:END seconds of the composed timeline",
    )
    parser.add_argument(
        "--budget",
        action="append",
        default=[],
        help="flow=MB hard size budget override",
    )
    args = parser.parse_args(argv)
    if not args.ffmpeg:
        raise SystemExit(
            "ffmpeg is mandatory: pass --ffmpeg PATH or set FFMPEG "
            f"(got: {args.ffmpeg!r})"
        )
    if runner is subprocess.run:
        # Real conversion: the binary must exist and genuinely support GIF.
        if not Path(args.ffmpeg).is_file():
            raise SystemExit(f"ffmpeg executable is missing: {args.ffmpeg}")
        require_gif_support(args.ffmpeg, runner)
    if not 12 <= args.fps <= 15 or args.width != 960:
        raise SystemExit("Documentation GIFs require width 960 and fps 12–15")
    speeds = parse_overrides(args.speed, "speed")
    trims = parse_overrides(args.trim, "trim")
    budgets = parse_overrides(args.budget, "budget")
    if not args.input.is_dir():
        raise SystemExit(f"Input directory is missing: {args.input}")
    flows = discover_flows(args.input)
    expected = {"00-overview", "01-configure-plugins", "02-predicates", "03-rules-events", "04-aerograph", "05-agents", "06-simulate", "07-visualize"}
    if {flow.name for flow in flows} != expected:
        raise SystemExit(f"Expected all eight product flows; missing: {sorted(expected - {flow.name for flow in flows})}")
    missing = find_missing(args.input, flows)
    if not flows:
        detail = f" ({', '.join(missing)} produced no video)" if missing else ""
        raise SystemExit(f"No flow recordings under {args.input}{detail}")
    results: list[FlowResult] = []
    failures: list[dict[str, str]] = []
    palette = args.output / ".palette.png"
    for flow in flows:
        flow.speed = speeds.get(flow.name, flow.speed)
        trim: tuple[float, float] | None = trims.get(flow.name)
        budget = budgets.get(
            flow.name,
            HERO_BUDGET_BYTES if HERO_PATTERN in flow.name else BUDGET_BYTES,
        )
        target = args.output / f"{flow.name}.gif"
        try:
            align_prelude(args.ffmpeg, flow, runner)
            result = convert_flow(
                args.ffmpeg,
                flow,
                target,
                budget,
                args.fps,
                args.width,
                trim,
                runner,
                palette,
            )
        except (RuntimeError, OSError) as error:
            failures.append({"flow": flow.name, "reason": str(error)})
            print(f"FAILED {flow.name}: {error}", flush=True)
            continue
        results.append(result)
        print(
            f"{flow.name}: {result.bytes} bytes, {result.duration_s}s, "
            f"{result.fps}fps/{result.width}px "
            f"({len(result.segments)} real segment(s))",
            flush=True,
        )
    palette.unlink(missing_ok=True)
    for name in missing:
        failures.append(
            {
                "flow": name,
                "reason": "no video.webm and no recordings.json manifest entry",
            }
        )
        print(f"FAILED {name}: recording artifact is missing", flush=True)
    report = {
        "ffmpeg": args.ffmpeg,
        "default_fps": args.fps,
        "default_width": args.width,
        "budget_bytes": {
            "default": BUDGET_BYTES,
            "hero": HERO_BUDGET_BYTES,
            "overrides": budgets,
        },
        "items": [vars(result) for result in results],
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Report: {args.report} ({len(results)} ok, {len(failures)} failed)", flush=True
    )
    if failures:
        raise SystemExit(
            "Failed recordings: " + ", ".join(row["flow"] for row in failures)
        )


if __name__ == "__main__":
    main()
