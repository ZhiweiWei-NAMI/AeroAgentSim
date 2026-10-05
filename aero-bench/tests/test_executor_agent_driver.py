from __future__ import annotations

import json
from pathlib import Path

import pytest

from aero_bench.executor import DockerExecutorError
from tests.support import build_bundle, resolve_bundle
from tests.test_executor_docker import completed, executor, handle


def test_model_auth_is_one_bounded_regular_file_capture(tmp_path) -> None:
    auth = tmp_path / "auth.json"
    payload = b'{"tokens":{"access_token":"test-access","account_id":"test-account"}}'
    auth.write_bytes(payload)
    ex = executor()
    ex._model_auth_file = str(auth)
    assert ex._model_auth_bytes() == payload
    auth.write_bytes(b"changed after capture")
    assert ex._model_auth_bytes() == payload
    other = executor()
    link = tmp_path / "auth-link.json"
    link.symlink_to(auth)
    other._model_auth_file = str(link)
    with pytest.raises(DockerExecutorError, match="unavailable"):
        other._model_auth_bytes()
    other._model_auth_file = str(tmp_path / "oversized.json")
    Path(other._model_auth_file).write_bytes(b"x" * 1_048_577)
    with pytest.raises(DockerExecutorError, match="invalid"):
        other._model_auth_bytes()


def test_failed_exit_is_permitted_only_for_partial_evidence(monkeypatch) -> None:
    ex = executor()
    monkeypatch.setattr(
        ex, "_run", lambda args, **kwargs: completed(args, stdout="false 2\n")
    )
    with pytest.raises(DockerExecutorError, match="unsuccessfully"):
        ex._require_stopped(("failed-driver",))
    ex._require_stopped(("failed-driver",), allow_failed=True)
    monkeypatch.setattr(
        ex, "_run", lambda args, **kwargs: completed(args, stdout="true 0\n")
    )
    with pytest.raises(DockerExecutorError, match="still running"):
        ex._require_stopped(("failed-driver",), allow_failed=True)


def test_partial_evidence_survives_nonzero_exit_and_another_copy_failure(
    tmp_path, monkeypatch
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    execution = handle(run.run_id)
    execution.attempt_id = "attempt.partial"
    execution.runtime_containers = {
        "reference.agent": "failed-agent",
        "harness": "failed-harness",
    }
    requirement = run.agents[0].artifact_requirements[0]
    data = b"[]"

    def command(args, **kwargs):
        if args[0] == "inspect":
            return completed(args, stdout="false 2\n")
        if args[0] == "cp":
            if args[1].startswith("failed-harness:"):
                raise DockerExecutorError("fixture copy failure")
            destination = Path(args[2]) / requirement.relative_path
            destination.parent.mkdir(parents=True)
            destination.write_bytes(data)
        return completed(args)

    monkeypatch.setattr(ex, "_run", command)
    monkeypatch.setattr(ex, "_join_runtime_stream_pumps", lambda _handle: None)
    destination = tmp_path / "failure-evidence"
    ex.collect_failure_outputs(plan, execution, destination_root=destination)
    evidence = json.loads((destination / "failure-evidence.json").read_bytes())
    assert evidence["attempt_id"] == "attempt.partial"
    assert evidence["authoritative_seal"] is False
    assert [a["artifact_id"] for a in evidence["artifacts"]] == [
        requirement.artifact_id
    ]
    assert (destination / requirement.relative_path).read_bytes() == data
    assert evidence["missing_artifact_ids"]
    assert evidence["collection_errors"]
    assert not (destination / "seal-manifest.json").exists()
    with pytest.raises(ValueError, match="fresh"):
        ex.collect_failure_outputs(plan, execution, destination_root=destination)


def test_session_transport_credentials_are_redacted_from_command_errors() -> None:
    secret = "f" * 64
    ex = executor()
    arguments = ("create", "--env", "AERO_BENCH_SESSION_TOKEN=" + secret)
    assert secret not in " ".join(ex._redact_arguments(arguments))
    assert secret not in ex._redact_text("failed " + secret, arguments)
