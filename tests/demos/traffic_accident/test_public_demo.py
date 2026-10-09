"""The public demo runs entirely from committed inputs and procedural geometry."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.slow
def test_headless_public_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aeroagentsim.services.cli",
            "demo",
            "traffic-accident",
            "--headless",
            "--out",
            str(tmp_path),
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=2100,
    )
    output = json.loads(result.stdout)
    assert output["status"] == "completed"
    assert [row["event"] for row in output["events"]] == [
        "accident",
        "award",
        "capture",
        "upload",
    ]
    assert 48 < output["events"][-1]["simulated_s"] < 51
    assert Path(output["journal"]).is_file()
