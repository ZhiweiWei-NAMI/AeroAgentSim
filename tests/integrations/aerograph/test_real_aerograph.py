"""Read-only integration against the persisted AeroGraph source tree."""

from pathlib import Path

import pytest

from aeroagentsim.integrations.aerograph import Policy, compile_registry, read_snapshot

ROOT = Path("/mnt/data2/weizhiwei/AeroGraph")
SLICE = ("oo:UAV", "oo:Order", "oo:ObservationRecord")


@pytest.mark.integration
@pytest.mark.skipif(not ROOT.is_dir(), reason="real AeroGraph checkout is absent")
def test_real_three_type_slice(tmp_path: Path) -> None:
    policy = Policy(admit_proposed=True)
    first = compile_registry(ROOT, SLICE, policy)
    second = compile_registry(ROOT, reversed(SLICE), policy)
    assert first.digest == second.digest
    assert first.registry.digest == second.registry.digest
    assert first.statistics["compile_blockers"] == 0
    assert first.statistics["selected_types"] == 3
    assert first.statistics["fields"] > 0
    assert first.statistics["relations"] > 0
    assert all(first.effective_fields(t) for t in SLICE)
    assert first.normalizations
    assert first.details["admissions"]
    out = tmp_path / "real.snapshot.json"
    first.write_snapshot(out)
    assert read_snapshot(out).digest == first.digest


@pytest.mark.integration
@pytest.mark.skipif(not ROOT.is_dir(), reason="real AeroGraph checkout is absent")
def test_real_default_policy_preserves_quarantine() -> None:
    result = compile_registry(ROOT, SLICE)
    assert result.exclusions
    assert not result.details["admissions"]
    assert all(
        f.metadata["raw"].get("reviewStatus") != "proposed"
        for f in result.registry.fields
    )
    assert all(not r["research_admitted"] for r in result.relations)
