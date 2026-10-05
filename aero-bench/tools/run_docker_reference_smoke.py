from __future__ import annotations

from aero_bench.providers.registry import builtin_provider_registry

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

from aero_bench.config.loader import sha256_file
from aero_bench.executor import DockerExecutor
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.support import build_bundle, resolve_bundle


RUNTIME_IMAGE = (
    "docker.1panel.live/library/python@sha256:"
    "c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49"
)
VERIFIER_IMAGE = (
    "docker.m.daocloud.io/library/busybox@sha256:"
    "73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
)


def _runtime_payloads() -> dict[tuple[str, str], tuple[str, bytes]]:
    return {
        ("harness", "theoretical.bounds"): (
            "harness/theoretical-bounds.json",
            b'{"success_upper_bound":1.0}',
        ),
        ("flight", "trajectory"): (
            "flight/trajectory.json",
            b'{"samples":[]}',
        ),
        ("network", "network.delivery"): (
            "network/delivery.json",
            b'{"deliveries":[]}',
        ),
        ("business", "business.state"): (
            "business/state.json",
            b'{"state":"mechanical-only"}',
        ),
        ("observation", "observation.metadata"): (
            "observation/metadata.json",
            b'{"observations":[]}',
        ),
        ("reference.agent", "inspection.detection"): (
            "agent/detections.json",
            b"[]",
        ),
        ("reference.agent", "inspection.report"): (
            "agent/report.json",
            b'{"mode":"mechanical-only"}',
        ),
    }


def _writer_command(items: list[tuple[str, bytes]]) -> list[str]:
    encoded_items = [(relative_path, payload.hex()) for relative_path, payload in items]
    script = (
        "import json,os,pathlib;"
        "root=pathlib.Path(os.environ['AERO_BENCH_ARTIFACT_DIR']);"
        f"items=json.loads({json.dumps(json.dumps(encoded_items))});"
        "[(lambda p,h:(p.parent.mkdir(parents=True,exist_ok=True),"
        "p.write_bytes(bytes.fromhex(h))))(root/path,h) for path,h in items]"
    )
    return ["python", "-c", script]


def _harness_writer_command(bounds: tuple[str, bytes]) -> list[str]:
    bounds_path, bounds_payload = bounds
    script = f'''import hashlib,json,os,pathlib,time
root=pathlib.Path(os.environ["AERO_BENCH_ARTIFACT_DIR"])
run_id=os.environ["AERO_BENCH_RUN_ID"]
root_digest="0"*64
previous=root_digest
records=[]
wall=time.time_ns()
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def append(event_type,tick,sim_time_ns,payload):
    global previous,wall
    sequence=len(records)
    event_id=f"event.{{sequence:016d}}"
    attributes=[]
    for name,value in sorted(payload.items()):
        value_type="null" if value is None else "bool" if isinstance(value,bool) else "int" if isinstance(value,int) else "float" if isinstance(value,float) else "str"
        attributes.append({{"name":name,"value_type":value_type,"value":value}})
    payload_digest=hashlib.sha256(canonical(attributes)).hexdigest()
    event={{
        "schema_version":"aero-bench.run-event/v1","segment_kind":"runtime",
        "run_id":run_id,"sequence":sequence,"event_id":event_id,
        "source_kind":"harness","source":"harness","workload_id":"harness",
        "event_type":event_type,"time":{{"tick":tick,"sim_time_ns":sim_time_ns}},
        "wall_time_ns":wall,"correlation_id":event_id,"parent_event_id":None,
        "causal_event_ids":[],"visibility":[{{"scope":"private","audience_id":None}}],
        "frame_id":None,"agent_id":None,"provider_id":None,"vehicle_id":None,
        "entity_id":None,"command_id":None,"observation_id":None,
        "payload_schema_id":event_type+".v1","payload":attributes,
        "payload_digest":payload_digest,"interaction":None,
        "previous_event_digest":previous,
    }}
    event["event_digest"]=hashlib.sha256(canonical(event)).hexdigest()
    record={{"sequence":sequence,"event":event,"previous_hash":previous,"event_hash":event["event_digest"]}}
    records.append(record)
    previous=event["event_digest"]
    wall+=1
append("validation.scope",0,0,{{"execution_scope":"executor_validation"}})
append("run.started",0,0,{{"mode":"mechanical-smoke"}})
append("run.completed",1,100000000,{{}})
event_path=root/"harness/event.log"
event_path.parent.mkdir(parents=True,exist_ok=True)
event_path.write_bytes(b"".join(canonical(record)+b"\\n" for record in records))
bounds_path=root/{bounds_path!r}
bounds_path.parent.mkdir(parents=True,exist_ok=True)
bounds_path.write_bytes(bytes.fromhex({bounds_payload.hex()!r}))
'''
    return ["python", "-c", script]


