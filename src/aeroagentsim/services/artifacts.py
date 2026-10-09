"""Additive run artifact routes; uploads enter the existing worker input path."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from collections.abc import Callable
from dataclasses import replace
from itertools import islice
from pathlib import Path
from typing import Any

import yaml
from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.errors import KernelError
from aerokernel.journal import iter_records
from aerokernel.values import canonical_json
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from aeroagentsim.observations.artifacts import ArtifactStore
from aeroagentsim.observations.contracts import (
    CaptureRequest,
    browser_decode,
    browser_encode,
    content_digest,
    digest,
)
from aeroagentsim.platform.ingress import submission
from aeroagentsim.scenario.loader import Scenario, contract, integer
from aeroagentsim.services.projector import project
from aeroagentsim.services.subjects import projection_context

Directory = Callable[[str], Path]
WorkerInput = Callable[[str, str, Any], dict[str, Any]]


def mount_artifacts(
    app: FastAPI, directory: Directory, worker_input: WorkerInput
) -> None:
    """Mount before the existing generic POST control route so uploads can match."""

    @app.get("/v1/runs/{run_id}/capture-requests/{request_id}/scene")
    def capture_scene(run_id: str, request_id: str) -> dict[str, Any]:
        path = directory(run_id)
        try:
            capture = ArtifactStore(path).request(request_id)
            subjects = projection_context(path, 1)
            commits = []
            # A renderer runs inside an advance, before worker index refresh.
            # The owner has already validated/registered this committed cut.
            # Read through exactly that issued prefix without mutating indexes.
            seen = -1
            for expected, record in enumerate(
                islice(
                    iter_records(path / "journal.jsonl"), capture.source_cut.index + 1
                )
            ):
                seen = expected
                if record["index"] != expected:
                    raise ValueError("capture journal prefix has an index gap")
                if expected == capture.source_cut.index:
                    decoded = expand_record(record)
                    if decode_record(decoded["instant"]) != capture.source_cut.instant:
                        raise ValueError(
                            "capture source cut differs from the journal instant"
                        )
                if expected > 0:
                    commits.append(project(record, subjects=subjects))
            if seen != capture.source_cut.index:
                raise ValueError("requested committed prefix is not readable yet")
            return {
                "contract": "aeroagentsim.capture-scene/v1",
                "service_run_id": run_id,
                "kernel_run_id": capture.run_id,
                "source_cut": browser_encode(capture.to_data()["source_cut"]),
                "commits": commits,
            }
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown capture request or journal") from exc
        except (ValueError, TypeError, KeyError, OSError, KernelError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/runs/{run_id}/artifacts")
    def artifacts(run_id: str) -> list[dict[str, Any]]:
        try:
            return [
                browser_encode(record)
                for record in ArtifactStore(directory(run_id)).list()
            ]
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/runs/{run_id}/artifacts/{content}")
    def artifact(run_id: str, content: str) -> dict[str, Any]:
        try:
            store = ArtifactStore(directory(run_id))
            records = store.by_digest(content)
            return {
                "digest": content,
                "byte_count": records[0]["byte_count"],
                "media_type": "image/png",
                "records": browser_encode(records),
                "download": f"/v1/runs/{run_id}/artifacts/{content}/download",
            }
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown artifact") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/runs/{run_id}/artifacts/{content}/download")
    def download(run_id: str, content: str) -> Response:
        try:
            store = ArtifactStore(directory(run_id))
            store.by_digest(content)
            data = store.read(content)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown artifact") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return Response(
            data,
            media_type="image/png",
            headers={
                "ETag": '"sha256-' + content + '"',
                "X-Content-SHA256": content,
                "Content-Length": str(len(data)),
                "Content-Disposition": f'attachment; filename="{content}.png"',
            },
        )

    @app.post("/v1/runs/{run_id}/capture-artifacts", status_code=202)
    async def upload(run_id: str, request: Request) -> dict[str, Any]:
        path = directory(run_id)
        if (
            await asyncio.to_thread(
                lambda: json.loads((path / "manifest.json").read_bytes())
            )
        )["status"] not in {"running", "paused", "created"}:
            raise HTTPException(
                409, "Capture upload requires an active run; replay is read-only"
            )
        try:
            # Bound the transport before parsing a large base64 payload.
            if (
                "content-length" in request.headers
                and int(request.headers["content-length"]) > 90 * 1024 * 1024
            ):
                raise ValueError("capture upload exceeds transport limit")
            raw = await request.body()
            if len(raw) > 90 * 1024 * 1024:
                raise ValueError("capture upload exceeds transport limit")
            body = contract(
                browser_decode(json.loads(raw)),
                "capture.upload",
                {
                    "request",
                    "png_base64",
                    "digest",
                    "byte_count",
                    "at_ns",
                    "source_stamp",
                },
            )
            capture = CaptureRequest.from_data(body["request"])
            store = ArtifactStore(path)
            if store.request(capture.request_id).to_data() != capture.to_data():
                raise ValueError(
                    "upload actor/cut/camera/assets differ from the outstanding request"
                )
            engines = json.loads((path / "scenario.json").read_bytes())["engines"]
            owners = [
                (name, engine["config"])
                for name, engine in engines.items()
                if engine["plugin"] == "capture"
                and "storage_result_schema" in engine["config"]
            ]
            if len(owners) != 1:
                raise ValueError("capture upload requires one configured capture owner")
            owner, config = owners[0]
            png = base64.b64decode(body["png_base64"], validate=True)
            content = digest(body["digest"])

            if content_digest(png) != content or len(png) != integer(
                body["byte_count"], "capture.byte_count", 1
            ):
                raise ValueError("upload content hash/byte count mismatch")
            ingress = {
                "engine": owner,
                "schema": config["storage_result_schema"],
                "target": owner,
                "stream_id": config["ingress_stream_id"],
                "at_ns": body["at_ns"],
                "source_stamp": body["source_stamp"],
                "idempotency_key": "capture/" + capture.request_id + "/" + content,
                "payload": {"request_id": capture.request_id, "digest": content},
            }
            submission(ingress)  # Validate shape before persisting new bytes.
            record = await asyncio.to_thread(
                store.put, capture, png, renderer_mode="browser"
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                409, "No outstanding capture request/configuration"
            ) from exc
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            admission = await asyncio.to_thread(
                worker_input, run_id, "ingress", ingress
            )
        except HTTPException as exc:
            await asyncio.to_thread(
                store.delivery,
                capture.request_id,
                {"status": "admission_failed", "detail": exc.detail, "digest": content},
            )
            raise
        await asyncio.to_thread(store.delivery, capture.request_id, admission)
        return {
            "contract": "aeroagentsim.artifact-upload/v1",
            "storage": browser_encode(record),
            "admission": admission,
        }


def scope_capture_storage(scenario: Scenario, directory: Path) -> Scenario:
    """Pin the service-assigned run path before validation and journal creation.

    Only the output location changes; registry, identities, bindings and clocks
    remain the already compiled immutable objects. Direct RunSession callers
    supply their chosen run_directory explicitly.
    """
    if not any(item["plugin"] == "capture" for item in scenario.engines.values()):
        return scenario
    document = copy.deepcopy(scenario.document)
    for item in document["engines"].values():
        if item["plugin"] == "capture":
            item["config"]["run_directory"] = str(directory.resolve())
            if item["config"].get("renderer", {}).get("mode") == "browser":
                item["config"]["renderer"]["service_run_id"] = directory.name
    return replace(
        scenario,
        document=document,
        engines=document["engines"],
        source=yaml.safe_dump(document, sort_keys=True),
        digest=content_digest(canonical_json(document)),
    )
