"""Input-buffer changes must preserve the existing strict ledger boundary."""

import hashlib
import io

import pytest

from aero_bench.runtime.ledger import EventLedger
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.verifier.streaming import _read_records
from aero_bench.verifier.streaming import load_sealed_event_ledger_streaming


def _payload():
    ledger = EventLedger(run_id="a" * 64)
    ledger.append_event(source="harness", event_type="run.created",
                        time=SimulationTime(tick=0, sim_time_ns=0))
    from aero_bench.runtime.ledger import ledger_jsonl_bytes
    return ledger_jsonl_bytes(ledger.records)


def test_streamed_records_equal_existing_decoder(tmp_path):
    payload = _payload()
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(payload)
    expected = EventLedger.read_jsonl(path)
    actual = _read_records(io.BytesIO(payload), size_bytes=len(payload),
                           sha256=hashlib.sha256(payload).hexdigest())
    assert actual.records == expected.records
    assert actual.chain_root == expected.chain_root


@pytest.mark.parametrize("mutate", [
    lambda p: p[:-1], lambda p: p + b"\n", lambda p: b" " + p,
    lambda p: p.replace(b'"sequence":0', b'"sequence":1'),
])
def test_streamed_decoder_rejects_same_malformed_inputs(tmp_path, mutate):
    payload = mutate(_payload())
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        EventLedger.read_jsonl(path)
    with pytest.raises(ValueError):
        _read_records(io.BytesIO(payload), size_bytes=len(payload),
                      sha256=hashlib.sha256(payload).hexdigest())


@pytest.mark.parametrize("size_delta", [-1, 1])
def test_streamed_decoder_rejects_size_drift(size_delta):
    payload = _payload()
    with pytest.raises(ValueError, match="size"):
        _read_records(io.BytesIO(payload), size_bytes=len(payload) + size_delta,
                      sha256=hashlib.sha256(payload).hexdigest())


def test_streamed_decoder_rejects_digest_drift():
    payload = _payload()
    with pytest.raises(ValueError, match="digest"):
        _read_records(io.BytesIO(payload), size_bytes=len(payload), sha256="0" * 64)


