"""Opt-in host checks with generous smoke bounds and full retained history."""

import json
import os
import subprocess
import sys

import pytest

from benchmarks.kernel_bench import dense_binding


@pytest.mark.perf
def test_dense_dependency_binding_is_linear():
    assert dense_binding(40)["bind_wall_s"] < 2.0


@pytest.mark.perf
def test_two_engine_file_wal_and_retained_history_smoke(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/kernel_bench.py",
            "--entities",
            "100",
            "--steps",
            "60",
            "--journal",
            str(tmp_path / "smoke.jsonl"),
        ],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    measured = json.loads(result.stdout)
    assert measured["failure"] is None
    assert measured["sealed_ns"] == 6_000_000_000
    assert measured["fact_versions"] == 18_300
    assert measured["wall_s"] < 15 and measured["rss_peak_mib"] < 400
    assert measured["journal_bytes"] > 0
    assert measured["bytes_per_fact_version"] < 120
