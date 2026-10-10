from __future__ import annotations

import json

import pytest

from aero_bench.config.cli import main
from tests.support import build_bundle, fixture_provider_registry
import aero_bench.config.cli as config_cli


def test_cli_writes_new_immutable_resolved_run_directory(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(config_cli, "builtin_provider_registry", fixture_provider_registry)
    bundle = build_bundle(tmp_path / "bundle")
    output = tmp_path / "resolved"

    assert (
        main(
            (
                "--executor",
                "docker_reference",
                "--output-dir",
                str(output),
                str(bundle.suite),
            )
        )
        == 0
    )
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    resolved_files = sorted(output.glob("*.resolved-run.json"))

    assert index["run_count"] == 4
    assert len(resolved_files) == 4
    assert json.loads(capsys.readouterr().out)["run_ids"] == index["run_ids"]
    with pytest.raises(FileExistsError):
        main(
            (
                "--executor",
                "docker_reference",
                "--output-dir",
                str(output),
                str(bundle.suite),
            )
        )
