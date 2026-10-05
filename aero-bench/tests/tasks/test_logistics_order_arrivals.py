"""Focused module tests for the deterministic logistics order-arrival layer.

The layer under test (``aero_bench/tasks/logistics/order_arrivals.py``) is the
authoritative business simulation input that lets a future logistics business
Provider register canonical ``OrderRequest`` values during a run and gate their
availability against the explicit native simulation clock. These tests use only
clearly synthetic unit fixtures and never claim physical transport: evidence
references remain opaque external identifiers.

Coverage maps to the required invariants (this is a self-authored module test
file; it does not claim to be, or to replace, any external review):

* the canonical ``SimulationTime`` native clock (integer tick + sim_time_ns) is
  used on commands/events/snapshot/clock-advance with no float clock authority
  and no old float API alias; tick/ns mismatch, stale/future timestamps and
  exact nanosecond identities are enforced;
* the float-accumulation probe (``0.1 + 0.2`` vs literal ``0.3``) is closed by
  the integer nanosecond clock;
* the arrival journal is ONE causal append-only sequence (creation -> one-time
  release -> lifecycle markers); same-timestamp reordering, lifecycle before
  creation/availability, and release omission are rejected;
* snapshot construction / from_snapshot / replay_all enforce the sequence, the
  released-availability binding and the ledger pool order even when a ledger is
  supplied directly;
* the reserved package-baseline namespace is bound to the caller-declared
  authoritative ``initial_requests`` (required for restore/replay, ``()`` when
  none) and can never be claimed by a live command or actor grant, even with
  recomputed journal hashes;
* canonical hub / catalogue / payload / grant validation, atomic rejection,
  deterministic release ordering, no physical-proof claim and no random default
  are preserved.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import (
    FacilityCatalogue,
    compile_authored_facilities,
)
from aero_bench.tasks.logistics.fleet import (
    Fleet,
    compile_authored_fleet,
)
from aero_bench.tasks.logistics.order_arrivals import (
    ORDER_INTRODUCING_ROLE,
    ORDER_PACKAGE_ACTOR_ID,
    ORDER_PACKAGE_SOURCE,
    OrderArrivalCommand,
    OrderArrivalEvent,
    OrderArrivalService,
    OrderArrivalSnapshot,
    replay_order_arrivals,
    validate_order_for_arrival,
)
from aero_bench.tasks.logistics.orders import (
    LogisticsLedgerState,
    LogisticsOrdersService,
    OrderActorGrant,
    OrderRequest,
    OrderState,
    OrderTransition,
    replay_logistics_history,
)

FACILITY_ORIGIN = "facility.tianhe"
FACILITY_DESTINATION = "facility.liede"
FACILITY_HUB = "facility.hub.cargo"

#: The canonical native clock has no float alias; these helpers build explicit
#: integer (tick, sim_time_ns) instants.
ZERO = SimulationTime(tick=0, sim_time_ns=0)


def _sec(seconds: float) -> SimulationTime:
    """A consistent native instant whose derived float seconds equal ``seconds``."""
    return SimulationTime(
        tick=int(seconds),
        sim_time_ns=int(round(seconds * 1_000_000_000)),
    )


def _native_seconds(time: SimulationTime) -> float:
    """Mirror the layer's domain derivation: sim_time_ns / 1e9."""
    return time.sim_time_ns / 1_000_000_000


#: Full current frontend v3 facility capability records lifted to the canonical
#: FacilityCatalogue by compile_authored_facilities (synthetic unit fixture).
FULL_V3_FACILITIES = [
    {
        "id": FACILITY_ORIGIN,
        "name": "tianhe vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 0, "z": 0},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    },
    {
        "id": FACILITY_DESTINATION,
        "name": "liede vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 0, "z": 200},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": None,
        "charging": None,
    },
    {
        "id": FACILITY_HUB,
        "name": "cargo hub",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 200, "z": 0},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 1000, "throughputPerHourKg": 500},
        "charging": None,
    },
]

FLEET_VALUES = [
    {
        "id": "fleet.air",
        "assetId": "model:aircraft.a",
        "count": 2,
        "homeFacilityId": FACILITY_ORIGIN,
        "batteryWh": 1200,
        "reserveRatio": 0.2,
        "maxPayloadKg": 50,
    },
]

RUN_ACTORS = (
    OrderActorGrant(actor_id="business", role="business"),
    OrderActorGrant(actor_id="dispatcher", role="dispatcher"),
    OrderActorGrant(actor_id="fleet.air:1", role="aircraft_agent"),
    OrderActorGrant(actor_id="fleet.air:2", role="aircraft_agent"),
)


def _catalogue() -> FacilityCatalogue:
    return compile_authored_facilities(FULL_V3_FACILITIES)


def _fleet(catalogue: FacilityCatalogue) -> Fleet:
    return compile_authored_fleet(FLEET_VALUES, catalogue)


def _request(
    order_id: str = "order.1",
    *,
    origin: str = FACILITY_ORIGIN,
    destination: str = FACILITY_DESTINATION,
    hub: str = FACILITY_HUB,
    cargo_kg: float = 12.0,
    release_time_s: float = 0.0,
    deadline_s: float = 3600.0,
) -> OrderRequest:
    return OrderRequest(
        order_id=order_id,
        origin_facility_id=origin,
        destination_facility_id=destination,
        hub_handoff_facility_id=hub,
        cargo_mass_kg=cargo_kg,
        release_time_s=release_time_s,
        deadline_s=deadline_s,
    )


def _service(
    *requests: OrderRequest,
    catalogue: FacilityCatalogue | None = None,
    fleet: Fleet | None = None,
    initial_time: SimulationTime = ZERO,
    actor_grants: tuple[OrderActorGrant, ...] = RUN_ACTORS,
) -> OrderArrivalService:
    catalogue = catalogue if catalogue is not None else _catalogue()
    fleet = fleet if fleet is not None else _fleet(catalogue)
    return OrderArrivalService(
        catalogue=catalogue,
        fleet=fleet,
        actor_grants=actor_grants,
        initial_requests=requests,
        initial_time=initial_time,
    )


def _command(
    command_id: str,
    *,
    order: OrderRequest,
    actor_id: str = "business",
    actor_role: str = "business",
    time: SimulationTime = ZERO,
    source_identity: str = "seed.program.7",
) -> OrderArrivalCommand:
    return OrderArrivalCommand(
        command_id=command_id,
        actor_id=actor_id,
        actor_role=actor_role,  # type: ignore[arg-type]
        time=time,
        source_identity=source_identity,
        order=order,
    )


