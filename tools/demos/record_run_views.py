"""Record exact committed cuts as derived replay videos outside the source run."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import asdict
from fractions import Fraction
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from aerokernel import Cut, EntityRef, Instant, Stamp
from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.values import canonical_json, thaw

from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.observations.png import validate_png
from aeroagentsim.observations.renderer import BrowserRenderer, Renderer
from aeroagentsim.runs import RunReadError, Snapshot, open_run
from aeroagentsim.scenario.loader import contract, text
from aeroagentsim.services.storage import RunStorage


def _has_ack(directory: Path) -> bool:
    ack = directory / "committed-prefix.json"
    # A broken symlink is an invalid published ACK, not a legacy input.
    return ack.exists() or ack.is_symlink()


def _metadata(directory: Path) -> dict[str, Any]:
    # Reader v1 has no service status/final cursor or full-manifest capability.
    return json.loads((directory / "manifest.json").read_text())


def _final_cursor(metadata: dict[str, Any]) -> int:
    cursor = metadata["final_cursor"]
    if type(cursor) is not int or cursor < 1:
        raise ValueError("run has an invalid manifest final cursor")
    return cursor


def source_evidence(snapshot: Snapshot) -> dict[str, Any]:
    """Detached provenance for derived exports, not a run-success claim."""
    return {
        "reader_api_version": snapshot.api_version,
        "cut_index": snapshot.cut_index,
        "cut_bytes": snapshot.cut_bytes,
        "header": asdict(snapshot.header),
        "redaction": "none",
    }


def _cuts(snapshot: Snapshot) -> list[Cut]:
    result = []
    for row in snapshot.iter_records(start=1):
        at = decode_record(row["instant"])
        if not isinstance(at, Instant):
            raise TypeError("run has an invalid journal instant")
        result.append(Cut(row["index"], at))
    if not result:
        raise ValueError("run has no committed cuts to record")
    return result


def _indexed_cuts(directory: Path) -> list[Cut]:
    """Retain main's indexed admission for runs predating ACK publication."""
    storage = RunStorage(directory)
    result: list[Cut] = []
    cursor = 1
    while batch := storage.records(cursor, 4096):
        for offset, row in enumerate(batch):
            if row["index"] != cursor + offset:
                raise ValueError(
                    "recording journal has a gap or unordered indexed prefix"
                )
            at = decode_record(expand_record(row)["instant"])
            if not isinstance(at, Instant):
                raise TypeError("run has an invalid journal instant")
            result.append(Cut(row["index"], at))
        cursor = batch[-1]["index"] + 1
    if not result:
        raise ValueError("run has no committed cuts to record")
    if cursor != storage.metadata()["final_cursor"]:
        raise ValueError("run index does not cover the manifest final cursor")
    return result


def cuts(directory: Path) -> list[Cut]:
    if not _has_ack(directory):
        return _indexed_cuts(directory)
    with open_run(directory) as run:
        snapshot = run.snapshot()
        if snapshot.cut_index + 1 != _final_cursor(_metadata(directory)):
            raise ValueError("run ACK does not cover the manifest final cursor")
        return _cuts(snapshot)


def public_results(service_url: str, run_id: str) -> dict[str, Any]:
    """Read the existing service finality DTO, never its local cache layout."""
    url = f"{service_url.rstrip('/')}/v1/runs/{quote(run_id, safe='')}?include_kpis=false"
    with urlopen(url, timeout=20.0) as response:
        result = json.load(response)
    if result["id"] != run_id:
        raise ValueError("finality response belongs to another service run")
    return {
        key: result[key]
        for key in ("results_final", "results_cursor", "final_commit_index")
    }