def _verifier_command() -> list[str]:
    command = (
        'set -eu; test -r "$AERO_BENCH_SEAL_DIR/seal-manifest.json"; '
        'mkdir -p "$AERO_BENCH_ARTIFACT_DIR/verifier"; '
        "printf '"
        '{"coverage_complete":true,"execution_scope":"executor_validation",'
        '"goals":[{"failure_class":"executor.validation",'
        '"goal_id":"inspection.success","metrics":[{'
        '"evidence":[{"artifact_id":"artifact.event-log"}],'
        '"metric_id":"inspection.success_rate","unit":"ratio",'
        '"value":0.0}],"passed":false}],'
        '"run_id":"%s","schema_version":"aero-bench.verification/v1",'
        '"status":"invalid"}\\n\' "$AERO_BENCH_RUN_ID" > '
        '"$AERO_BENCH_ARTIFACT_DIR/verifier/report.json"'
    )
    return ["/bin/sh", "-c", command]


def _materialize_fixture(
    root: Path, payloads: dict[tuple[str, str], tuple[str, bytes]]
):
    bundle = build_bundle(root)

    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    task["verifier"]["workload"]["runtime"] = {
        "image": VERIFIER_IMAGE,
        "command": _verifier_command(),
    }
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")

    environment_path = root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    environment["harness"]["runtime"] = {
        "image": RUNTIME_IMAGE,
        "command": _harness_writer_command(
            payloads[("harness", "theoretical.bounds")]
        ),
    }
    for provider in environment["providers"]:
        key = (
            provider["provider_id"],
            provider["artifact_requirements"][0]["artifact_type"],
        )
        provider["workload"]["runtime"] = {
            "image": RUNTIME_IMAGE,
            "command": _writer_command([payloads[key]]),
        }
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )

    agent_path = root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["workload"]["runtime"] = {
        "image": RUNTIME_IMAGE,
        "command": _writer_command(
            [
                payloads[("reference.agent", "inspection.detection")],
                payloads[("reference.agent", "inspection.report")],
            ]
        ),
    }
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False), encoding="utf-8")

    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    case = suite["cases"][0]
    case["task"]["sha256"] = sha256_file(bundle.task)
    case["environment"]["sha256"] = sha256_file(environment_path)
    case["agents"][0]["sha256"] = sha256_file(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    return bundle


def _executor() -> DockerExecutor:
    return DockerExecutor(
        docker_binary="docker",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=4_194_304,
        artifact_volume_size_bytes=4_194_304,
        scratch_size_bytes=1_048_576,
        pids_limit=64,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        gateway_bind_host="0.0.0.0",
        readiness_timeout_seconds=30,
        volume_keeper_image=VERIFIER_IMAGE,
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )


def _residue(run_id: str) -> dict[str, list[str]]:
    commands = {
        "containers": (
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=aero-bench/run={run_id}",
            "--format",
            "{{.Names}}",
        ),
        "volumes": (
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=aero-bench/run={run_id}",
            "--format",
            "{{.Name}}",
        ),
        "networks": (
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=aero-bench/run={run_id}",
            "--format",
            "{{.Name}}",
        ),
    }
    return {
        kind: subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        for kind, command in commands.items()
    }


def run_smoke() -> dict[str, object]:
    payloads = _runtime_payloads()
    with tempfile.TemporaryDirectory(prefix="aero-docker-smoke-") as temporary:
        root = Path(temporary)
        bundle = _materialize_fixture(root, payloads)
        run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
        executor = _executor()
        plan = executor.materialize(run, bundle_root=root)
        preflight = executor.preflight(plan)
        if not preflight.ready:
            raise RuntimeError(f"Docker smoke preflight failed: {preflight.blockers}")

        handle = None
        seal_root = root / "sealed"
        verification_root = root / "verification"
        try:
            handle = executor.start_runtime(plan)
            executor.wait_runtime(handle, timeout_seconds=60)
            seal = executor.collect_and_seal(
                plan,
                handle,
                destination_root=seal_root,
            )
            executor.start_verifier(plan, seal)
            executor.wait_verifier(handle, timeout_seconds=60)
            verification = executor.collect_verification_outputs(
                plan,
                handle,
                destination_root=verification_root,
            )
            result: dict[str, object] = {
                "run_id": run.run_id,
                "execution_scope": run.execution_scope,
                "preflight_ready": True,
                "runtime_artifacts": len(seal.artifacts),
                "runtime_manifest_digest": seal.manifest_digest,
                "event_chain_root": seal.event_chain_root,
                "verification_artifacts": len(verification.seal.artifacts),
                "verification_manifest_digest": verification.seal.manifest_digest,
                "verification_status": verification.report.status,
                "seal_dir_mode": oct(seal_root.stat().st_mode & 0o777),
                "seal_file_modes": sorted(
                    {
                        oct(path.stat().st_mode & 0o777)
                        for path in seal_root.rglob("*")
                        if path.is_file()
                    }
                ),
                "feasibility": {
                    item.name: item.value for item in run.feasibility.bounds
                },
                "success_upper_bound": run.feasibility.success_upper_bound,
            }
        finally:
            if handle is not None:
                executor.cleanup(handle)

        residue = _residue(run.run_id)
        if any(residue.values()):
            raise RuntimeError(f"Docker resources remain after cleanup: {residue}")
        result["residue"] = residue
        return result


def main() -> int:
    print(json.dumps(run_smoke(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