def _offer(order_id: str, transition_id: str, time: SimulationTime) -> OrderTransition:
    return OrderTransition(
        order_id=order_id,
        transition_id=transition_id,
        event="offer",
        actor_id="dispatcher",
        actor_role="dispatcher",
        time_s=_native_seconds(time),
        expected_version=0,
        assignee_id="fleet.air:1",
    )


def _delivery_path(
    order_id: str,
    time: SimulationTime,
    *transition_ids: str,
) -> tuple[OrderTransition, ...]:
    """Offer/accept/assign/pick_up/handoff/deliver at one explicit native instant."""
    assert len(transition_ids) == 6
    offer_id, accept_id, assign_id, pick_id, handoff_id, deliver_id = transition_ids
    seconds = _native_seconds(time)
    return (
        OrderTransition(
            order_id=order_id, transition_id=offer_id, event="offer",
            actor_id="dispatcher", actor_role="dispatcher", time_s=seconds,
            expected_version=0, assignee_id="fleet.air:1",
        ),
        OrderTransition(
            order_id=order_id, transition_id=accept_id, event="accept",
            actor_id="fleet.air:1", actor_role="aircraft_agent", time_s=seconds,
            expected_version=1, assignee_id="fleet.air:1",
        ),
        OrderTransition(
            order_id=order_id, transition_id=assign_id, event="assign",
            actor_id="dispatcher", actor_role="dispatcher", time_s=seconds,
            expected_version=2, assignee_id="fleet.air:1", capacity_kg=50.0,
        ),
        OrderTransition(
            order_id=order_id, transition_id=pick_id, event="pick_up",
            actor_id="fleet.air:1", actor_role="aircraft_agent", time_s=seconds,
            expected_version=3, assignee_id="fleet.air:1", evidence_ref="evidence.pick",
        ),
        OrderTransition(
            order_id=order_id, transition_id=handoff_id, event="handoff",
            actor_id="fleet.air:1", actor_role="aircraft_agent", time_s=seconds,
            expected_version=4, assignee_id="fleet.air:1", evidence_ref="evidence.handoff",
            handoff_facility_id=FACILITY_HUB,
        ),
        OrderTransition(
            order_id=order_id, transition_id=deliver_id, event="deliver",
            actor_id="fleet.air:1", actor_role="aircraft_agent", time_s=seconds,
            expected_version=5, assignee_id="fleet.air:1", evidence_ref="evidence.deliver",
        ),
    )


def _rehash_append(
    events: tuple[OrderArrivalEvent, ...],
    *,
    kind: str,
    event_id: str,
    order_id: str,
    time: SimulationTime,
    order: OrderRequest | None = None,
    source_identity: str | None = None,
    actor_id: str | None = None,
    lifecycle_record=None,
) -> OrderArrivalEvent:
    """Rebuild one arrival-journal record with a recomputed hash chain.

    This intentionally mirrors the layer's canonical journal hash body so the
    semantic probes below prove that causality / baseline / release checks hold
    *even when* the SHA-256 chain itself is recomputed to be valid.
    """
    previous_hash = events[-1].record_hash if events else "0" * 64
    sequence = len(events)
    record_hash = hashlib.sha256(
        canonical_json_bytes(
            {
                "sequence": sequence,
                "kind": kind,
                "event_id": event_id,
                "order_id": order_id,
                "time": time.model_dump(mode="json"),
                "order": None if order is None else order.model_dump(mode="json"),
                "source_identity": source_identity,
                "actor_id": actor_id,
                "lifecycle_record": (
                    None if lifecycle_record is None else lifecycle_record.model_dump(mode="json")
                ),
                "previous_hash": previous_hash,
            }
        )
    ).hexdigest()
    return OrderArrivalEvent(
        sequence=sequence,
        kind=kind,  # type: ignore[arg-type]
        event_id=event_id,
        order_id=order_id,
        time=time,
        order=order,
        source_identity=source_identity,
        actor_id=actor_id,
        lifecycle_record=lifecycle_record,
        previous_hash=previous_hash,
        record_hash=record_hash,
    )


# ---------------------------------------------------------------------------
# Release gate: hidden until due, refused before due, exact one-time boundary.
# ---------------------------------------------------------------------------


def test_future_release_hidden_from_available_view_and_offer_refused() -> None:
    service = _service(_request("order.1", release_time_s=100.0))
    assert service.current_time == ZERO
    assert service.registered_order_ids() == ("order.1",)
    assert service.released_order_ids() == ()
    assert service.available_orders() == ()

    service.advance_time(_sec(50.0))
    assert service.released_order_ids() == ()
    assert service.available_orders() == ()

    with pytest.raises(ValueError, match="not yet available"):
        service.apply_lifecycle(_offer("order.1", "transition.offer.1", _sec(50.0)))

    released = service.advance_time(_sec(100.0))
    assert [event.event_id for event in released] == ["arrival.release:order.1"]
    assert service.released_order_ids() == ("order.1",)
    assert [state.order_id for state in service.available_orders()] == ["order.1"]

    result = service.apply_lifecycle(_offer("order.1", "transition.offer.1", _sec(100.0)))
    assert result.order.status.value == "offered"


def test_release_boundary_exactly_once() -> None:
    service = _service(_request("order.1", release_time_s=100.0))
    service.advance_time(_sec(99.0))
    assert service.released_order_ids() == ()

    first = service.advance_time(_sec(100.0))
    second = service.advance_time(_sec(100.0))
    later = service.advance_time(_sec(500.0))
    assert len(first) == 1
    assert second == ()
    assert later == ()
    release_events = [
        event for event in service.snapshot.events if event.kind == "released"
    ]
    assert len(release_events) == 1
    assert release_events[0].order_id == "order.1"
    assert release_events[0].time == _sec(100.0)
    assert service.released_order_ids() == ("order.1",)


def test_deterministic_due_release_ordering_at_one_tick() -> None:
    service = _service(
        _request("order.b", release_time_s=100.0),
        _request("order.a", release_time_s=100.0),
        _request("order.c", release_time_s=200.0),
    )
    released = service.advance_time(_sec(100.0))
    # Registration order, not alphabetical order: order.b then order.a.
    assert [event.order_id for event in released] == ["order.b", "order.a"]
    assert service.released_order_ids() == ("order.b", "order.a")


