from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import ProviderRemoteError
from aero_bench.providers.sumo import PROTOCOL_VERSION, SumoProvider
from containers.sumo.service import SumoService, SumoServiceError
from tests.providers.sumo_support import sumo_workload_identity
from tests.providers.test_sumo_provider import (
    sumo_clock,
    sumo_config,
    sumo_manifest,
    sumo_scenario,
)


RUN_ID = "a" * 64
SESSION_TOKEN = "c" * 64
RUNTIME_IMAGE = "registry.test/sumo@sha256:" + "1" * 64


def _service(tmp_path: Path, *, session_token: str = SESSION_TOKEN) -> SumoService:
    config = tmp_path / "provider.json"
    config.write_bytes(b'{"schema_version":"aero-bench.sumo/v2"}\n')
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    return SumoService(
        scenario_root=tmp_path,
        artifact_root=artifacts,
        rpc_port=17434,
        session_token=session_token,
        workload_identity=sumo_workload_identity(
            run_id=RUN_ID,
            runtime_image=RUNTIME_IMAGE,
            config_digest=hashlib.sha256(config.read_bytes()).hexdigest(),
        ),
    )


def _prepare_payload(
    *, session_token: str | None, config_digest: str
) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_id": "traffic",
        "run_id": RUN_ID,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": config_digest,
        "artifact_requirements": [
            {
                "artifact_id": "artifact.sumo.traffic",
                "artifact_type": "sumo.traffic.evidence",
                "producer_id": "traffic",
                "visibility": "private",
                "relative_path": "traffic/evidence.jsonl",
                "max_size_bytes": 4096,
                "source_asset_id": None,
            }
        ],
        "sumo": {"version": "1.27.1", "commit": "b" * 40},
        "sumo_binary": "sumo",
        "traci_port": 17534,
        "step_length_ns": 100,
        "sumo_args": ["--no-step-log", "true"],
        "required_commands": ["sumo"],
        "command_timeout_ms": 1000,
    }
    if session_token is not None:
        payload["session_token"] = session_token
    return payload


@pytest.mark.parametrize("token", (None, "a" * 64, "0" * 64, "not-a-digest"))
def test_sumo_prepare_rejects_missing_or_wrong_token_before_binding(
    tmp_path: Path, token: str | None
) -> None:
    service = _service(tmp_path)
    payload = _prepare_payload(
        session_token=token, config_digest=service._workload_identity.config_digest
    )
    with pytest.raises(SumoServiceError) as error:
        asyncio.run(service.handle("prepare", payload))
    assert error.value.code == "principal.denied"
    assert service._config is None


def test_sumo_wrong_token_cannot_preempt_legitimate_prepare(tmp_path: Path) -> None:
    asyncio.run(_test_sumo_wrong_token_cannot_preempt(tmp_path))


async def _test_sumo_wrong_token_cannot_preempt(tmp_path: Path) -> None:
    service = _service(tmp_path)
    digest = service._workload_identity.config_digest
    results: list[str] = []

    async def attacker() -> None:
        try:
            await service.handle(
                "prepare",
                _prepare_payload(session_token="a" * 64, config_digest=digest),
            )
            results.append("accepted")
        except SumoServiceError as error:
            results.append(error.code)

    async def legitimate() -> None:
        try:
            await service.handle(
                "prepare",
                _prepare_payload(session_token=SESSION_TOKEN, config_digest=digest),
            )
            results.append("bound")
        except SumoServiceError as error:
            results.append(error.code)

    await asyncio.gather(attacker(), legitimate())
    assert "principal.denied" in results
    assert "accepted" not in results
    # A denied attacker must not occupy the prepare slot. The legitimate
    # prepare may still fail later on SUMO identity/binary checks, but the
    # session cannot have been bound by the wrong token.
    if service._config is not None:
        assert "bound" in results


def test_sumo_loopback_client_peer_is_denied(tmp_path: Path) -> None:
    asyncio.run(_test_sumo_loopback_client_peer_is_denied(tmp_path))


async def _test_sumo_loopback_client_peer_is_denied(tmp_path: Path) -> None:
    from containers.sumo.service import _client_handler

    service = _service(tmp_path)
    server = await asyncio.start_server(
        lambda reader, writer: _client_handler(service, reader, writer),
        host="127.0.0.1",
        port=0,
    )
    sockets = server.sockets
    assert sockets
    port = int(sockets[0].getsockname()[1])
    attacker = SumoProvider(
        config=sumo_config(),
        manifest=sumo_manifest(),
        runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
        run_id=RUN_ID,
        session_token="a" * 64,
        scenario=sumo_scenario(),
        clock=sumo_clock(),
    )
    try:
        with pytest.raises(ProviderRemoteError) as error:
            await attacker.prepare()
        assert error.value.code == "principal.denied"
        assert service._config is None
    finally:
        await attacker.shutdown()
        server.close()
        await server.wait_closed()
