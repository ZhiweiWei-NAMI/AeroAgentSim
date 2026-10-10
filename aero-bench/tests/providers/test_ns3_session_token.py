from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from aero_bench.providers.ns3 import PROTOCOL_VERSION, Ns3Provider
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import ProviderRemoteError
from tests.providers.test_ns3_provider import config, manifest
from tests.providers.test_ns3_service import workload_identity
from tests.providers.ns3_support import network_scenario


_NS3_PATH = Path(__file__).parents[2] / "containers" / "ns3" / "server.py"
_SPEC = importlib.util.spec_from_file_location("aero_bench_ns3_token_server", _NS3_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_NS3 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_NS3)


RUN_ID = "a" * 64
SESSION_TOKEN = "c" * 64
RUNTIME_IMAGE = "registry.test/ns3@sha256:" + "1" * 64


def _prepare_payload(*, session_token: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_id": "network",
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": "b" * 64,
        "network_model": _NS3.NETWORK_MODEL,
        "supported_wifi_standards": ["802.11ax"],
        "artifact_requirements": [
            {
                "artifact_id": "artifact.network.delivery",
                "artifact_type": "network.delivery",
                "producer_id": "network",
                "visibility": "private",
                "relative_path": "evidence/network-delivery.json",
                "max_size_bytes": 65_536,
                "source_asset_id": None,
            }
        ],
    }
    if session_token is not None:
        payload["session_token"] = session_token
    return payload


def _service(tmp_path: Path, monkeypatch, *, session_token: str = SESSION_TOKEN):
    monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
    monkeypatch.setenv("AERO_BENCH_SEED", "7")
    monkeypatch.setenv("AERO_BENCH_ARTIFACT_DIR", str(tmp_path))
    return _NS3.Ns3Service(
        workload_identity(),
        session_token=session_token,
    )


@pytest.mark.parametrize("token", (None, "a" * 64, "0" * 64, "not-a-digest"))
def test_ns3_prepare_rejects_missing_or_wrong_token_before_binding(
    tmp_path: Path, monkeypatch, token: str | None
) -> None:
    service = _service(tmp_path, monkeypatch)
    with pytest.raises(_NS3.ProviderError) as error:
        asyncio.run(service.handle("prepare", _prepare_payload(session_token=token)))
    assert error.value.code == "principal.denied"
    assert service._prepared is False


def test_ns3_wrong_token_cannot_preempt_legitimate_prepare(
    tmp_path: Path, monkeypatch
) -> None:
    asyncio.run(
        _test_ns3_wrong_token_cannot_preempt_legitimate_prepare(tmp_path, monkeypatch)
    )


async def _test_ns3_wrong_token_cannot_preempt_legitimate_prepare(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    results: list[object] = []

    async def attacker() -> None:
        try:
            await service.handle("prepare", _prepare_payload(session_token="a" * 64))
            results.append("accepted")
        except _NS3.ProviderError as error:
            results.append(error.code)

    async def legitimate() -> None:
        try:
            await service.handle(
                "prepare", _prepare_payload(session_token=SESSION_TOKEN)
            )
            results.append("bound")
        except _NS3.ProviderError as error:
            results.append(error.code)

    await asyncio.gather(attacker(), legitimate())
    assert "principal.denied" in results
    assert "accepted" not in results
    if service._prepared:
        assert "bound" in results


def test_ns3_loopback_client_attaches_token_and_peer_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    asyncio.run(
        _test_ns3_loopback_client_attaches_token_and_peer_is_denied(
            tmp_path, monkeypatch
        )
    )


async def _test_ns3_loopback_client_attaches_token_and_peer_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path, monkeypatch)
    server = await asyncio.start_server(
        lambda reader, writer: _NS3._serve_client(reader, writer, service),
        host="127.0.0.1",
        port=0,
    )
    sockets = server.sockets
    assert sockets
    port = int(sockets[0].getsockname()[1])
    endpoint = RuntimeEndpoint(host="127.0.0.1", port=port)
    attacker = Ns3Provider(
        config=config(),
        manifest=manifest(),
        runtime_endpoint=endpoint,
        run_id=RUN_ID,
        session_token="a" * 64,
        scenario=network_scenario(),
    )
    try:
        with pytest.raises(ProviderRemoteError) as error:
            await attacker.prepare()
        assert error.value.code == "principal.denied"
        assert service._prepared is False
    finally:
        await attacker.shutdown()
        server.close()
        await server.wait_closed()