def test_float_binary_fraction_gate_never_releases_before_declared_seconds() -> None:
    release_s = 0.1 + 0.2  # 0.30000000000000004
    service = _service(_request("order.gate", release_time_s=release_s))

    # Canonical seconds comparison: the round-tripped 0.3s instant is strictly
    # before the declared domain release, and the first guarded nanosecond
    # instant is at/after it -- so 300000000ns must refuse and 300000001ns
    # must be the exact one-time release boundary.
    assert 300_000_000 / 1_000_000_000 < release_s
    assert 300_000_001 / 1_000_000_000 >= release_s

    before_gate = SimulationTime(tick=0, sim_time_ns=300_000_000)
    service.advance_time(before_gate)
    assert service.released_order_ids() == ()
    assert service.available_orders() == ()
    with pytest.raises(ValueError, match="not yet available"):
        service.apply_lifecycle(
            _offer("order.gate", "transition.offer.300m", before_gate)
        )

    at_gate = SimulationTime(tick=0, sim_time_ns=300_000_001)
    released = service.advance_time(at_gate)
    assert [event.event_id for event in released] == ["arrival.release:order.gate"]
    assert service.released_order_ids() == ("order.gate",)
    assert [state.order_id for state in service.available_orders()] == ["order.gate"]

    # The released order accepts a domain transition at the exact guarded native
    # instant (0.300000001s), which the canonical lifecycle accepts because it
    # is at/after the declared 0.30000000000000004s release.
    result = service.apply_lifecycle(
        _offer("order.gate", "transition.offer.300m1", at_gate)
    )
    assert result.order.status.value == "offered"


def test_positive_subnanosecond_release_never_available_at_zero() -> None:
    # 1e-10 s is 0.1 ns -- a positive sub-nanosecond declared release.  The
    # conservative ceiling binds it to the first positive nanosecond instant, so
    # the order is never available at the zero native clock.
    service = _service(_request("order.subns", release_time_s=1e-10))
    assert service.current_time == ZERO
    assert service.released_order_ids() == ()
    assert service.available_orders() == ()
    with pytest.raises(ValueError, match="not yet available"):
        service.apply_lifecycle(_offer("order.subns", "transition.offer.0", ZERO))

    released = service.advance_time(SimulationTime(tick=0, sim_time_ns=1))
    assert [event.event_id for event in released] == ["arrival.release:order.subns"]
    assert service.released_order_ids() == ("order.subns",)


def test_exact_0_3_seconds_gate_release_boundary_preserved() -> None:
    service = _service(_request("order.three", release_time_s=0.3))
    service.advance_time(SimulationTime(tick=0, sim_time_ns=299_999_999))
    assert service.released_order_ids() == ()

    released = service.advance_time(SimulationTime(tick=0, sim_time_ns=300_000_000))
    assert [event.event_id for event in released] == ["arrival.release:order.three"]
    assert service.released_order_ids() == ("order.three",)
    result = service.apply_lifecycle(
        _offer(
            "order.three",
            "transition.offer.300m",
            SimulationTime(tick=0, sim_time_ns=300_000_000),
        )
    )
    assert result.order.status.value == "offered"


# ---------------------------------------------------------------------------
# Native clock: exact tick/ns identity, no float accumulation, no float alias.
# ---------------------------------------------------------------------------


def test_native_clock_commands_bind_exact_tick_and_nanoseconds() -> None:
    service = _service()
    current = SimulationTime(tick=3, sim_time_ns=333_333_333)
    service.advance_time(current)

    # Exact tick+ns identity is accepted.
    order = _request("order.tick", release_time_s=_native_seconds(current))
    event = service.submit(_command("command.tick", order=order, time=current))
    assert event.time == current
    recorded = next(e for e in service.snapshot.events if e.kind == "created")
    assert recorded.time == SimulationTime(tick=3, sim_time_ns=333_333_333)

    # Same tick, off-by-one integer nanosecond -> mismatch.
    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.tick.ns",
                order=_request("order.tick.ns", release_time_s=0.0),
                time=SimulationTime(tick=3, sim_time_ns=333_333_334),
            )
        )
    # Identical nanoseconds, different tick -> tick mismatch.
    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.tick.tick",
                order=_request("order.tick.tick", release_time_s=0.0),
                time=SimulationTime(tick=2, sim_time_ns=333_333_333),
            )
        )
    # Stale (earlier) and future (later) native timestamps are rejected.
    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.stale",
                order=_request("order.stale", release_time_s=0.0),
                time=SimulationTime(tick=2, sim_time_ns=200_000_000),
            )
        )
    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.future",
                order=_request("order.future", release_time_s=0.0),
                time=SimulationTime(tick=4, sim_time_ns=400_000_000),
            )
        )


def test_float_accumulation_loss_replaced_by_native_clock() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    # The old float clock accumulated 0.1 then 0.1+0.2 into
    # 0.30000000000000004 and rejected a literal 0.3 command.  The native clock
    # is an explicit integer nanosecond instant, so command binding is exact.
    service.advance_time(SimulationTime(tick=1, sim_time_ns=100_000_000))
    service.advance_time(SimulationTime(tick=1, sim_time_ns=300_000_000))

    event = service.submit(
        _command(
            "command.online",
            order=_request("order.online", release_time_s=0.3),
            time=SimulationTime(tick=1, sim_time_ns=300_000_000),
        )
    )
    assert event.time == SimulationTime(tick=1, sim_time_ns=300_000_000)
    # An off-by-one nanosecond physical instant is a different harness time.
    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.offbyone",
                order=_request("order.offbyone", release_time_s=0.3),
                time=SimulationTime(tick=1, sim_time_ns=300_000_001),
            )
        )


def test_no_old_float_time_api_alias() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    # No float clock property remains on the service or the snapshot.
    assert not hasattr(service, "current_time_s")
    assert not hasattr(service.snapshot, "current_time_s")
    # The clock advance API takes a SimulationTime, never a float.
    with pytest.raises(TypeError):
        service.advance_time(0.5)  # type: ignore[arg-type]
    # The service constructor takes a native initial_time, never a float.
    with pytest.raises(TypeError):
        OrderArrivalService(
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=RUN_ACTORS,
            initial_requests=(_request("order.1", release_time_s=0.0),),
            initial_time_s=0.0,  # type: ignore[call-arg]
        )
    # The command model carries native time: a float or legacy ``at_s`` field is
    # rejected at the strict model boundary.
    with pytest.raises(ValidationError):
        OrderArrivalCommand(
            command_id="command.float",
            actor_id="business",
            actor_role="business",
            at_s=0.5,  # type: ignore[arg-type]
            source_identity="seed",
            order=_request("order.float", release_time_s=0.0),
        )
    with pytest.raises((TypeError, ValidationError)):
        _command("command.time.float", order=_request("order.x", release_time_s=0.0), time=0.5)  # type: ignore[arg-type]
    # Replay requires a native current_time, not a float ``current_time_s``.
    with pytest.raises(TypeError):
        replay_order_arrivals(  # type: ignore[call-arg]
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=RUN_ACTORS,
            initial_requests=(),
            initial_time=ZERO,
            events=(),
            current_time_s=0.0,
        )


