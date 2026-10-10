"""Exercise the actual browser-runtime export against the independent Python reader."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aeroagentsim.integration.contracts import ViewKey
from aeroagentsim.integration.normalization import canonical_bytes
from aeroagentsim.integration.replay import MappingArtifactReader, ReadOnlyReplay
from aeroagentsim.integration.selection import SharedViewStore


@pytest.fixture(scope="module")
def console_replay():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the console/Python contract bridge")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, str(root / "frontend/console-prototype/scripts/emit-fixture.mjs")],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=root,
    )
    bundle = json.loads(result.stdout)
    artifacts = {key: canonical_bytes(value) for key, value in bundle["artifacts"].items()}
    return ReadOnlyReplay(canonical_bytes(bundle["manifest"]), MappingArtifactReader(artifacts))


def test_browser_export_has_exact_bytes_and_all_entity_kinds(console_replay):
    assert console_replay.provenance["is_actual_bench_data"] is False
    assert {entity.kind for entity in console_replay.frames[0].entities} == {"aerial", "ground", "static"}
    assert console_replay.frames[0].sim_time_ns == "100000000"
    assert console_replay.frames[0].key.stage_evidence_key == "fixture.motion.1"


def test_browser_missing_motion_stays_null_with_no_inferred_body(console_replay):
    frame = console_replay.seek("25000000000").frame
    assert frame is not None
    aerial = next(entity for entity in frame.entities if entity.kind == "aerial")
    assert aerial.position_enu_m is None and aerial.velocity_enu_mps is None
    assert aerial.body is None and "authored_evidence_gap" in aerial.unknown_reasons


def test_browser_export_bounds_and_nonindexed_time_are_explicit(console_replay):
    assert console_replay.seek("0").status == "before_start"
    assert console_replay.seek("61000000000").status == "after_end"
    assert console_replay.seek("8500000000").status == "not_indexed"
    assert console_replay.seek("8500000000", "previous").frame.sim_time_ns == "8000000000"


def test_browser_view_key_uses_python_store_revision_guard(console_replay):
    frame = console_replay.frames[0]
    current = ViewKey(frame.key, "not-evaluated", "fixture-unbound.config-digest")
    store = SharedViewStore()
    store.activate(current, "sealed_replay")
    assert store.apply_evidence(current, ())
    assert not store.apply_evidence(ViewKey(frame.key, "changed-evaluation", current.binding_epoch), ())
