"""Read-only integration against the persisted AeroGraph source tree."""

from pathlib import Path

import pytest

from aeroagentsim.authoring.inputs import configured_ontology
from aeroagentsim.integrations.aerograph import Policy, compile_registry, read_snapshot

try:
    ROOT = configured_ontology()
except ValueError as error:  # unconfigured: AEROAGENTSIM_AEROGRAPH_ROOT unset/invalid
    pytest.skip(str(error), allow_module_level=True)
SLICE = ("oo:UAV", "oo:Order", "oo:ObservationRecord")


@pytest.mark.integration
@pytest.mark.skipif(not ROOT.is_dir(), reason="real AeroGraph checkout is absent")
def test_real_three_type_slice(tmp_path: Path) -> None:
    policy = Policy(admit_proposed=True)
    first = compile_registry(ROOT, SLICE, policy)
    second = compile_registry(ROOT, reversed(SLICE), policy)
    assert first.to_data() == second.to_data()
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
    assert read_snapshot(out).to_data() == first.to_data()


@pytest.mark.integration
@pytest.mark.skipif(not ROOT.is_dir(), reason="real AeroGraph checkout is absent")
def test_real_default_policy_admits_unreviewed_definitions() -> None:
    """Default policy compiles the proposed-but-real slice; proposals stay out.

    The persisted source declares reviewStatus 'proposed' on every field and
    relation, so the default policy admits them as unreviewed, preserves the
    status in provenance, counts them per status, and still excludes the
    explicit 'proposal:' candidate namespace.
    """
    result = compile_registry(ROOT, SLICE)
    assert result.statistics["compile_blockers"] == 0
    assert result.statistics["selected_types"] == 3
    assert result.statistics["fields"] > 0
    assert result.statistics["relations"] > 0
    assert all(result.effective_fields(t) for t in SLICE)
    assert result.details["admissions"]
    counts = result.details["review"]["admitted_by_status"]
    assert counts["proposed"] > 0
    assert result.statistics["admitted_unreviewed_definitions"] > 0
    assert all(
        f.metadata["reviewStatus"] == f.metadata["raw"].get("reviewStatus")
        for f in result.registry.fields
    )
    assert not any(t.id.startswith("proposal:") for t in result.registry.types)
    assert any(
        x["id"].startswith("proposal:") and x["kind"] == "relation"
        for x in result.exclusions
    )
    # --admit-proposed additionally activates the candidate namespace.
    full = compile_registry(ROOT, SLICE, Policy(admit_proposed=True))
    assert full.statistics["relations"] > result.statistics["relations"]
    assert any(t.id.startswith("proposal:") for t in full.registry.types)