def test_replay_requires_explicit_initial_requests() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    snap = service.snapshot
    with pytest.raises(TypeError):
        replay_order_arrivals(  # type: ignore[call-arg]
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=service.actor_grants,
            events=snap.events,
            current_time=snap.time,
            initial_time=ZERO,
        )
    with pytest.raises(TypeError):
        OrderArrivalService.from_snapshot(  # type: ignore[call-arg]
            snapshot=snap,
            initial_time=ZERO,
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=service.actor_grants,
        )
    with pytest.raises(TypeError):
        OrderArrivalService(  # type: ignore[call-arg]
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=RUN_ACTORS,
        )


def test_state_digest_includes_native_clock() -> None:
    service_a = _service(_request("order.1", release_time_s=0.0), initial_time=ZERO)
    service_b = _service(
        _request("order.1", release_time_s=0.0),
        initial_time=SimulationTime(tick=1, sim_time_ns=0),
    )
    assert service_a.snapshot.canonical_digest() != service_b.snapshot.canonical_digest()
    before = service_a.digest
    service_a.advance_time(SimulationTime(tick=1, sim_time_ns=1_000_000_000))
    assert service_a.digest != before


def test_backdated_and_future_dated_commands_rejected() -> None:
    service = _service(_request("order.1"))
    current = _sec(100.0)
    service.advance_time(current)

    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.backdated",
                order=_request("order.backdated", release_time_s=100.0),
                time=_sec(50.0),
            )
        )
    with pytest.raises(ValueError, match="exactly the current simulation time"):
        service.submit(
            _command(
                "command.futuredated",
                order=_request("order.futuredated", release_time_s=150.0),
                time=_sec(150.0),
            )
        )
    with pytest.raises(ValueError, match="moved backwards"):
        service.advance_time(_sec(50.0))

    # A negative native clock is rejected at the canonical model boundary.
    with pytest.raises(ValidationError):
        SimulationTime(tick=-1, sim_time_ns=0)
    with pytest.raises(ValidationError):
        SimulationTime(tick=0, sim_time_ns=-1)


def test_native_clock_rejects_regression_in_either_component() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    service.advance_time(SimulationTime(tick=1, sim_time_ns=200_000_000))

    # A later tick with an earlier nanosecond regresses the nanosecond
    # component: the clock never moved to a valid later native instant.
    with pytest.raises(ValueError, match="moved backwards"):
        service.advance_time(SimulationTime(tick=2, sim_time_ns=100_000_000))
    # Same tick but an earlier nanosecond regresses.
    with pytest.raises(ValueError, match="moved backwards"):
        service.advance_time(SimulationTime(tick=1, sim_time_ns=199_000_000))
    # An earlier tick with a higher nanosecond regresses the tick component.
    with pytest.raises(ValueError, match="moved backwards"):
        service.advance_time(SimulationTime(tick=0, sim_time_ns=300_000_000))

    # Same-time no-op remains allowed; both-components-forward is allowed.
    assert service.advance_time(SimulationTime(tick=1, sim_time_ns=200_000_000)) == ()
    service.advance_time(SimulationTime(tick=2, sim_time_ns=250_000_000))
    assert service.current_time == SimulationTime(tick=2, sim_time_ns=250_000_000)


def test_online_submission_schedules_future_release_only_explicitly() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    assert service.released_order_ids() == ("order.1",)
    current = _sec(100.0)
    service.advance_time(current)

    command = _command(
        "command.scheduled",
        order=_request("order.scheduled", release_time_s=200.0),
        time=current,
        source_identity="seed.program.9",
    )
    service.submit(command)
    assert "order.scheduled" in service.registered_order_ids()
    # order.scheduled is registered but its future release gate keeps it hidden
    # until the sim clock reaches 200.0.
    assert service.released_order_ids() == ("order.1",)
    assert [state.order_id for state in service.available_orders()] == ["order.1"]
    assert service.advance_time(_sec(200.0))[0].order_id == "order.scheduled"
    assert service.released_order_ids() == ("order.1", "order.scheduled")

    # A retrospective availability timestamp is rejected: release < submission.
    with pytest.raises(ValueError, match="retrospective availability"):
        service.submit(
            _command(
                "command.backdated.avail",
                order=_request("order.backdated.avail", release_time_s=50.0),
                time=_sec(200.0),
            )
        )


# ---------------------------------------------------------------------------
# Dynamic arrival while preserving the prior canonical lifecycle history.
# ---------------------------------------------------------------------------


def test_dynamic_valid_arrival_keeps_prior_history_identical() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    current = service.current_time
    for transition in _delivery_path(
        "order.1", current, "t.offer", "t.accept", "t.assign", "t.pick", "t.handoff", "t.deliver"
    ):
        service.apply_lifecycle(transition)
    before = service.snapshot
    assert before.ledger.version == 6

    service.submit(
        _command(
            command_id="command.online.1",
            order=_request("order.online.1", release_time_s=0.0),
            time=current,
            source_identity="seed.program.7",
        )
    )
    after = service.snapshot
    # The online arrival appended the order and released it at the same tick,
    # but every previously accepted canonical lifecycle record is byte-identical.
    assert after.ledger.history == before.ledger.history
    assert after.ledger.processed_transition_ids == before.ledger.processed_transition_ids
    before_states = {state.order_id: state for state in before.ledger.orders}
    after_states = {state.order_id: state for state in after.ledger.orders}
    assert after_states["order.1"] == before_states["order.1"]
    assert after_states["order.online.1"].status.value == "created"
    assert [state.order_id for state in service.available_orders()] == [
        "order.1",
        "order.online.1",
    ]


# ---------------------------------------------------------------------------
# Canonical validation negatives: mandatory hub, payload, unknown facilities.
# ---------------------------------------------------------------------------


def test_mandatory_hub_and_payload_and_unknown_facility_negatives() -> None:
    service = _service(_request("order.1"))

    # Vertiport-to-Vertiport goods bypass: neither endpoint is a hub and no hub
    # is declared, so the canonical hub routing rejects it.
    bypass = _request(
        "order.bypass",
        hub=FACILITY_ORIGIN,
        release_time_s=0.0,
    )
    with pytest.raises(ValueError, match="lacks a hub handoff"):
        service.submit(_command("command.bypass", order=bypass, time=ZERO))

    heavy = _request("order.heavy", cargo_kg=500.0, release_time_s=0.0)
    with pytest.raises(ValueError, match="sufficient payload"):
        service.submit(_command("command.heavy", order=heavy, time=ZERO))

    ghost = _request(
        "order.ghost",
        origin="facility.ghost.unknown",
        release_time_s=0.0,
    )
    with pytest.raises(ValueError, match="unknown source facility"):
        service.submit(_command("command.ghost", order=ghost, time=ZERO))

    valid = _request("order.valid", release_time_s=0.0)
    invalid_payload = _request(
        "order.invalid.payload", cargo_kg=500.0, release_time_s=0.0
    )
    validate_order_for_arrival(service.catalogue, service.fleet, valid)
    with pytest.raises(ValueError, match="sufficient payload"):
        validate_order_for_arrival(service.catalogue, service.fleet, invalid_payload)


