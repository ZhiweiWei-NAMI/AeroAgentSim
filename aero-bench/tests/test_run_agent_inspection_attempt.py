from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.config.models import FileRef


_TOOL_PATH = (
    Path(__file__).parents[1] / "tools" / "run_agent_inspection_attempt.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_run_agent_inspection_attempt_test", _TOOL_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_TOOL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_TOOL)


def _write(root: Path, relative_path: str, payload: bytes) -> FileRef:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return FileRef(
        path=relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class _DumpModel:
    def __init__(self, payload: dict[str, object], **attributes: object) -> None:
        self._payload = payload
        for name, value in attributes.items():
            setattr(self, name, value)

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return dict(self._payload)


class _FakeRunnerConfig(_DumpModel):
    @classmethod
    def model_validate(cls, payload: object) -> "_FakeRunnerConfig":
        assert isinstance(payload, dict)
        return cls(dict(payload), **payload)


class _FakeSummary(_DumpModel):
    def __init__(self, run_id: str, status: str = "passed") -> None:
        super().__init__(
            {
                "run_id": run_id,
                "executor_kind": "docker_reference",
                "execution_scope": "formal_benchmark",
                "preflight": {"ready": True, "blocker_codes": []},
                "status": status,
                "seal": {"attempt_id": "recorded-by-executor"},
                "verification": {"status": status},
                "public_trace": {"relative_path": "public/public-trace.json"},
                "failure_classes": [],
            },
            run_id=run_id,
            status=status,
        )


def _inputs(tmp_path: Path) -> SimpleNamespace:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    suite = _write(bundle, "suite.yaml", b"schema_version: test\n")
    runner = _write(bundle, "runner.local.yaml", b"schema_version: test-runner\n")
    task = _write(bundle, "specs/task.yaml", b"task: exact\n")
    environment = _write(bundle, "specs/environment.yaml", b"environment: exact\n")
    world = _write(bundle, "specs/world.yaml", b"world: exact\n")
    agent = _write(bundle, "specs/agent.yaml", b"agent: exact\n")
    matrix = _write(bundle, "specs/matrix.json", b'{"value":1}\n')
    instruction = _write(bundle, "instruction.md", b"Complete the inspection.\n")
    case = SimpleNamespace(
        case_id="case.formal",
        task=task,
        environment=environment,
        world_package=world,
        agents=(agent,),
        axes=(
            SimpleNamespace(axis_id="weather", values=(matrix, "clear")),
        ),
    )
    loaded = SimpleNamespace(
        root=bundle.resolve(),
        suite_path=(bundle / suite.path).resolve(),
        suite_digest=suite.sha256,
        suite=SimpleNamespace(cases=(case,)),
    )
    task_model = _DumpModel(
        {
            "schema_version": "aero-bench.task/v1",
            "task_id": "inspection.formal",
            "instruction": instruction.model_dump(mode="json"),
        },
        instruction=instruction,
    )
    run_id = "a" * 64
    run = _DumpModel(
        {
            "schema_version": "aero-bench.resolved-run/v3",
            "run_id": run_id,
            "execution_scope": "formal_benchmark",
            "case_id": case.case_id,
            "task": task_model.model_dump(mode="json"),
        },
        run_id=run_id,
        case_id=case.case_id,
        execution_scope="formal_benchmark",
        task=task_model,
    )
    base_config = _FakeRunnerConfig.model_validate(
        {
            "schema_version": "aero-bench.runner-config/v1",
            "executor_kind": "docker_reference",
            "output_root": "/discarded",
            "runtime_timeout_seconds": 1,
            "verifier_timeout_seconds": 2,
        }
    )
    auth = tmp_path / "model-auth.json"
    auth.write_text('{"secret":"never snapshot this"}\n', encoding="utf-8")
    return SimpleNamespace(
        bundle=bundle,
        suite_path=loaded.suite_path,
        runner_path=(bundle / runner.path).resolve(),
        loaded=loaded,
        run=run,
        base_config=base_config,
        auth=auth.resolve(),
        output=(tmp_path / "validation" / "agent-inspection").resolve(),
    )


def _install_success_fakes(
    monkeypatch: pytest.MonkeyPatch,
    inputs: SimpleNamespace,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(_TOOL, "load_suite", lambda path: inputs.loaded)
    monkeypatch.setattr(
        _TOOL,
        "resolve_suite",
        lambda *args, **kwargs: (inputs.run,),
    )
    monkeypatch.setattr(
        _TOOL, "builtin_task_package_resolvers", lambda: ("resolver",)
    )
    monkeypatch.setattr(
        _TOOL, "load_runner_config", lambda path: inputs.base_config
    )
    monkeypatch.setattr(_TOOL, "RunnerConfig", _FakeRunnerConfig)
    monkeypatch.setattr(
        _TOOL,
        "build_docker_executor",
        lambda config, *, provider_registry: calls.append(
            {"config": config, "provider_registry": provider_registry}
        )
        or object(),
    )

    class Session:
        def __init__(self, **kwargs: object) -> None:
            calls.append(dict(kwargs))
            self.kwargs = kwargs

        def execute(self) -> _FakeSummary:
            run = self.kwargs["run"]
            output_root = self.kwargs["output_root"]
            run_root = output_root / run.run_id
            (run_root / "runtime-seal").mkdir(parents=True)
            (run_root / "runtime-seal" / "seal.json").write_text("sealed\n")
            (run_root / "verification").mkdir()
            (run_root / "verification" / "verification.json").write_text(
                "verified\n"
            )
            (run_root / "public").mkdir()
            (run_root / "public" / "public-trace.json").write_text("public\n")
            return _FakeSummary(run.run_id)

    monkeypatch.setattr(_TOOL, "RunExecutionSession", Session)
    return calls


def test_attempt_uses_explicit_runner_identity_and_preserves_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    calls = _install_success_fakes(monkeypatch, inputs)
    attempt_id = "attempt.20260912t010203z.unit"

    results = _TOOL.execute_attempts(
        suite_path=inputs.suite_path,
        output_root=inputs.output,
        model_auth_file=inputs.auth,
        model_https_proxy="http://model-proxy.internal:8443",
        attempt_id=attempt_id,
    )

    assert results[0][0].status == "passed"
    attempt_root = inputs.output / inputs.run.run_id / attempt_id
    assert results[0][1] == attempt_root
    assert not (attempt_root / ".staging").exists()
    assert (attempt_root / "runtime-seal" / "seal.json").read_text() == "sealed\n"
    assert (attempt_root / "verification" / "verification.json").is_file()
    assert (attempt_root / "public" / "public-trace.json").is_file()

    config = calls[0]["config"]
    assert config.attempt_id == attempt_id
    assert config.model_auth_file == str(inputs.auth)
    assert config.model_https_proxy == "http://model-proxy.internal:8443"
    assert config.runtime_timeout_seconds == 22_800
    assert config.verifier_timeout_seconds == 22_800
    assert config.output_root == str(attempt_root / ".staging")
    session = calls[1]
    assert session["output_root"] == attempt_root / ".staging"

    manifest = json.loads(
        (attempt_root / "source-specs" / "manifest.json").read_text()
    )
    paths = {record["relative_path"] for record in manifest["files"]}
    assert paths == {
        "suite.yaml",
        "runner.local.yaml",
        "specs/task.yaml",
        "specs/environment.yaml",
        "specs/world.yaml",
        "specs/agent.yaml",
        "specs/matrix.json",
        "instruction.md",
    }
    assert (
        attempt_root / "source-specs" / "files" / "specs" / "task.yaml"
    ).read_bytes() == b"task: exact\n"
    assert "never snapshot this" not in "\n".join(
        path.read_text(errors="replace")
        for path in attempt_root.rglob("*")
        if path.is_file()
    )
    attempt_summary = json.loads(
        (attempt_root / "attempt-summary.json").read_text()
    )
    assert attempt_summary["attempt_id"] == attempt_id
    assert attempt_summary["status"] == "passed"
    assert all(
        len(attempt_summary[name]) == 64
        for name in (
            "source_snapshot_sha256",
            "task_definition_sha256",
            "resolved_run_sha256",
            "runner_config_sha256",
            "run_summary_sha256",
        )
    )


def test_existing_attempt_is_rejected_without_starting_a_second_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    calls = _install_success_fakes(monkeypatch, inputs)
    arguments = {
        "suite_path": inputs.suite_path,
        "output_root": inputs.output,
        "model_auth_file": inputs.auth,
        "model_https_proxy": "http://model-proxy.internal:8443",
        "attempt_id": "attempt.duplicate",
    }

    _TOOL.execute_attempts(**arguments)
    with pytest.raises(_TOOL.AttemptRunnerError, match="attempt already exists"):
        _TOOL.execute_attempts(**arguments)

    assert sum("output_root" in call for call in calls) == 1


def test_unexpected_session_failure_retains_staging_and_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    _install_success_fakes(monkeypatch, inputs)

    class FailingSession:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def execute(self) -> object:
            run = self.kwargs["run"]
            run_root = self.kwargs["output_root"] / run.run_id
            run_root.mkdir(parents=True)
            (run_root / "failure-diagnostic.json").write_text("session evidence\n")
            raise RuntimeError("session exploded")

    monkeypatch.setattr(_TOOL, "RunExecutionSession", FailingSession)
    attempt_id = "attempt.failure"

    with pytest.raises(RuntimeError, match="session exploded"):
        _TOOL.execute_attempts(
            suite_path=inputs.suite_path,
            output_root=inputs.output,
            model_auth_file=inputs.auth,
            model_https_proxy="http://model-proxy.internal:8443",
            attempt_id=attempt_id,
        )

    attempt_root = inputs.output / inputs.run.run_id / attempt_id
    assert (
        attempt_root
        / ".staging"
        / inputs.run.run_id
        / "failure-diagnostic.json"
    ).read_text() == "session evidence\n"
    failure = json.loads((attempt_root / "wrapper-failure.json").read_text())
    summary = json.loads((attempt_root / "attempt-summary.json").read_text())
    assert failure["failure_type"] == "RuntimeError"
    assert summary["status"] == "error"
    assert summary["failure"] == "session exploded"


def test_attempt_identifier_cannot_escape_the_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    calls = _install_success_fakes(monkeypatch, inputs)

    with pytest.raises(_TOOL.AttemptRunnerError, match="declared identifier"):
        _TOOL.execute_attempts(
            suite_path=inputs.suite_path,
            output_root=inputs.output,
            model_auth_file=inputs.auth,
            model_https_proxy="http://model-proxy.internal:8443",
            attempt_id="../escaped",
        )

    assert calls == []
    assert not (inputs.output / "escaped").exists()
