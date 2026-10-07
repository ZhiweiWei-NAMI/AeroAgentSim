"""Failure-diagnostic plumbing checks, not container execution evidence."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from aero_bench.executor.docker import DockerExecutionHandle, DockerExecutor, DockerExecutorError


def test_wrapped_wait_timeout_keeps_logs_before_stopping_owned_workloads(monkeypatch):
    executor = object.__new__(DockerExecutor)
    executor._docker_binary = "docker"
    handle = DockerExecutionHandle(
        run_id="unit-only", agent_network="agent", provider_network="provider", egress_network="egress",
        input_mount_path="/input", artifact_mount_path="/artifacts", seal_mount_path="/seal",
        runtime_containers={"harness": "unit-harness", "flight": "unit-flight"},
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command[1:])
        if command[1] == "wait":
            raise subprocess.TimeoutExpired(command, 1, output=b"1\n")
        output = "native startup failed" if command[1] == "logs" else "|false|1" if command[1] == "inspect" else ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr("aero_bench.executor.docker.shutil.which", lambda _: "/unit/docker")
    monkeypatch.setattr("aero_bench.executor.docker.subprocess.run", run)
    with pytest.raises(DockerExecutorError, match="runtime exceeded its explicit 1-second timeout") as failure:
        executor.wait_runtime(handle, timeout_seconds=1)
    assert "native startup failed" in str(failure.value)
    assert "harness=state:" in str(failure.value) and "flight=state:" in str(failure.value)
    first_stop = next(index for index, call in enumerate(calls) if call[0] == "stop")
    assert sum(call[0] == "logs" for call in calls[:first_stop]) == 2
    assert [call[-1] for call in calls if call[0] == "stop"] == ["unit-harness", "unit-flight"]


def test_readiness_failure_keeps_provider_logs_before_cleanup(monkeypatch):
    executor = object.__new__(DockerExecutor)
    executor._handles = {}
    executor._runtime_plan_bindings = {}
    executor._attempt_id = None
    executor._input_mount_path = "/input"
    executor._artifact_mount_path = "/artifacts"
    executor._seal_mount_path = "/seal"
    executor._artifact_volume_size_bytes = 1024
    executor._readiness_timeout_seconds = 1
    workloads = tuple(
        SimpleNamespace(
            workload_id=name, role=role,
            workload=SimpleNamespace(implementation=SimpleNamespace(kind="production")),
        )
        for name, role in (("flight", "provider"), ("harness", "harness"), ("agent", "agent"))
    )
    plan = SimpleNamespace(
        run=SimpleNamespace(
            run_id="unit-only", execution_scope="executor_validation",
            agents=(SimpleNamespace(agent_id="agent", driver=None),),
            task=SimpleNamespace(package=SimpleNamespace(package_id="urban.uav-recovery-demo.v1")),
        ),
        runtime_workloads=workloads,
    )
    calls = []
    provider_token = "f" * 64
    monkeypatch.setattr(executor, "_require_ready", lambda *_: None)
    monkeypatch.setattr(executor, "_runtime_plan_digest", lambda *_: "a" * 64)
    monkeypatch.setattr(executor, "_new_agent_credentials", lambda *_: {})
    monkeypatch.setattr(executor, "_new_provider_credentials", lambda *_: {"flight": provider_token})
    for method in (
        "_create_network", "_create_volume", "_create_tmpfs_volume",
        "_start_volume_keeper", "_populate_workload_inputs",
    ):
        monkeypatch.setattr(executor, method, lambda *args, **kwargs: None)
    monkeypatch.setattr(
        executor, "_create_arguments", lambda _plan, _handle, _workload, name, **kwargs: (name,)
    )

    def create_container(handle, workload, arguments):
        handle.runtime_containers[workload.workload_id] = arguments[0]

    def run(arguments, **kwargs):
        calls.append(arguments)
        output = ""
        if arguments[:3] == ("inspect", "--format", "{{json .State}}"):
            output = json.dumps(
                {"Status": "exited", "ExitCode": 1}
                if "harness" in arguments[-1]
                else {"Status": "running", "Health": {"Status": "healthy"}}
            )
        elif arguments[0] == "inspect":
            output = "|false|0"
        elif arguments[0] == "logs":
            output = (
                f"native contact startup failed; token={provider_token}"
                if "flight" in arguments[-1] else "RPC reset failed"
            )
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(executor, "_create_container", create_container)
    monkeypatch.setattr(executor, "_run", run)
    monkeypatch.setattr(executor, "cleanup", lambda _: calls.append(("cleanup",)))
    with pytest.raises(DockerExecutorError, match="exited before becoming healthy") as failure:
        executor.start_runtime(plan)
    assert "flight=state:" in str(failure.value)
    assert "native contact startup failed; token=<redacted>" in str(failure.value)
    assert provider_token not in str(failure.value)
    assert calls[-1] == ("cleanup",)
    assert any(call[0] == "logs" and "flight" in call[-1] for call in calls[:-1])
    assert not any(call[0] == "start" and "-agent-" in call[-1] for call in calls)


@pytest.mark.parametrize("clock", [(0.0, 0.0, 2.0), (0.0, 0.0, 0.0, 2.0)])
def test_readiness_timeout_names_only_pending_containers(monkeypatch, clock):
    executor = object.__new__(DockerExecutor)
    times = iter(clock)
    monkeypatch.setattr("aero_bench.executor.docker.time.monotonic", lambda: next(times))

    def run(arguments, **kwargs):
        state = {
            "Status": "running",
            "Health": {"Status": "healthy" if arguments[-1] == "unit-flight" else "starting"},
        }
        return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(state), stderr="")

    monkeypatch.setattr(executor, "_run", run)
    with pytest.raises(DockerExecutorError) as failure:
        executor._wait_for_health(("unit-flight", "unit-harness"), timeout_seconds=1)
    assert str(failure.value).endswith("pending=['unit-harness']")


def test_verifier_failure_captures_bounded_redacted_logs_before_cleanup(monkeypatch):
    executor = object.__new__(DockerExecutor)
    token = "a" * 64
    handle = DockerExecutionHandle(
        run_id="unit-only", agent_network="agent", provider_network="provider", egress_network="egress",
        input_mount_path="/input", artifact_mount_path="/artifacts", seal_mount_path="/seal",
        verifier_container="unit-verifier", runtime_control_token=token,
    )
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "wait":
            output = "1\n"
        elif arguments[0] == "inspect":
            output = "exited||false|1|"
        elif arguments[0] == "logs":
            output = f"inspection-verifier: FAILED: inconsistent frame; token={token}"
        else:
            pytest.fail(f"unexpected command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(executor, "_run", run)
    with pytest.raises(DockerExecutorError, match="verifier workloads failed") as failure:
        executor.wait_verifier(handle, timeout_seconds=10)
    assert "FAILED: inconsistent frame" in str(failure.value)
    assert "token=<redacted>" in str(failure.value)
    assert token not in str(failure.value)
    assert calls[0] == ("wait", "unit-verifier")
    assert any(call[0] == "logs" and call[-1] == "unit-verifier" for call in calls)
    assert not any(call[0] in {"stop", "rm"} for call in calls)


@pytest.mark.parametrize("phase", ["running", "completed", "aborted", "unavailable"])
def test_failure_collection_closes_harness_before_stopping_owned_containers(
    monkeypatch, tmp_path, phase
):
    executor = object.__new__(DockerExecutor)
    handle = DockerExecutionHandle(
        run_id="unit-only", agent_network="agent", provider_network="provider", egress_network="egress",
        input_mount_path="/input", artifact_mount_path="/artifacts", seal_mount_path="/seal",
        runtime_containers={"harness": "unit-harness", "agent": "unit-agent"},
    )
    plan = SimpleNamespace(run=SimpleNamespace(agents=(), artifact_requirements=()))
    calls = []
    for method in ("_require_handle", "_join_runtime_stream_pumps", "_require_stopped", "_copy_failure_artifacts"):
        monkeypatch.setattr(executor, method, lambda *args, **kwargs: None)

    def control(_plan, _handle, *, operation, control_id=None):
        calls.append((operation, control_id))
        if phase == "unavailable":
            raise DockerExecutorError("unit-only unavailable Gateway")
        return SimpleNamespace(status=SimpleNamespace(phase=phase), audit_event_id="unit.abort")

    def run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="0\n", stderr="")

    monkeypatch.setattr(executor, "_request_runtime_control", control)
    monkeypatch.setattr(executor, "_run", run)
    destination = tmp_path / "failure-evidence"
    executor.collect_failure_outputs(plan, handle, destination_root=destination)
    index = json.loads((destination / "failure-evidence.json").read_bytes())
    assert index["authoritative_seal"] is False
    assert [call[-1] for call in calls if call[0] == "stop" and len(call) > 2] == ["unit-harness", "unit-agent"]
    if phase == "unavailable":
        assert index["collection_errors"] == [{"workload_id":"harness", "error":"abort_DockerExecutorError"}]
        assert not any(call[0] == "wait" for call in calls)
    else:
        assert index["collection_errors"] == []
        wait_index = calls.index(("wait", "unit-harness"))
        first_stop = next(i for i, call in enumerate(calls) if call[0] == "stop" and len(call) > 2)
        assert wait_index < first_stop
        if phase == "running":
            assert calls[:2] == [("status", None), ("stop", "failure-diagnostic.stop")]
            assert handle.controlled_stop_audit_event_id == "unit.abort"
        else:
            assert calls[0] == ("status", None)
            assert not any(call == ("stop", "failure-diagnostic.stop") for call in calls)


@pytest.mark.parametrize("agent_code,audited", [(2, False), (0, False), (137, True)])
def test_engineering_wait_observes_agent_exit_before_harness(monkeypatch, agent_code, audited):
    executor = object.__new__(DockerExecutor)
    handle = DockerExecutionHandle(
        run_id="unit-only", agent_network="agent", provider_network="provider", egress_network="egress",
        input_mount_path="/input", artifact_mount_path="/artifacts", seal_mount_path="/seal",
        runtime_containers={"harness": "unit-harness", "agent": "unit-agent"},
        fail_fast_runtime=True,
        controlled_stop_audit_event_id="unit-stop" if audited else None,
    )
    calls = []
    polls = 0

    def run(arguments, **kwargs):
        nonlocal polls
        calls.append(arguments)
        if arguments[0] == "inspect" and '"container"' in arguments[2]:
            polls += 1
            output = "\n".join(json.dumps({"container": f"/{name}", "state": state}) for name, state in (
                ("unit-harness", {"Status": "running" if polls == 1 else "exited", "ExitCode": 0}),
                ("unit-agent", {"Status": "exited", "ExitCode": agent_code}),
            ))
        elif arguments[0] == "inspect":
            output = "running||false|0|healthy"
        elif arguments[0] == "logs":
            output = "unit Agent receipt failure" if arguments[-1] == "unit-agent" else "waiting for turn"
        else:
            pytest.fail(f"unexpected command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(executor, "_run", run)
    monkeypatch.setattr("aero_bench.executor.docker.time.sleep", lambda _: None)
    if agent_code != 0 and not audited:
        with pytest.raises(DockerExecutorError, match="runtime workloads failed") as failure:
            executor.wait_runtime(handle, timeout_seconds=10)
        assert "'unit-agent': 2" in str(failure.value)
        assert "unit Agent receipt failure" in str(failure.value)
        assert "waiting for turn" in str(failure.value)
        assert polls == 1
    else:
        executor.wait_runtime(handle, timeout_seconds=10)
        assert polls == 2  # Agent exit alone is not completed execution.
    assert not any(call[0] == "wait" for call in calls)