# ---------------------------------------------------------------------------
# Duplicate ids / role forgery: reject before any partial state change.
# ---------------------------------------------------------------------------


def test_duplicate_command_and_order_ids_rejected() -> None:
    service = _service(_request("order.1"))
    command = _command(
        "command.online.1",
        order=_request("order.online.1", release_time_s=0.0),
        time=ZERO,
    )
    service.submit(command)
    before = service.digest

    with pytest.raises(ValueError, match="already processed"):
        service.submit(
            _command(
                "command.online.1",
                order=_request("order.another", release_time_s=0.0),
                time=ZERO,
            )
        )
    with pytest.raises(ValueError, match="already registered"):
        service.submit(
            _command(
                "command.online.2",
                order=_request("order.online.1", release_time_s=0.0),
                time=ZERO,
            )
        )
    # Rejected submits must not mutate the snapshot digest (no partial state).
    assert service.digest == before
    assert service.registered_order_ids() == ("order.1", "order.online.1")

    with pytest.raises(ValueError, match="duplicate"):
        _service(_request("order.1"), _request("order.1"))

    for transition in _delivery_path(
        "order.1", ZERO, "t.offer", "t.accept", "t.assign", "t.pick", "t.handoff", "t.deliver"
    ):
        service.apply_lifecycle(transition)
    with pytest.raises(ValueError, match="already processed"):
        service.apply_lifecycle(_offer("order.1", "t.offer", time=ZERO))


def test_role_forgery_rejected_against_canonical_grants() -> None:
    service = _service(_request("order.1"))

    with pytest.raises(ValueError, match="does not match"):
        service.submit(
            _command(
                "command.forge.1",
                order=_request("order.forge.1", release_time_s=0.0),
                actor_id="dispatcher",
                actor_role="business",
                time=ZERO,
            )
        )
    with pytest.raises(ValueError, match="not authorized"):
        service.submit(
            _command(
                "command.forge.2",
                order=_request("order.forge.2", release_time_s=0.0),
                actor_id="dispatcher",
                actor_role="dispatcher",
                time=ZERO,
            )
        )
    with pytest.raises(ValueError, match="not authorized"):
        service.submit(
            _command(
                "command.forge.3",
                order=_request("order.forge.3", release_time_s=0.0),
                actor_id="fleet.air:1",
                actor_role="aircraft_agent",
                time=ZERO,
            )
        )
    with pytest.raises(ValueError, match="unknown actor"):
        service.submit(
            _command(
                "command.forge.4",
                order=_request("order.forge.4", release_time_s=0.0),
                actor_id="ghost",
                actor_role="business",
                time=ZERO,
            )
        )
    event = service.submit(
        _command(
            "command.legit",
            order=_request("order.legit", release_time_s=0.0),
            actor_id="business",
            actor_role=ORDER_INTRODUCING_ROLE,
            time=ZERO,
        )
    )
    assert event.actor_id == "business"
    # Rejected submissions above left no partial state behind.
    assert service.registered_order_ids() == ("order.1", "order.legit")


# ---------------------------------------------------------------------------
# Reserved package-baseline namespace: bound to declared initial_requests only.
# ---------------------------------------------------------------------------


def test_reserved_baseline_namespace_cannot_be_claimed_live() -> None:
    # A grant set claiming the reserved baseline actor namespace is rejected,
    # even when it claims the business role.
    forged_grants = RUN_ACTORS + (
        OrderActorGrant(actor_id=ORDER_PACKAGE_ACTOR_ID, role="business"),
    )
    with pytest.raises(ValueError, match="reserved baseline actor"):
        _service(actor_grants=forged_grants)

    service = _service(_request("order.1"))
    # Live actor cannot claim the reserved baseline actor id.
    with pytest.raises(ValueError, match="reserved baseline actor"):
        service.submit(
            _command(
                "command.baseline.actor",
                order=_request("order.baseline.actor", release_time_s=0.0),
                actor_id=ORDER_PACKAGE_ACTOR_ID,
                actor_role=ORDER_INTRODUCING_ROLE,
                time=ZERO,
            )
        )
    # Live source cannot claim the reserved package source verbatim.
    with pytest.raises(ValueError, match="reserved baseline source"):
        service.submit(
            _command(
                "command.baseline.source",
                order=_request("order.baseline.source", release_time_s=0.0),
                source_identity=ORDER_PACKAGE_SOURCE,
                time=ZERO,
            )
        )
    # Live command ids cannot use the reserved arrival event namespace.
    for prefix in ("arrival.package:", "arrival.release:", "arrival.lifecycle:"):
        with pytest.raises(ValueError, match="reserved arrival event namespace"):
            service.submit(
                _command(
                    f"{prefix}order.bad",
                    order=_request("order.bad.prefix", release_time_s=0.0),
                    time=ZERO,
                )
            )
    assert service.registered_order_ids() == ("order.1",)


def test_baseline_binding_to_declared_initial_requests() -> None:
    req1 = _request("order.1", release_time_s=0.0)
    req2 = _request("order.2", release_time_s=10.0)
    service = _service(req1, req2)
    service.advance_time(_sec(10.0))
    snap = service.snapshot

    # Baseline-only journal: restores/replays with an empty grant set because
    # baseline records are authoring input bound to initial_requests, not actors.
    clone = OrderArrivalService.from_snapshot(
        snapshot=snap,
        initial_requests=(req1, req2),
        initial_time=ZERO,
        catalogue=service.catalogue,
        fleet=service.fleet,
        actor_grants=(),
    )
    assert clone.snapshot == snap
    replayed = replay_order_arrivals(
        catalogue=service.catalogue,
        fleet=service.fleet,
        actor_grants=(),
        initial_requests=(req1, req2),
        initial_time=ZERO,
        events=snap.events,
        current_time=snap.time,
    )
    assert replayed.canonical_digest() == snap.canonical_digest()

    # Declaring fewer baseline requests mismatches the journal count/content.
    with pytest.raises(ValueError, match="baseline"):
        OrderArrivalService.from_snapshot(
            snapshot=snap,
            initial_requests=(req1,),
            initial_time=ZERO,
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=(),
        )
    # Declaring a different initial native time mismatches the baseline records.
    with pytest.raises(ValueError, match="initial simulation time"):
        OrderArrivalService.from_snapshot(
            snapshot=snap,
            initial_requests=(req1, req2),
            initial_time=_sec(1.0),
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=(),
        )

    # An online created event in the journal is NOT baseline authoring input:
    # replay without a business grant must reject it.
    online = _service(_request("order.base", release_time_s=0.0))
    online.submit(
        _command(
            "command.online",
            order=_request("order.online", release_time_s=0.0),
            actor_id="business",
            actor_role=ORDER_INTRODUCING_ROLE,
            time=ZERO,
        )
    )
    online_snap = online.snapshot
    with pytest.raises(ValueError, match="business grant"):
        replay_order_arrivals(
            catalogue=online.catalogue,
            fleet=online.fleet,
            actor_grants=(),
            initial_requests=(_request("order.base", release_time_s=0.0),),
            initial_time=ZERO,
            events=online_snap.events,
            current_time=online_snap.time,
        )


