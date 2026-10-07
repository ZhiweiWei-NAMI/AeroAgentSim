"""Synthetic sealed CLI fixtures, never formal benchmark execution evidence."""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import socket
import threading

import pytest

from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest, seal_manifest
from aero_bench.control import sealed_replay
from aero_bench.control.contracts import StartRunRequest
from aero_bench.control.manager import ControlManagerError, ControlRunManager
from aero_bench.control.server import ControlHttpConfig, ControlHttpServer
from aero_bench.runner import execution
from aero_bench.runner.session import RunExecutionSession
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace import projector
from aero_bench.verifier.output import ValidatedVerificationOutput
from aero_bench.verifier.contracts import VerificationReport
from tests.support import (
    build_bundle,
    fixture_provider_registry,
    promote_bundle_to_formal,
)
from tests.test_runner import _FakeExecutor, _runner_config, _write_runner_config


def _json(value):
    return canonical_json_bytes(value.model_dump(mode="json")) + b"\n"


class _SealedFixtureExecutor(_FakeExecutor):
    def collect_and_seal(self, plan, handle, *, destination_root):
        result = super().collect_and_seal(
            plan, handle, destination_root=destination_root
        )
        records = {item.artifact_id: item for item in result.artifacts}
        for item in plan.run.artifact_requirements:
            if item.artifact_id in records:
                continue
            path = destination_root / item.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = b"{}\n"
            path.write_bytes(payload)
            records[item.artifact_id] = ArtifactRecord(
                artifact_id=item.artifact_id,
                artifact_type=item.artifact_type,
                producer_id=item.producer_id,
                visibility=item.visibility,
                relative_path=item.relative_path,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        result = seal_manifest(
            root=destination_root,
            run_id=plan.run.run_id,
            attempt_id=handle.attempt_id,
            execution_scope=plan.run.execution_scope,
            event_chain_root=result.event_chain_root,
            artifacts=tuple(records.values()),
        )
        (destination_root / "seal-manifest.json").write_bytes(_json(result))
        return result

    def collect_verification_outputs(self, plan, handle, *, destination_root):
        destination_root.mkdir()
        status = self.verification_status or "passed"
        report = VerificationReport.model_validate(
            {
                "schema_version": "aero-bench.verification/v1",
                "run_id": plan.run.run_id,
                "execution_scope": plan.run.execution_scope,
                "status": status,
                "coverage_complete": True,
                "goals": [
                    {
                        "goal_id": goal.goal_id,
                        "passed": status == "passed",
                        "failure_class": None
                        if status == "passed"
                        else "fixture.rejected",
                        "metrics": []
                        if status == "invalid"
                        else [
                            {
                                "metric_id": goal.metric_id,
                                "value": 1.0 if status == "passed" else 0.0,
                                "unit": "ratio",
                                "evidence": [
                                    {
                                        "artifact_id": next(
                                            item.artifact_id
                                            for item in plan.run.artifact_requirements
                                            if item.visibility == "public"
                                        ),
                                        "selector": "result",
                                    }
                                ],
                            }
                        ],
                    }
                    for goal in plan.run.task.goals
                ],
            }
        )
        runtime = SealManifest.model_validate_json(
            (destination_root.parent / "runtime-seal/seal-manifest.json").read_bytes()
        )
        requirement = plan.run.verification_outputs[0]
        payload = _json(report)
        report_path = destination_root / requirement.relative_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(payload)
        record = ArtifactRecord(
            artifact_id=requirement.artifact_id,
            artifact_type=requirement.artifact_type,
            producer_id=requirement.producer_id,
            visibility=requirement.visibility,
            relative_path=requirement.relative_path,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        seal = seal_manifest(
            root=destination_root,
            run_id=plan.run.run_id,
            execution_scope=plan.run.execution_scope,
            attempt_id=handle.attempt_id,
            event_chain_root=runtime.event_chain_root,
            artifacts=(record,),
        )
        (destination_root / "verification-manifest.json").write_bytes(_json(seal))
        return ValidatedVerificationOutput(seal=seal, report=report)


@pytest.fixture
def sealed_cli(tmp_path, monkeypatch, request):
    bundle = build_bundle(tmp_path / "bundle")
    promote_bundle_to_formal(bundle)
    monkeypatch.setattr(
        execution, "builtin_provider_registry", fixture_provider_registry
    )
    monkeypatch.setattr(
        sealed_replay, "builtin_provider_registry", fixture_provider_registry
    )
    if getattr(request, "param", "embedded") == "indexed":
        original = projector.project_public_scenario

        def indexed(run):
            return original(run).model_copy(update={"replay_mode": "indexed"})

        monkeypatch.setattr(projector, "project_public_scenario", indexed)
        monkeypatch.setattr(sealed_replay, "project_public_scenario", indexed)
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, _runner_config(tmp_path / "results"))
    status = getattr(request, "param", "embedded")
    status = status if status in {"failed", "invalid"} else "passed"
    executor = _SealedFixtureExecutor(verification_status=status)
    monkeypatch.setattr(execution, "build_docker_executor", lambda *a, **kw: executor)
    summary = execution.run_suite(bundle.suite, config_path)
    assert summary.passed_count == (4 if status == "passed" else 0)
    run_id = summary.runs[0].run_id

    def forbidden(*args, **kwargs):
        pytest.fail("sealed delivery constructed an executor or live session")

    monkeypatch.setattr(execution, "build_docker_executor", forbidden)
    monkeypatch.setattr(RunExecutionSession, "__init__", forbidden)
    monkeypatch.setattr(ControlRunManager, "__init__", forbidden)
    return bundle.suite, config_path, tmp_path / "results", run_id


def _load(fixture):
    suite, config, _, run_id = fixture
    return sealed_replay.SealedReplayManager.from_files(
        suite_path=suite, runner_config_path=config, run_ids=(run_id,)
    )


@pytest.mark.parametrize("sealed_cli", ["failed", "invalid"], indirect=True)
def test_independent_failed_or_invalid_verdict_is_not_promoted(sealed_cli):
    manager = _load(sealed_cli)
    run_id = sealed_cli[-1]
    token = manager.issue_read_access(run_id).credentials.operator_token
    trace = json.loads(manager.public_trace_document(run_id, operator_token=token))
    assert trace["verifier_public"]["status"] in {"failed", "invalid"}
    assert manager._replays[run_id].summary.status == trace["verifier_public"]["status"]


def test_import_rejects_invalid_selection(sealed_cli):
    suite, config, _, run_id = sealed_cli
    for selection in ((), (run_id, run_id), ("f" * 64,)):
        with pytest.raises(ControlManagerError, match="selection"):
            sealed_replay.SealedReplayManager.from_files(
                suite_path=suite, runner_config_path=config, run_ids=selection
            )


@pytest.mark.parametrize("sealed_cli", ["embedded", "indexed"], indirect=True)
def test_relocated_replay_accepts_active_config_and_preserves_summary(sealed_cli):
    suite, config, output, run_id = sealed_cli
    original_summary = (output / "runner-summary.json").read_bytes()
    relocated = output.with_name("relocated")
    shutil.move(output, relocated)
    active_config = json.loads(config.read_bytes())
    active_config["output_root"] = str(relocated)
    config.write_text(json.dumps(active_config, indent=2) + "\n\n")
    manager = sealed_replay.SealedReplayManager.from_files(
        suite_path=suite, runner_config_path=config, run_ids=(run_id,),
    )
    access = manager.issue_read_access(run_id)
    assert access.read_only
    assert manager._replays[run_id].root == relocated / run_id
    assert manager.public_trace_document(run_id, operator_token=access.credentials.operator_token) == (
        relocated / run_id / "public/public-trace.json"
    ).read_bytes()
    assert (relocated / "runner-summary.json").read_bytes() == original_summary
    assert not output.exists()


@pytest.mark.parametrize("target", ["run-id", "public"])
def test_relocated_replay_still_rejects_identity_or_evidence_drift(sealed_cli, target):
    suite, config, output, run_id = sealed_cli
    relocated = output.with_name("relocated")
    shutil.move(output, relocated)
    active_config = json.loads(config.read_bytes())
    active_config["output_root"] = str(relocated)
    config.write_text(json.dumps(active_config))
    if target == "run-id":
        summary_path = relocated / "runner-summary.json"
        summary = json.loads(summary_path.read_bytes())
        summary["runs"][0]["run_id"] = "f" * 64
        summary_path.write_bytes(canonical_json_bytes(summary) + b"\n")
    else:
        (relocated / run_id / "public/public-trace.json").write_bytes(b"{}\n")
    with pytest.raises(ControlManagerError) as error:
        sealed_replay.SealedReplayManager.from_files(
            suite_path=suite, runner_config_path=config, run_ids=(run_id,),
        )
    assert error.value.code == "replay.invalid"


@pytest.mark.parametrize("sealed_cli", ["embedded", "indexed"], indirect=True)
def test_import_and_every_public_file_are_exact_and_read_only(sealed_cli):
    manager = _load(sealed_cli)
    _, _, output, run_id = sealed_cli
    assert not hasattr(manager, "_executor") and not hasattr(manager, "_managed")
    assert [item.run_id for item in manager.catalog.runs] == [run_id]
    response = manager.issue_read_access(run_id)
    token = response.credentials.operator_token
    assert response.read_only
    assert manager.issue_read_access(run_id) == response
    root = output / run_id
    manifest_data = manager.public_replay_manifest_document(
        run_id, operator_token=token
    )
    assert manifest_data == (root / "public/replay/replay-manifest.json").read_bytes()
    assert (
        manager.public_trace_document(run_id, operator_token=token)
        == (root / "public/public-trace.json").read_bytes()
    )
    for item in json.loads(manifest_data)["files"]:
        if item["relative_path"] == "public-trace.json":
            continue
        data, _ = manager.public_asset(run_id, item["sha256"], operator_token=token)
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["size_bytes"]
    assert token not in repr(manager) and token not in repr(response)
    for operation in (
        "status",
        "snapshot",
        "runtime_projection",
        "control",
        "transitions_after",
        "wait_for_transitions",
        "check_csrf",
    ):
        with pytest.raises(ControlManagerError) as error:
            getattr(manager, operation)(run_id, operator_token=token)
        assert error.value.code == "replay.read_only"
    with pytest.raises(ControlManagerError, match="cannot start"):
        manager.start(
            StartRunRequest(
                schema_version="aero-bench.start-run-request/v1",
                run_id=run_id,
                start_id="test-start",
            )
        )
    manager.shutdown()
    with pytest.raises(ControlManagerError, match="authentication"):
        manager.public_trace_document(run_id, operator_token=token)


@pytest.mark.parametrize(
    "target",
    [
        "runner-summary.json",
        "runtime-seal/seal-manifest.json",
        "runtime-seal/evidence.json",
        "verification/verification-manifest.json",
        "verification/verifier/report.json",
        "public/public-trace.json",
        "public/replay/replay-manifest.json",
    ],
)
def test_import_rejects_missing_or_corrupted_evidence(sealed_cli, target):
    _, _, output, run_id = sealed_cli
    root = output if target == "runner-summary.json" else output / run_id
    (root / target).write_bytes(b"{}\n")
    with pytest.raises(ControlManagerError) as error:
        _load(sealed_cli)
    assert error.value.code == "replay.invalid"


def test_import_rejects_terminal_run_without_verifier(sealed_cli):
    _, _, output, run_id = sealed_cli
    runner = json.loads((output / "runner-summary.json").read_bytes())
    run = next(item for item in runner["runs"] if item["run_id"] == run_id)
    run.update(
        status="error", verification=None, failure_classes=["wait_verifier_failed"]
    )
    runner["passed_count"] -= 1
    runner["execution_complete_count"] -= 1
    (output / "runner-summary.json").write_bytes(canonical_json_bytes(runner) + b"\n")
    with pytest.raises(ControlManagerError, match="independently verified"):
        _load(sealed_cli)


@pytest.mark.parametrize(
    "target", ["public/replay/extra", "runtime-seal/extra", "verification/extra"]
)
def test_import_rejects_undeclared_files(sealed_cli, target):
    _, _, output, run_id = sealed_cli
    (output / run_id / target).write_bytes(b"not declared")
    with pytest.raises(ControlManagerError, match="undeclared"):
        _load(sealed_cli)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_reads_reject_symlinks_created_after_import(sealed_cli, kind):
    manager = _load(sealed_cli)
    _, _, output, run_id = sealed_cli
    token = manager.issue_read_access(run_id).credentials.operator_token
    target = output / run_id / "public"
    if kind == "file":
        target /= "public-trace.json"
    saved = target.with_name(target.name + "-saved")
    target.rename(saved)
    target.symlink_to(saved, target_is_directory=kind == "directory")
    with pytest.raises(ControlManagerError, match="integrity"):
        manager.public_trace_document(run_id, operator_token=token)


def test_reads_require_auth_declared_inventory_and_unchanged_bytes(
    sealed_cli, monkeypatch
):
    manager = _load(sealed_cli)
    _, _, output, run_id = sealed_cli
    with pytest.raises(ControlManagerError, match="authentication"):
        manager.public_trace_document(run_id, operator_token="a" * 64)
    token = manager.issue_read_access(run_id).credentials.operator_token
    with pytest.raises(ControlManagerError, match="public replay inventory"):
        manager.public_asset(run_id, "1" * 64, operator_token=token)
    monkeypatch.setattr(sealed_replay, "_MAX_PUBLIC_TRACE_BYTES", 1)
    with pytest.raises(ControlManagerError, match="integrity"):
        manager.public_trace_document(run_id, operator_token=token)
    monkeypatch.setattr(sealed_replay, "_MAX_PUBLIC_TRACE_BYTES", 1024 * 1024 * 1024)
    trace = output / run_id / "public/public-trace.json"
    payload = trace.read_bytes()
    trace.write_bytes(payload.replace(b'"phase":"verified"', b'"phase":"tampered"'))
    with pytest.raises(ControlManagerError, match="integrity"):
        manager.public_trace_document(run_id, operator_token=token)


@pytest.mark.parametrize("sealed_cli", ["indexed"], indirect=True)
def test_http_auth_csrf_delivery_and_live_rejection(sealed_cli):
    manager = _load(sealed_cli)
    run_id = sealed_cli[-1]
    origin = "http://127.0.0.1:5392"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ControlHttpServer(
        manager=manager,
        bootstrap_token="e" * 64,
        bootstrap_csrf_token="f" * 64,
        config=ControlHttpConfig(
            schema_version="aero-bench.control-http-config/v1",
            bind_host="127.0.0.1",
            port=port,
            allowed_hosts=(f"127.0.0.1:{port}",),
            allowed_origins=(origin,),
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, token="e" * 64, body=None, csrf=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Origin": origin, "Authorization": "Bearer " + token}
        if body is not None:
            body = canonical_json_bytes(body)
            headers["Content-Type"] = "application/json"
        if csrf is not None:
            headers["X-Aero-Bench-CSRF"] = csrf
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    try:
        path = f"/v1/runs/{run_id}/replay-access"
        body = {"schema_version": "aero-bench.replay-access-request/v1"}
        assert request("POST", path, body=body)[0] == 403
        assert request("POST", path, token="a" * 64, body=body, csrf="f" * 64)[0] == 401
        status, payload = request("POST", path, body=body, csrf="f" * 64)
        assert status == 200
        token = json.loads(payload)["credentials"]["operator_token"]
        base = f"/v1/runs/{run_id}"
        assert request("GET", base + "/public/trace", token="a" * 64)[0] == 401
        status, payload = request("GET", base + "/public/replay-manifest", token=token)
        assert status == 200
        manifest = json.loads(payload)
        status, trace = request("GET", base + "/public/trace", token=token)
        assert (
            status == 200
            and hashlib.sha256(trace).hexdigest() == manifest["trace_sha256"]
        )
        for item in manifest["files"]:
            if item["relative_path"] == "public-trace.json":
                continue
            status, content = request(
                "GET", base + "/assets/" + item["sha256"], token=token
            )
            assert (
                status == 200 and hashlib.sha256(content).hexdigest() == item["sha256"]
            )
        for path in (
            base,
            base
            + "/events?after_transition=-1&after_scene_tick=0&after_event_sequence=-1",
        ):
            status, content = request("GET", path, token=token)
            assert (
                status == 409
                and json.loads(content)["error"]["code"] == "replay.read_only"
            )
        status, content = request(
            "POST",
            "/v1/runs",
            body={
                "schema_version": "aero-bench.start-run-request/v1",
                "start_id": "no-start",
                "run_id": run_id,
            },
            csrf="f" * 64,
        )
        assert (
            status == 409 and json.loads(content)["error"]["code"] == "replay.read_only"
        )
        assert (
            request(
                "POST",
                base + "/controls/stop",
                token=token,
                body={
                    "schema_version": "aero-bench.runtime-control-request/v1",
                    "control_id": "no-stop",
                },
                csrf=json.loads(
                    request("POST", f"{base}/replay-access", body=body, csrf="f" * 64)[
                        1
                    ]
                )["credentials"]["csrf_token"],
            )[0]
            == 409
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
