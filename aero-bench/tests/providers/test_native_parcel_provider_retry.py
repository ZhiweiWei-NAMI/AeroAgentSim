"""Regressions for the NativeParcelBusinessProvider pre-RPC temporal gate.

Actual package calls with an in-memory transport: the real provider method
reaches the real ``NativeParcelRpcComponent.transition`` (authoritative dedup),
with no service launch, no native flight and no executor.  The positive case
reproduces the parent scenario: after the authoritative tick advances, a
retransmitted already-processed request must be replayed by the RPC dedup
instead of being refused by the provider gate.
"""
import asyncio
from types import SimpleNamespace

import pytest

from aero_bench.providers.logistics_business.native_parcel import (
    NativeParcelBusinessProvider,
)
from aero_bench.tasks.logistics.native_parcel_runtime import NativeParcelRuntimeError
from tests.tasks import test_native_parcel_runtime as h
from tests.tasks import test_native_parcel_rpc as r


@pytest.fixture
def package():
    return h.lower_logistics_task_package(h._package_document())


def ready(package):
    rpc = r.component(package)
    for tick in (1, 2, 3):
        rpc.ingest_closed_stage(r.batch(package, tick))
    return rpc


def _provider_with_in_memory_transport(rpc, calls):
    """The real provider method bound to an in-memory authoritative RPC."""
    provider = object.__new__(NativeParcelBusinessProvider)
    provider._prepared = True
    provider._run_id = h._RUN_ID
    provider._last_time = h._dwell_tick(3)
    provider._config = SimpleNamespace(provider_id="logistics.native-parcel")

    async def transport(operation, payload):
        calls.append(operation)
        req = r.CommandRequest.model_validate(payload["request"])
        record = rpc.transition(req, now=provider._last_time)
        phases = (
            ("received", "accepted", "applied", "completed")
            if record["outcome"]["status"] == "admitted"
            else ("received", "failed")
        )
        return {
            "receipts": [
                dict(
                    run_id=req.run_id,
                    command_id=req.command_id,
                    provider_id=provider._config.provider_id,
                    phase=phase,
                    time=req.issued_at.model_dump(mode="json"),
                )
                for phase in phases
            ],
            "parcel_action_record": record,
        }

    provider._request = transport
    return provider


def test_provider_retry_after_tick_advance_reaches_authoritative_dedup(package):
    """Actual provider method, in-memory transport; no service launch claimed."""
    rpc = ready(package)
    calls = []
    provider = _provider_with_in_memory_transport(rpc, calls)
    request = r.request(3)
    first = asyncio.run(provider.handle_command(request))
    assert first.receipts[-1].phase == "completed"

    rpc.ingest_closed_stage(r.batch(package, 4, moving=True))
    provider._last_time = h._dwell_tick(4)
    before = rpc.machine.snapshot()
    again = asyncio.run(provider.handle_command(request))
    assert again == first
    assert rpc.machine.snapshot() == before
    assert rpc.projection().state == "in_transit"
    assert len(calls) == 2
    assert len(rpc.machine.parcel_state.transfers) == 1


def test_first_seen_stale_request_at_new_tick_is_rejected_without_state_mutation(
    package,
):
    """A first-seen request issued at tick3, first presented after the
    authoritative time advanced to tick4, is refused without mutation."""
    rpc = ready(package)
    calls = []
    provider = _provider_with_in_memory_transport(rpc, calls)
    rpc.ingest_closed_stage(r.batch(package, 4, moving=True))
    provider._last_time = h._dwell_tick(4)
    before = rpc.machine.snapshot()
    stale = r.request(3, command_id="action.pickup.stale.1")
    # The gate forwards it; the authoritative RPC dedup is the layer that
    # refuses the first-seen stale request.
    with pytest.raises(
        NativeParcelRuntimeError, match="issue time differs from current admission time"
    ):
        asyncio.run(provider.handle_command(stale))
    assert calls == ["command"]
    assert rpc.machine.snapshot() == before
    assert rpc.projection().state == "awaiting_pickup"
    assert len(rpc.machine.parcel_state.transfers) == 0


def test_processed_command_id_with_altered_payload_is_rejected_without_rollback(
    package,
):
    """Same command ID as the processed request with a different payload is
    refused by the authoritative dedup; state never rolls back or retransfers."""
    rpc = ready(package)
    calls = []
    provider = _provider_with_in_memory_transport(rpc, calls)
    request = r.request(3)
    first = asyncio.run(provider.handle_command(request))
    assert first.receipts[-1].phase == "completed"
    rpc.ingest_closed_stage(r.batch(package, 4, moving=True))
    provider._last_time = h._dwell_tick(4)
    before = rpc.machine.snapshot()
    altered = r.request(3, kind="dropoff")
    with pytest.raises(
        NativeParcelRuntimeError, match="reused with different request content"
    ):
        asyncio.run(provider.handle_command(altered))
    assert len(calls) == 2
    assert rpc.machine.snapshot() == before
    assert rpc.projection().state == "in_transit"
    assert len(rpc.machine.parcel_state.transfers) == 1
    decision_time = [
        record for record in rpc.actions
        if record["request"]["command_id"] == "action.pickup.1"
    ][0]["action"]["received_at"]
    assert decision_time == h._dwell_tick(3).model_dump()