def test_forged_baseline_journal_with_valid_hashes_rejected() -> None:
    catalogue = _catalogue()
    fleet = _fleet(catalogue)
    forged = _request("order.forged", release_time_s=0.0)

    # Build a chain-valid journal whose created record claims the reserved
    # baseline actor / source / event-id namespace without any declared
    # initial_requests backing it.
    events: tuple[OrderArrivalEvent, ...] = ()
    events += (
        _rehash_append(
            events,
            kind="created",
            event_id=f"arrival.package:{forged.order_id}",
            order_id=forged.order_id,
            time=ZERO,
            order=forged,
            source_identity=ORDER_PACKAGE_SOURCE,
            actor_id=ORDER_PACKAGE_ACTOR_ID,
        ),
    )
    events += (
        _rehash_append(
            events,
            kind="released",
            event_id=f"arrival.release:{forged.order_id}",
            order_id=forged.order_id,
            time=ZERO,
        ),
    )
    # The journal itself is hash-bound and causally coherent...
    ledger = replay_logistics_history((forged,), actor_grants=RUN_ACTORS, history=())
    forged_snapshot = OrderArrivalSnapshot(
        time=ZERO, version=len(events), events=events, ledger=ledger
    )
    # ...but replay/restore bind the reserved namespace to the authoritative
    # initial_requests and must reject the forged baseline record even though
    # every SHA-256 in the journal was recomputed to be valid.
    with pytest.raises(ValueError, match="baseline"):
        replay_order_arrivals(
            catalogue=catalogue,
            fleet=fleet,
            actor_grants=RUN_ACTORS,
            initial_requests=(),
            initial_time=ZERO,
            events=events,
            current_time=ZERO,
        )
    with pytest.raises(ValueError, match="baseline"):
        OrderArrivalService.from_snapshot(
            snapshot=forged_snapshot,
            initial_requests=(),
            initial_time=ZERO,
            catalogue=catalogue,
            fleet=fleet,
            actor_grants=RUN_ACTORS,
        )


# ---------------------------------------------------------------------------
# Single causal journal: lifecycle binding, same-time causality, omission,
# supplied-ledger bypass.
# ---------------------------------------------------------------------------


def test_lifecycle_must_bind_to_current_harness_time_not_earlier() -> None:
    service = _service()
    current = _sec(100.0)
    service.advance_time(current)
    service.submit(
        _command(
            "command.online",
            order=_request("order.online", release_time_s=100.0),
            time=current,
            actor_id="business",
            actor_role=ORDER_INTRODUCING_ROLE,
        )
    )
    assert "order.online" in service.released_order_ids()

    # The order is released, but an offer at an EARLIER float number (50s) does
    # not bind exactly to the current harness time and is rejected.
    with pytest.raises(ValueError, match="bind exactly"):
        service.apply_lifecycle(_offer("order.online", "t.offer.50", _sec(50.0)))

    # The transition exactly at the current native instant is accepted.
    result = service.apply_lifecycle(_offer("order.online", "t.offer.100", current))
    assert result.order.status.value == "offered"


def test_replay_rejects_lifecycle_marker_before_release_or_creation() -> None:
    service = _service(_request("order.legit", release_time_s=0.0))
    result = service.apply_lifecycle(_offer("order.legit", "t.offer.0", ZERO))
    record = result.history_record
    created = next(e for e in service.snapshot.events if e.kind == "created")

    # Build a forged journal whose lifecycle marker is sequenced BEFORE the
    # order's one-time release record (same native instant), with a recomputed
    # hash chain so only the journal stage-order check remains to reject it.
    events: tuple[OrderArrivalEvent, ...] = ()
    events += (
        _rehash_append(
            events,
            kind="created",
            event_id=created.event_id,
            order_id=created.order_id,
            time=ZERO,
            order=created.order,
            source_identity=created.source_identity,
            actor_id=created.actor_id,
        ),
    )
    events += (
        _rehash_append(
            events,
            kind="lifecycle",
            event_id="arrival.lifecycle:t.offer.0",
            order_id="order.legit",
            time=ZERO,
            lifecycle_record=record,
        ),
    )
    events += (
        _rehash_append(
            events,
            kind="released",
            event_id="arrival.release:order.legit",
            order_id="order.legit",
            time=ZERO,
        ),
    )
    ledger = replay_logistics_history(
        (created.order,), actor_grants=RUN_ACTORS, history=(record,)
    )
    with pytest.raises(ValueError, match="precedes the one-time release"):
        OrderArrivalSnapshot(
            time=ZERO, version=len(events), events=events, ledger=ledger
        )


def test_same_time_reordered_lifecycle_marker_rejected() -> None:
    order = _request("order.same", release_time_s=0.0)
    service = _service(order)
    result = service.apply_lifecycle(_offer("order.same", "t.offer.0", ZERO))
    record = result.history_record

    # Same native instant, but the lifecycle marker is sequenced BEFORE the
    # created record; all hashes are recomputed so only the stage-order check
    # can reject it.
    events: tuple[OrderArrivalEvent, ...] = ()
    events += (
        _rehash_append(
            events,
            kind="lifecycle",
            event_id=f"arrival.lifecycle:t.offer.0",
            order_id="order.same",
            time=ZERO,
            lifecycle_record=record,
        ),
        _rehash_append(
            events,
            kind="created",
            event_id=f"arrival.package:order.same",
            order_id="order.same",
            time=ZERO,
            order=order,
            source_identity=ORDER_PACKAGE_SOURCE,
            actor_id=ORDER_PACKAGE_ACTOR_ID,
        ),
        _rehash_append(
            events,
            kind="released",
            event_id=f"arrival.release:order.same",
            order_id="order.same",
            time=ZERO,
        ),
    )
    ledger = replay_logistics_history(
        (order,), actor_grants=RUN_ACTORS, history=(record,)
    )
    with pytest.raises(ValueError, match="precedes the order creation"):
        OrderArrivalSnapshot(
            time=ZERO, version=3, events=events, ledger=ledger
        )


