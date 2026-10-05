from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from pathlib import Path

import pytest

from containers.sumo.service import SumoService, SumoServiceError
from tests.providers.sumo_support import sumo_workload_identity
from tests.providers.test_ns3_service import workload_identity as ns3_workload_identity


_NS3_PATH = Path(__file__).parents[2] / "containers" / "ns3" / "server.py"
_SPEC = importlib.util.spec_from_file_location("aero_bench_ns3_probe_server", _NS3_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_NS3 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_NS3)


RUN_ID = "a" * 64
RUNTIME_IMAGE = "registry.example/aero-bench/provider@sha256:" + "1" * 64


def test_ns3_probe_is_accepting_and_side_effect_free(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
    monkeypatch.setenv("AERO_BENCH_SEED", "7")
    monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
    service = _NS3.Ns3Service(
        ns3_workload_identity()._replace(runtime_image=RUNTIME_IMAGE),
        session_token="c" * 64,
    )

    response = asyncio.run(service.handle("probe", {}))

    assert response == {
        "schema_version": "aero-bench.provider-probe/v1",
        "status": "accepting",
        "run_id": RUN_ID,
        "provider_id": "network",
        "adapter": "ns3.rpc",
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": "b" * 64,
    }
    assert tuple(tmp_path.iterdir()) == ()


def test_ns3_probe_rejects_nonempty_request(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
    monkeypatch.setenv("AERO_BENCH_SEED", "7")
    monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
    service = _NS3.Ns3Service(
        ns3_workload_identity()._replace(runtime_image=RUNTIME_IMAGE),
        session_token="c" * 64,
    )

    with pytest.raises(_NS3.ProviderError, match="empty"):
        asyncio.run(service.handle("probe", {"unexpected": True}))


def test_sumo_probe_is_accepting_before_prepare(tmp_path: Path) -> None:
    config = tmp_path / "provider.json"
    config.write_bytes(b'{"schema_version":"aero-bench.sumo/v2"}\n')
    (tmp_path / "artifacts").mkdir()
    service = SumoService(
        scenario_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        rpc_port=17434,
        session_token="c" * 64,
        workload_identity=sumo_workload_identity(
            run_id=RUN_ID,
            runtime_image=RUNTIME_IMAGE,
            config_digest=hashlib.sha256(config.read_bytes()).hexdigest(),
        ),
    )
    response = asyncio.run(service.handle("probe", {}))

    assert response == {
        "schema_version": "aero-bench.provider-probe/v1",
        "status": "accepting",
        "run_id": RUN_ID,
        "provider_id": "traffic",
        "adapter": "sumo.traci",
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": hashlib.sha256(config.read_bytes()).hexdigest(),
    }
    assert service._process is None
    assert service._connection is None

    with pytest.raises(SumoServiceError, match="empty"):
        asyncio.run(service.handle("probe", {"unexpected": True}))
