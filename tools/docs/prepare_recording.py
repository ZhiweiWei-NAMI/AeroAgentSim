"""Create real demo inputs and a completed city-capture replay before filming."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

EVENTS = ("traffic.incident.detected", "traffic.award.committed", "traffic.capture.accepted")


def request(base: str, path: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body, allow_nan=False).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{req.get_method()} {path}: HTTP {exc.code}: {exc.read().decode()}") from exc


def prepare(base: str, output: Path, replay_source: Path | None, timeout: float) -> None:
    workspace = request(base, "/v1/studio/workspaces", {"name": "Traffic accident · README"})
    wid = workspace["id"]
    workspace = request(
        base, f"/v1/studio/workspaces/{wid}/templates/traffic-accident",
        {"console": True, "capture_mode": "city"},
    )
    # The shipped recorded responses run through the actual LangGraph executor,
    # producing inspectable model/tool records without making a paid model call.
    fixture = json.loads(Path(workspace["scenario"]["engines"]["decisions"]["config"]["fixture_path"]).read_text())
    workspace = request(base, f"/v1/studio/workspaces/{wid}/decision-profile", {"provider": {
        "base_url": "http://127.0.0.1:8788/v1", "model": "traffic-accident-recorded", "api_key_env": "DOCS_UNUSED_KEY",
    }})
    scenario = workspace["scenario"]
    # Explicit scripted-provider accounting: no model inference is performed.
    responses: dict[str, list[dict[str, Any]]] = {}
    actor_keys = {identity.replace(".", "_"): identity for identity in scenario["engines"]["decisions"]["config"]["options"]["candidates"]}
    for fixture_key, values in fixture["responses"].items():
        node = actor_keys.get(fixture_key, fixture_key)
        responses[node] = []
        for value in values:
            message = {"role": "assistant", "content": json.dumps(value, allow_nan=False)}
            responses[node].append({"message": message, "raw": {"choices": [{"message": message}]}, "usage": {"completion_tokens": 0}})
    scenario["engines"]["decisions"]["config"]["provider"] = {
        "mode": "stub", "model": "traffic-accident-recorded", "responses": responses,
    }
    # The endpoint default is an absolute 1s deadline; the demo decision occurs later.
    scenario["engines"]["decisions"]["config"]["budget"]["sim_deadline_ns"] = scenario["run"]["until_ns"]
    workspace = request(base, f"/v1/studio/workspaces/{wid}", {"scenario": scenario})
    validation = request(base, f"/v1/studio/workspaces/{wid}/validate", {"scenario": workspace["scenario"]})
    if validation.get("valid") is not True:
        raise RuntimeError(f"Demo compiler rejected recording draft: {validation}")
    if replay_source is not None:
        metadata = json.loads((replay_source / "manifest.json").read_text())
        if metadata["status"] != "completed" or metadata["scenario"] != "traffic-accident":
            raise ValueError("Replay source must be a genuinely completed traffic-accident run.")
        target = Path(os.environ["AEROAGENTSIM_DOCS_ROOT"]) / "runs" / replay_source.name
        if target.exists():
            raise FileExistsError(target)
        shutil.copytree(replay_source, target)
        # Relocate the asset URL, retaining the recorded city identity and geometry.
        scene_path = target / "console-scene.json"
        scene = json.loads(scene_path.read_text())
        scene["city"]["url"] = base.rstrip("/") + "/v1/studio/demo-assets/scene.json"
        scene_path.write_text(json.dumps(scene))
        rid = metadata["id"]
    else:
        started = request(base, "/v1/runs", {"scenario": workspace["scenario"], "studio_workspace": wid})
        rid = started["id"]
        deadline = time.monotonic() + timeout
        try:
            identity_deadline = time.monotonic() + 30
            while True:
                try:
                    configuration = request(base, f"/v1/studio/runs/{rid}/configuration")
                    break
                except RuntimeError as exc:
                    # The service returns 201 before the worker commits its WAL header.
                    # Wait only for this specific startup condition; retain other errors.
                    if not any(text in str(exc) for text in ("journal.jsonl", "WAL header", "WAL lacks")) or time.monotonic() >= identity_deadline:
                        raise
                    time.sleep(0.25)
            incident = next(row for row in workspace["scenario"]["entities"] if row["type"] == "aas:TrafficIncident")
            identity = {
                "run_id": configuration["kernel_run_id"], "epoch": configuration["epoch"],
                "id": incident["id"], "generation": 0, "type_id": incident["type"],
            }
            # Real external input precedes its closed prefix; no timer or invented event.
            admitted = request(base, f"/v1/runs/{rid}/ingress", {
                "schema": "aas.runtime.inject_event", "target": "behaviour", "stream_id": "operator",
                "at_ns": 1, "source_stamp": {"clock_id": "canonical", "mapping_id": "canonical", "numerator": 1, "denominator": 1},
                "payload": {"injection_point": "accident", "payload": {"incident": {"$ref": identity}, "reason": "README operator demonstration"}},
            })
            if admitted.get("disposition") != "accepted":
                raise RuntimeError(f"Accident was not admitted: {admitted}")
            request(base, f"/v1/runs/{rid}/watermark", {
                "stream_id": "operator", "watermark_ns": workspace["scenario"]["run"]["until_ns"],
            })
            previous = ""
            while time.monotonic() < deadline:
                metadata = next(row for row in request(base, "/v1/runs") if row["id"] == rid)
                status = metadata["status"]
                if status != previous:
                    print(f"Recording replay {rid}: {status}", flush=True)
                    previous = status
                if status == "completed":
                    break
                if status in {"faulted", "stopped", "interrupted"}:
                    raise RuntimeError(f"Recording replay did not complete: {metadata}")
                time.sleep(1)
            else:
                raise TimeoutError(f"Real replay {rid} did not complete in {timeout}s")
        except BaseException:
            request(base, f"/v1/runs/{rid}/stop", {})
            raise
    header = request(base, f"/v1/runs/{rid}/header")
    if not header.get("scene", {}).get("city"):
        raise RuntimeError("Completed recording run has no recorded city binding.")
    artifacts = request(base, f"/v1/runs/{rid}/artifacts")
    marks: dict[str, dict[str, Any]] = {}
    failures: list[Any] = []
    finished_agents = 0
    cursor = 1
    while True:
        page = request(base, f"/v1/runs/{rid}/commits?from={cursor}&limit=1000")
        for commit in page["commits"]:
            for message in commit["messages"]:
                if message["schemaId"] == "aas.langgraph.record":
                    if message["payload"]["phase"] == "failure":
                        failures.append(message["payload"])
                    if message["payload"]["phase"] == "finished":
                        finished_agents += 1
                if message["schemaId"] == "traffic.capture.failed":
                    failures.append(message["payload"])
                if message["schemaId"] in EVENTS and message["schemaId"] not in marks:
                    marks[message["schemaId"]] = {"cut": commit["commitIndex"], "ns": message["at"]["ns"]}
        cursor = page["next"]
        if not page["commits"]:
            break
    if failures or finished_agents < 2 or not artifacts or any(row["renderer_mode"] != "browser" for row in artifacts):
        raise RuntimeError(f"Replay requires successful LangGraph decisions and real browser photos; finished={finished_agents}, photos={len(artifacts)}, failures={failures[:3]}")
    if any(row["request"]["camera"].get("preset") != "actor-nadir" or row["request"]["width"] != 1024 for row in artifacts):
        raise RuntimeError("Recording photo must use the console city camera, not the primitive test renderer")
    if set(marks) != set(EVENTS):
        raise RuntimeError(f"Replay lacks required detection/award/capture records: {marks}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "baseURL": base, "workspaceId": wid, "runId": rid,
        "events": marks, "finalCut": cursor - 1, "artifacts": len(artifacts),
    }, indent=2) + "\n")
    print(f"Ready: completed run, {cursor - 1} commits, {len(artifacts)} real photos", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replay-source", type=Path)
    parser.add_argument("--timeout", type=float, default=1500)
    args = parser.parse_args()
    prepare(args.base_url, args.output, args.replay_source, args.timeout)


if __name__ == "__main__":
    main()