def recording_source(
    directory: Path, *, timeout_s: float = 20.0,
    results_provider: Callable[[], dict[str, Any]] | None = None,
) -> tuple[list[Cut], dict[str, Any], dict[str, Any]]:
    """Use legacy indexed evidence, or require matching public finality and ACK."""
    if not 0 <= timeout_s < float("inf"):
        raise ValueError("results timeout must be finite and nonnegative")
    if not _has_ack(directory):
        metadata = _metadata(directory)
        if metadata["status"] not in {
            "completed", "stopped", "faulted", "interrupted",
        }:
            raise ValueError("recorder requires a finished run")
        selected = _indexed_cuts(directory)
        # This historical path proves index coverage, not reader ACK or service
        # projection finality. Never invent either claim from a terminal status.
        return selected, metadata, {
            "reader_api_version": None,
            "acknowledged": False,
            "cut_index": selected[-1].index,
            "redaction": "none",
        }
    # Reader v1 has no offline projection-finality capability. Require the
    # service's public DTO/provider instead of interpreting private cache files.
    if results_provider is None:
        raise ValueError(
            "reader v1 lacks offline projection finality; supply a public results provider"
        )
    deadline, wake = monotonic() + timeout_s, Event()
    with open_run(directory) as run:
        while True:
            snapshot = run.snapshot()
            metadata = _metadata(directory)
            if metadata["status"] not in {
                "completed", "stopped", "faulted", "interrupted",
            }:
                raise ValueError("recorder requires a finished run")
            cursor = _final_cursor(metadata)
            state = results_provider()
            if (
                state["results_final"] is True
                and type(state["results_cursor"]) is int
                and state["results_cursor"] == cursor == snapshot.cut_index + 1
                and state["final_commit_index"] == snapshot.cut_index
            ):
                return _cuts(snapshot), metadata, {
                    **source_evidence(snapshot),
                    "projection": state,
                }
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "final capture requires results_final and matching results, "
                    "manifest and acknowledged cursors"
                )
            # Poll backoff is not evidence of completion; only the state above is.
            wake.wait(min(0.05, remaining))


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
    results_timeout_s: float = 20.0,
    results_provider: Callable[[], dict[str, Any]] | None = None,
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
    source, metadata, evidence = recording_source(
        run, timeout_s=results_timeout_s, results_provider=results_provider,
    )
    selected = schedule(source, fps, speed)
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "contract": "aeroagentsim.replay-recordings/v1",
        "source_run": metadata,
        "source_evidence": evidence,
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
        # Reader header identities/engine versions are tuples, not wire arrays.
        temporary.write_bytes(canonical_json(thaw(manifest)))
        temporary.replace(output / "manifest.json")

    save()
    for view, row in zip(views, rows):
        try:
            if ffmpeg is None:
                raise RuntimeError("ffmpeg unavailable; select an installed executable")
            actor = EntityRef.from_data(view["actor"])
            kernel_run_id = (
                metadata["kernel_run_id"] if evidence.get("acknowledged") is False
                else evidence["header"]["kernel_run_id"]
            )
            if actor.run_id != kernel_run_id:
                raise ValueError("camera actor belongs to another kernel run")
            filename = (
                "".join(c if c.isalnum() or c in "-._" else "-" for c in view["id"])
                + ".mp4"
            )
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
    parser.add_argument(
        "--service-url",
        help="Service API base URL for finality (defaults to --viewer-url's origin)",
    )
    parser.add_argument("--node-modules", required=True, type=Path)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--speed", default="2")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--results-timeout-s", type=float, default=20.0)
    parser.add_argument(
        "--encoder",
        default="libx264",
        help="Explicit ffmpeg encoder; never switches automatically",
    )
    args = parser.parse_args(argv)
    try:
        results_provider: Callable[[], dict[str, Any]] | None = None
        if _has_ack(args.run):
            service_url = args.service_url
            if service_url is None:
                viewer = urlsplit(args.viewer_url)
                if viewer.scheme not in {"http", "https"} or not viewer.netloc:
                    raise ValueError("viewer URL must have an HTTP(S) origin")
                service_url = viewer._replace(path="", query="", fragment="").geturl()
            with open_run(args.run) as source:
                service_run_id = source.header.service_run_id
            if service_run_id is None:
                raise ValueError("recording requires a recorded service run ID")
            results_provider = partial(public_results, service_url, service_run_id)
        else:
            service_run_id = _metadata(args.run)["id"]
        with BrowserRenderer(
            args.viewer_url,
            node_modules=args.node_modules,
            browser_executable=args.browser_executable,
            timeout_s=20.0,
            service_run_id=service_run_id,
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
                results_timeout_s=args.results_timeout_s,
                results_provider=results_provider,
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
        RunReadError,
        RuntimeError,
    ) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
