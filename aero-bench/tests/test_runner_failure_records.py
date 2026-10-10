"""Operator diagnostics preserve stage causes without changing run evidence."""

import json
from types import SimpleNamespace

from aero_bench.runner.session import RunExecutionSession


def test_later_projection_failure_does_not_erase_verifier_root_cause(tmp_path):
    session = SimpleNamespace(
        run_root=tmp_path, run=SimpleNamespace(run_id="unit-only")
    )
    RunExecutionSession._write_failure_diagnostic(
        session, stage="wait_verifier", error=ValueError("unit verifier failure")
    )
    RunExecutionSession._write_failure_diagnostic(
        session,
        stage="public_trace_projection",
        error=ValueError("unit projection failure"),
    )
    first = json.loads(
        (tmp_path / "failure-diagnostic.wait_verifier.json").read_bytes()
    )
    second = json.loads(
        (tmp_path / "failure-diagnostic.public_trace_projection.json").read_bytes()
    )
    latest = json.loads((tmp_path / "failure-diagnostic.json").read_bytes())
    assert first["error"] == "unit verifier failure"
    assert second["error"] == "unit projection failure"
    assert latest == second


def test_repeated_diagnostic_stage_preserves_its_first_record(tmp_path):
    session = SimpleNamespace(
        run_root=tmp_path, run=SimpleNamespace(run_id="unit-only")
    )
    RunExecutionSession._write_failure_diagnostic(
        session, stage="wait_verifier", error=ValueError("first unit failure")
    )
    original = (tmp_path / "failure-diagnostic.wait_verifier.json").read_bytes()
    RunExecutionSession._write_failure_diagnostic(
        session, stage="wait_verifier", error=ValueError("second unit failure")
    )
    assert (tmp_path / "failure-diagnostic.wait_verifier.json").read_bytes() == original