def test_streamed_decoder_never_reads_whole_file():
    class LinesOnly(io.BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("whole-file read")
    payload = _payload()
    assert _read_records(LinesOnly(payload), size_bytes=len(payload),
                         sha256=hashlib.sha256(payload).hexdigest()).records


def _sealed_fixture(tmp_path):
    from tests.test_inspection_verifier import _evidence, _package, _resolved_run
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    return run, evidence.seal, root


def test_streamed_sealed_loader_matches_existing_authority_boundary(tmp_path, monkeypatch):
    from aero_bench.runtime.evidence import load_sealed_event_ledger
    run, seal, root = _sealed_fixture(tmp_path)
    expected = load_sealed_event_ledger(run=run, seal=seal, seal_root=root)
    actual = load_sealed_event_ledger_streaming(run=run, seal=seal, seal_root=root)
    assert actual.records == expected.records
    assert actual.chain_root == expected.chain_root


@pytest.mark.parametrize("mutation", ["digest", "size", "identity", "chain", "symlink"])
def test_streamed_sealed_loader_retains_pin_and_path_gates(tmp_path, mutation):
    run, seal, root = _sealed_fixture(tmp_path)
    if mutation in ("digest", "size"):
        seal = seal.model_copy(update={"artifacts": tuple(
            a.model_copy(update={"sha256": "0" * 64} if mutation == "digest" else {"size_bytes": 1})
            if a.artifact_type == "event.log" else a for a in seal.artifacts
        )})
    elif mutation == "identity":
        seal = seal.model_copy(update={"run_id": "0" * 64})
    elif mutation == "chain":
        seal = seal.model_copy(update={"event_chain_root": "0" * 64})
    else:
        artifact = next(a for a in seal.artifacts if a.artifact_type == "event.log")
        path = root / artifact.relative_path
        relocated = tmp_path / "original-ledger.jsonl"
        path.rename(relocated)
        path.symlink_to(relocated)
    with pytest.raises((ValueError, OSError)):
        load_sealed_event_ledger_streaming(run=run, seal=seal, seal_root=root)


def test_primary_loader_streams_large_histories_and_preserves_decoded_evidence(tmp_path, monkeypatch):
    from aero_bench.runtime.scene_history import scene_state_history_from_jsonl_bytes
    from aero_bench.tasks.inspection import sealed_evidence
    from tests.test_inspection_verifier import _package
    run, seal, root = _sealed_fixture(tmp_path)
    original = sealed_evidence._read_artifact_bytes

    def reject_buffered_history(descriptor, artifact, **kwargs):
        assert artifact.artifact_type not in {"event.log", "scene.state-history"}
        return original(descriptor, artifact, **kwargs)

    monkeypatch.setattr(sealed_evidence, "_read_artifact_bytes", reject_buffered_history)
    # The fixture bundle lives in the shared test helper's declared bundle root.
    from tests.test_inspection_verifier import _bundle_root
    loader = sealed_evidence.InspectionSealedEvidenceLoader(_package(), bundle_root=_bundle_root(root))
    evidence = loader.load(run=run, seal=seal, sealed_root=root)
    event = next(a for a in seal.artifacts if a.artifact_type == "event.log")
    scene = next(a for a in seal.artifacts if a.artifact_type == "scene.state-history")
    assert evidence.event_ledger.records == EventLedger.read_jsonl(root / event.relative_path).records
    assert evidence.scene_states == scene_state_history_from_jsonl_bytes((root / scene.relative_path).read_bytes())


@pytest.mark.parametrize("kind", ["event.log", "scene.state-history", "business.state"])
def test_primary_loader_rejects_size_preserving_digest_corruption(tmp_path, kind):
    from tests.test_inspection_verifier import _load_sealed, _package
    run, seal, root = _sealed_fixture(tmp_path)
    artifact = next(a for a in seal.artifacts if a.artifact_type == kind)
    path = root / artifact.relative_path
    payload = path.read_bytes()
    # A whitespace substitution preserves file size but cannot preserve its pin.
    path.write_bytes(payload.replace(b"true", b"null", 1) if b"true" in payload else payload.replace(b"0", b"1", 1))
    assert path.stat().st_size == artifact.size_bytes
    assert path.read_bytes() != payload
    with pytest.raises(ValueError):
        _load_sealed(package=_package(), run=run, seal=seal, sealed_root=root)


def test_streamed_regular_file_checks_digest_before_issuing_content(tmp_path):
    import os
    from pathlib import PurePosixPath
    from aero_bench.tasks.inspection.sealed_evidence import _iter_regular_file
    path = tmp_path / "data.jsonl"
    path.write_bytes(b"{}\n")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="digest mismatch"):
            tuple(_iter_regular_file(descriptor, PurePosixPath(path.name), expected_size=3,
                                     max_size_bytes=3, description="test artifact", sha256="0" * 64,
                                     linewise=True))
    finally:
        os.close(descriptor)


def test_streamed_regular_file_detects_in_place_mutation(tmp_path):
    import os
    from pathlib import PurePosixPath
    from aero_bench.tasks.inspection.sealed_evidence import _iter_regular_file
    path = tmp_path / "data.jsonl"
    path.write_bytes(b"{}\n{}\n")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        stream = _iter_regular_file(descriptor, PurePosixPath(path.name), expected_size=6,
                                    max_size_bytes=6, description="test artifact", linewise=True)
        assert next(stream) == b"{}\n"
        path.write_bytes(b"{}\n[]\n")
        # Filesystem timestamps can share one clock quantum for adjacent writes.
        # Make the mutation's mtime distinct so this tests the metadata gate.
        metadata = path.stat()
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000))
        with pytest.raises(ValueError, match="changed while being read"):
            tuple(stream)
    finally:
        os.close(descriptor)