def test_release_omission_rejected_at_snapshot_level() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    snap = service.snapshot
    created = next(e for e in snap.events if e.kind == "created")

    # A chain-valid journal with the created order but no one-time release
    # record: the order is due at the current time, so the snapshot must reject
    # the silently-lost release.
    events: tuple[OrderArrivalEvent, ...] = ()
    events += (
        _rehash_append(
            events,
            kind="created",
            event_id=created.event_id,
            order_id=created.order_id,
            time=created.time,
            order=created.order,
            source_identity=created.source_identity,
            actor_id=created.actor_id,
        ),
    )
    ledger = replay_logistics_history(
        (created.order,), actor_grants=RUN_ACTORS, history=()
    )
    with pytest.raises(ValueError, match="no one-time release record"):
        OrderArrivalSnapshot(
            time=snap.time, version=len(events), events=events, ledger=ledger
        )


def test_supplied_ledger_pool_order_bypass_rejected() -> None:
    service = _service(_request("order.a", release_time_s=0.0), _request("order.b", release_time_s=0.0))
    snap = service.snapshot
    assert tuple(state.order_id for state in snap.ledger.orders) == (
        "order.a",
        "order.b",
    )
    # Hand-build a ledger whose pool order differs and supply it directly to the
    # snapshot constructor: the journal-vs-ledger pool binding must still hold.
    swapped = LogisticsLedgerState(
        orders=(snap.ledger.orders[1], snap.ledger.orders[0]),
        version=0,
        processed_transition_ids=(),
        history=(),
    )
    with pytest.raises(ValueError, match="does not follow the arrival journal creation order"):
        OrderArrivalSnapshot(
            time=snap.time, version=len(snap.events), events=snap.events, ledger=swapped
        )


# ---------------------------------------------------------------------------
# Deterministic replay / snapshot digest and journal tampering detection.
# ---------------------------------------------------------------------------


def test_replay_digest_equality_and_altered_or_reordered_journal_rejected() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    current = _sec(100.0)
    service.advance_time(current)
    service.submit(
        _command(
            "command.online.1",
            order=_request("order.online.1", release_time_s=100.0),
            time=current,
            source_identity="seed.program.7",
        )
    )
    for transition in _delivery_path(
        "order.1", current, "t.offer", "t.accept", "t.assign", "t.pick", "t.handoff", "t.deliver"
    ):
        service.apply_lifecycle(transition)

    snap = service.snapshot
    replayed = replay_order_arrivals(
        catalogue=service.catalogue,
        fleet=service.fleet,
        actor_grants=service.actor_grants,
        initial_requests=(_request("order.1", release_time_s=0.0),),
        initial_time=ZERO,
        events=snap.events,
        current_time=service.current_time,
    )
    assert replayed.canonical_digest() == snap.canonical_digest()
    assert replayed.events == snap.events
    assert replayed.ledger == snap.ledger

    # Altered arrival content (cargo mass lifted beyond payload) fails on the
    # journal chain and canonical validation.
    created = next(e for e in snap.events if e.kind == "created")
    altered_order = created.order.model_copy(update={"cargo_mass_kg": 500.0})
    altered_event = created.model_copy(update={"order": altered_order})
    altered_events = tuple(
        altered_event if e.sequence == created.sequence else e for e in snap.events
    )
    with pytest.raises(ValueError):
        replay_order_arrivals(
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=service.actor_grants,
            initial_requests=(_request("order.1", release_time_s=0.0),),
            initial_time=ZERO,
            events=altered_events,
            current_time=service.current_time,
        )

    # Reordering causal history (swap a later release and an earlier creation)
    # breaks the append-only chain / temporal order and is rejected.
    reordered = (snap.events[1], snap.events[0],) + snap.events[2:]
    with pytest.raises((ValueError, ValidationError)):
        OrderArrivalSnapshot.model_validate(
            {
                "time": service.current_time,
                "events": reordered,
                "ledger": snap.ledger,
            }
        )


def test_release_before_declared_gate_rejected_at_snapshot_level() -> None:
    service = _service(_request("order.1", release_time_s=100.0))
    service.advance_time(_sec(100.0))
    snap = service.snapshot

    # Rebuild the journal with the release moved to 50s and a recomputed hash
    # chain so the rejected reason is the availability gate, not a broken hash.
    events: tuple[OrderArrivalEvent, ...] = ()
    for e in snap.events:
        if e.kind == "released":
            e = e.model_copy(update={"time": _sec(50.0)})
        events += (
            _rehash_append(
                events,
                kind=e.kind,
                event_id=e.event_id,
                order_id=e.order_id,
                time=e.time,
                order=e.order,
                source_identity=e.source_identity,
                actor_id=e.actor_id,
                lifecycle_record=e.lifecycle_record,
            ),
        )
    with pytest.raises(ValueError, match="availability gate"):
        OrderArrivalSnapshot(
            time=snap.time, version=len(events), events=events, ledger=snap.ledger
        )


def test_journal_rejects_mixed_coordinate_regression_both_directions() -> None:
    order = _request("order.mixed", release_time_s=0.0)
    ledger = replay_logistics_history((order,), actor_grants=RUN_ACTORS, history=())

    # tick/ns forward, then tick-forward/ns-backward: the later release record
    # regresses the nanosecond component and must be rejected even though the
    # tick increased.
    events: tuple[OrderArrivalEvent, ...] = ()
    events += (
        _rehash_append(
            events,
            kind="created",
            event_id=f"arrival.package:{order.order_id}",
            order_id=order.order_id,
            time=SimulationTime(tick=1, sim_time_ns=200_000_000),
            order=order,
            source_identity=ORDER_PACKAGE_SOURCE,
            actor_id=ORDER_PACKAGE_ACTOR_ID,
        ),
    )
    events += (
        _rehash_append(
            events,
            kind="released",
            event_id=f"arrival.release:{order.order_id}",
            order_id=order.order_id,
            time=SimulationTime(tick=2, sim_time_ns=100_000_000),
        ),
    )
    with pytest.raises(ValueError, match="temporally ordered"):
        OrderArrivalSnapshot(
            time=SimulationTime(tick=2, sim_time_ns=200_000_000),
            version=len(events),
            events=events,
            ledger=ledger,
        )

    # tick/ns forward, then tick-backward/ns-forward: the later record regresses
    # the tick component and must be rejected too.
    events2: tuple[OrderArrivalEvent, ...] = ()
    events2 += (
        _rehash_append(
            events2,
            kind="created",
            event_id=f"arrival.package:{order.order_id}",
            order_id=order.order_id,
            time=SimulationTime(tick=2, sim_time_ns=100_000_000),
            order=order,
            source_identity=ORDER_PACKAGE_SOURCE,
            actor_id=ORDER_PACKAGE_ACTOR_ID,
        ),
    )
    events2 += (
        _rehash_append(
            events2,
            kind="released",
            event_id=f"arrival.release:{order.order_id}",
            order_id=order.order_id,
            time=SimulationTime(tick=1, sim_time_ns=200_000_000),
        ),
    )
    with pytest.raises(ValueError, match="temporally ordered"):
        OrderArrivalSnapshot(
            time=SimulationTime(tick=2, sim_time_ns=200_000_000),
            version=len(events2),
            events=events2,
            ledger=ledger,
        )


