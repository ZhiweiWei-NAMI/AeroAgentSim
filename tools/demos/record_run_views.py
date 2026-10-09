"""Record exact committed cuts as derived replay videos outside the source run."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from bisect import bisect_right
from fractions import Fraction
from pathlib import Path
from typing import Any

from aerokernel import Cut, EntityRef, Instant, Stamp
from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.values import canonical_json

from aeroagentsim.observations.contracts import CaptureRequest, content_digest
from aeroagentsim.observations.png import validate_png
from aeroagentsim.observations.renderer import BrowserRenderer, Renderer
from aeroagentsim.scenario.loader import contract, text
from aeroagentsim.services.storage import RunStorage


def cuts(directory: Path) -> list[Cut]:
    storage = RunStorage(directory)
    result: list[Cut] = []
    cursor = 1
    while batch := storage.records(cursor, 4096):
        for offset, row in enumerate(batch):
            if row["index"] != cursor + offset:
                raise ValueError(
                    "recording journal has a gap or unordered indexed prefix"
                )
            decoded = expand_record(row)
            at = decode_record(decoded["instant"])
            if not isinstance(at, Instant):
                raise TypeError("run has an invalid journal instant")
            result.append(Cut(row["index"], at))
        cursor = batch[-1]["index"] + 1
    if not result:
        raise ValueError("run has no committed cuts to record")
    if result[-1].index + 1 != storage.metadata()["final_cursor"]:
        raise ValueError("run index does not cover the manifest final cursor")
    return result


def schedule(source: list[Cut], fps: int, speed: Fraction) -> list[Cut]:
    if (
        type(fps) is not int
        or fps <= 0
        or not isinstance(speed, Fraction)
        or speed <= 0
    ):
        raise ValueError("fps and speed must be positive")
    first, end = source[0].instant.ns, source[-1].instant.ns
    step = speed * 1_000_000_000 / fps
    samples = [
        first + int(i * step) for i in range(int(Fraction(end - first, 1) / step) + 1)
    ]
    if samples[-1] != end:
        samples.append(end)
    times = [cut.instant.ns for cut in source]
    return [source[bisect_right(times, at) - 1] for at in samples]


def record_views(
    run: Path,
    output: Path,
    views: list[dict[str, Any]],
    *,
    renderer: Renderer,
    fps: int,
    speed: Fraction,
    ffmpeg: str | None,
    encoder: str = "libx264",
) -> dict[str, Any]:
    run, output = run.resolve(), output.resolve()
    if output == run or output.is_relative_to(run):
        raise ValueError("derived video output must be outside the immutable run")
    if not isinstance(views, list) or not views:
        raise ValueError("select at least one explicitly authored camera view")
    ids = []
    for view in views:
        spec = contract(
            view,
            "recording.view",
            {"id", "actor", "camera", "asset_digest", "width", "height"},
        )
        ids.append(text(spec["id"], "recording.view.id"))
    if len(set(ids)) != len(ids):
        raise ValueError("recording view IDs must be unique")
    storage = RunStorage(run)
    metadata = storage.metadata()
    if metadata["status"] not in {"completed", "stopped", "faulted", "interrupted"}:
        raise ValueError("recorder requires a finished run")
    selected = schedule(cuts(run), fps, speed)
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "contract": "aeroagentsim.replay-recordings/v1",
        "source_run": metadata,
        "journal_sha256": hashlib.sha256(
            (run / "journal.jsonl").read_bytes()
        ).hexdigest(),
        "fps": fps,
        "speed": str(speed),
        "terminal_frame_hold_s": str(Fraction(1, fps)),
        "views": [],
    }
    rows: list[dict[str, Any]] = manifest["views"]
    for view in views:
        rows.append(
            {
                **view,
                "camera_digest": content_digest(canonical_json(view["camera"])),
                "status": "pending",
                "source_cuts": [
                    {
                        "index": c.index,
                        "instant": [str(c.instant.ns), c.instant.microstep],
                    }
                    for c in selected
                ],
            }
        )

    def save() -> None:
        temporary = output / "manifest.json.writing"
        temporary.write_bytes(canonical_json(manifest))
        temporary.replace(output / "manifest.json")

    save()
    for view, row in zip(views, rows):
        try:
            if ffmpeg is None:
                raise RuntimeError("ffmpeg unavailable; select an installed executable")
            actor = EntityRef.from_data(view["actor"])
            if actor.run_id != metadata["kernel_run_id"]:
                raise ValueError("camera actor belongs to another kernel run")
            filename = content_digest(view["id"].encode()) + ".mp4"
            target = output / filename
            with tempfile.TemporaryDirectory(
                dir=output, prefix=".frames-"
            ) as temporary:
                frame_dir = Path(temporary)
                for index, cut in enumerate(selected):
                    request = CaptureRequest(
                        "record/" + view["id"] + "/" + str(index),
                        actor.run_id,
                        actor,
                        cut,
                        view["camera"],
                        view["asset_digest"],
                        Stamp("canonical", cut.instant.ns, 1, "canonical"),
                        view["width"],
                        view["height"],
                        20.0,
                    )
                    label = f"source={cut.instant.ns}ns/{cut.instant.microstep} cut={cut.index} speed={speed}x"
                    frame = renderer.render(request, label=label)
                    validate_png(frame.png, expected=(request.width, request.height))
                    if frame.mode not in {"browser", "stub"}:
                        raise ValueError(
                            "renderer omitted its explicitly selected mode"
                        )
                    (frame_dir / f"{index:08d}.png").write_bytes(frame.png)
                    row["renderer_mode"] = frame.mode
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-v",
                        "error",
                        "-framerate",
                        str(fps),
                        "-i",
                        str(frame_dir / "%08d.png"),
                        "-c:v",
                        encoder,
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(target),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=300,
                )
            decoded = subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    str(target),
                    "-progress",
                    "pipe:1",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                check=True,
                timeout=300,
            )
            counts = [
                int(line.split("=", 1)[1])
                for line in decoded.stdout.decode().splitlines()
                if line.startswith("frame=")
            ]
            if not counts or counts[-1] != len(selected):
                raise ValueError(
                    "encoded recording does not decode to the rendered frame count"
                )
            data = target.read_bytes()
            row.update(
                status="completed",
                file=filename,
                digest=content_digest(data),
                byte_count=len(data),
                frame_count=counts[-1],
                encoder=encoder,
                output_duration_s=str(Fraction(counts[-1], fps)),
                source_interval_ns=[
                    str(selected[0].instant.ns),
                    str(selected[-1].instant.ns),
                ],
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            reason = str(exc)
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                reason += ": " + exc.stderr.decode(errors="replace")
            row.update(status="failed", reason=reason)
        save()
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cameras", required=True, type=Path)
    parser.add_argument("--viewer-url", required=True)
    parser.add_argument("--node-modules", required=True, type=Path)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--speed", default="2")
    parser.add_argument("--ffmpeg")
    parser.add_argument(
        "--encoder",
        default="libx264",
        help="Explicit ffmpeg encoder; never switches automatically",
    )
    args = parser.parse_args(argv)
    try:
        with BrowserRenderer(
            args.viewer_url,
            node_modules=args.node_modules,
            browser_executable=args.browser_executable,
            timeout_s=20.0,
            service_run_id=RunStorage(args.run).metadata()["id"],
        ) as renderer:
            manifest = record_views(
                args.run,
                args.output,
                json.loads(args.cameras.read_bytes()),
                renderer=renderer,
                fps=args.fps,
                speed=Fraction(args.speed),
                ffmpeg=args.ffmpeg
                if args.ffmpeg is not None
                else shutil.which("ffmpeg"),
                encoder=args.encoder,
            )
        failed = [row for row in manifest["views"] if row["status"] == "failed"]
        for row in failed:
            print(row["id"] + ": " + row["reason"])
        print(args.output / "manifest.json")
        return 1 if failed else 0
    except (
        ValueError,
        TypeError,
        ZeroDivisionError,
        KeyError,
        OSError,
        RuntimeError,
    ) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