# ---------------------------------------------------------------------------
# Canonical lifecycle rules preserved through the arrival layer (no proof claim).
# ---------------------------------------------------------------------------


def test_canonical_lifecycle_evidence_rules_retained() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    current = service.current_time
    path = _delivery_path(
        "order.1", current, "t.offer", "t.accept", "t.assign", "t.pick", "t.handoff", "t.deliver"
    )
    for transition in path[:3]:
        service.apply_lifecycle(transition)

    # pick_up without an external evidence reference is rejected by the
    # canonical model itself; the arrival layer never weakens that rule.
    with pytest.raises(ValidationError, match=r"evidence reference"):
        OrderTransition(
            order_id="order.1", transition_id="t.pick.noevidence", event="pick_up",
            actor_id="fleet.air:1", actor_role="aircraft_agent", time_s=0.0,
            expected_version=3, assignee_id="fleet.air:1",
        )
    with pytest.raises(ValidationError, match=r"command receipt"):
        OrderTransition(
            order_id="order.1", transition_id="t.pick.selfreceipt", event="pick_up",
            actor_id="fleet.air:1", actor_role="aircraft_agent", time_s=0.0,
            expected_version=3, assignee_id="fleet.air:1",
            evidence_ref="t.pick.selfreceipt",
        )

    for transition in path[3:]:
        service.apply_lifecycle(transition)
    delivered = next(
        state for state in service.available_orders() if state.order_id == "order.1"
    )
    assert delivered.status.value == "delivered"
    assert delivered.evidence_ref == "evidence.deliver"
    assert delivered.handoff_evidence_ref == "evidence.handoff"


def test_package_baseline_orders_carry_package_source_identity() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    created = next(e for e in service.snapshot.events if e.kind == "created")
    assert created.source_identity == ORDER_PACKAGE_SOURCE
    assert created.actor_id == ORDER_PACKAGE_ACTOR_ID
    assert created.event_id == "arrival.package:order.1"

    replayed = replay_order_arrivals(
        catalogue=service.catalogue,
        fleet=service.fleet,
        actor_grants=service.actor_grants,
        initial_requests=(_request("order.1", release_time_s=0.0),),
        initial_time=ZERO,
        events=service.snapshot.events,
        current_time=service.snapshot.time,
    )
    assert replayed.canonical_digest() == service.digest


def test_replay_rejects_created_event_without_authorized_business_grant() -> None:
    service = _service(_request("order.base", release_time_s=0.0))
    service.submit(
        _command(
            "command.online.1",
            order=_request("order.online.1", release_time_s=0.0),
            time=ZERO,
            actor_id="business",
            actor_role=ORDER_INTRODUCING_ROLE,
        )
    )
    snap = service.snapshot
    no_business = tuple(g for g in RUN_ACTORS if g.actor_id != "business")
    with pytest.raises(ValueError, match="business grant"):
        replay_order_arrivals(
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=no_business,
            initial_requests=(_request("order.base", release_time_s=0.0),),
            initial_time=ZERO,
            events=snap.events,
            current_time=snap.time,
        )
    with pytest.raises(ValueError, match="business grant"):
        OrderArrivalService.from_snapshot(
            snapshot=snap,
            initial_requests=(_request("order.base", release_time_s=0.0),),
            initial_time=ZERO,
            catalogue=service.catalogue,
            fleet=service.fleet,
            actor_grants=no_business,
        )


def test_from_snapshot_reconstructs_identical_state_and_can_continue() -> None:
    service = _service(_request("order.1", release_time_s=0.0))
    current = _sec(100.0)
    service.advance_time(current)
    service.submit(
        _command(
            "command.online.1",
            order=_request("order.online.1", release_time_s=100.0),
            time=current,
            source_identity="seed.program.7",
        )
    )
    for transition in _delivery_path(
        "order.1", current, "t.offer", "t.accept", "t.assign", "t.pick", "t.handoff", "t.deliver"
    ):
        service.apply_lifecycle(transition)

    snap = service.snapshot
    clone = OrderArrivalService.from_snapshot(
        snapshot=snap,
        initial_requests=(_request("order.1", release_time_s=0.0),),
        initial_time=ZERO,
        catalogue=service.catalogue,
        fleet=service.fleet,
        actor_grants=service.actor_grants,
    )
    # Pure replay reconstruction is exact: identical snapshot and digest.
    assert clone.snapshot == snap
    assert clone.digest == service.digest
    assert clone.registered_order_ids() == service.registered_order_ids()
    assert clone.released_order_ids() == service.released_order_ids()

    # The reconstructed service can continue the run without losing history.
    service.submit(
        _command(
            "command.online.2",
            order=_request("order.online.2", release_time_s=100.0),
            time=current,
            source_identity="seed.program.8",
        )
    )
    clone.submit(
        _command(
            "command.online.2",
            order=_request("order.online.2", release_time_s=100.0),
            time=current,
            source_identity="seed.program.8",
        )
    )
    assert clone.digest == service.digest
    released_events = [
        e for e in clone.snapshot.events if e.kind == "released"
    ]
    assert [e.order_id for e in released_events] == [
        "order.1",
        "order.online.1",
        "order.online.2",
    ]


def test_arrival_layer_allows_empty_initial_pool_then_dynamic_registration() -> None:
    service = _service()
    assert service.registered_order_ids() == ()
    assert service.available_orders() == ()

    event = service.submit(
        _command(
            "command.first",
            order=_request("order.first", release_time_s=0.0),
            time=ZERO,
        )
    )
    assert event.order.order_id == "order.first"
    assert service.registered_order_ids() == ("order.first",)
    assert service.available_orders()[0].order_id == "order.first"
    assert service.released_order_ids() == ("order.first",)
