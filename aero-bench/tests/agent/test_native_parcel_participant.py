"""Control-flow tests; these mocked receipts are not physical run evidence."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.agent.native_parcel import (
    POLICY_ASSET_ID,
    NativeParcelParticipant,
    NativeParcelPolicy,
)
from aero_bench.agent.runtime import AgentFrameworkError
from aero_bench.runtime.contracts import CommandReceipt, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.frame_math import EnuTransform, Vector3


def policy_values():
    return {
        "schema_version": "aero-bench.native-parcel-policy/v1",
        "vehicle_id": "vehicle.parcel",
        "carrier_actor_id": "actor.carrier",
        "order_id": "order.parcel",
        "parcel_id": "parcel.delivery",
        "pickup_east_m": -450.0,
        "pickup_north_m": -450.0,
        "destination_east_m": -420.0,
        "destination_north_m": -450.0,
        "cruise_up_m": 20.0,
        "dwell_s": 2.0,
    }


class ReceiptGateway:
    def __init__(self, fail_pickup_landing=False):
        self.commands = []
        self.completed = []
        self.fail_pickup_landing = fail_pickup_landing
        self.finished_at = None

    def probe(self):
        return {"current": {"tick": 0, "sim_time_ns": 0}}

    def decision_summary(self, **kwargs):
        pass

    def _receipt(self, command, phase, at):
        return CommandReceipt(
            run_id="1" * 64,
            command_id=command["command_id"],
            provider_id="provider.test",
            phase=phase,
            time=at,
            detail="test landing failure" if phase == "failed" else None,
        )

    def command(self, **kwargs):
        # A new action must wait for its predecessor's terminal receipt.
        assert len(self.completed) == len(self.commands)
        self.commands.append(kwargs)
        return SimpleNamespace(receipts=(self._receipt(kwargs, "accepted", kwargs["at"]),))

    def command_status(self, command_id, at):
        command = next(item for item in self.commands if item["command_id"] == command_id)
        if at.tick == command["at"].tick:
            return self._receipt(command, "applied", at)
        if self.fail_pickup_landing and command["tool_id"] == "flight.land":
            return self._receipt(command, "failed", at)
        self.completed.append((command_id, at.tick))
        return self._receipt(command, "completed", at)

    def complete_turn(self, *, at, disposition, **kwargs):
        if disposition == "finished":
            self.finished_at = at
            return SimpleNamespace(status="terminated", at=at)
        return SimpleNamespace(
            status="advanced",
            at=SimulationTime(tick=at.tick + 1, sim_time_ns=at.sim_time_ns + 1_000_000_000),
        )


def participant(gateway):
    def asset_bytes(asset_id):
        assert asset_id == POLICY_ASSET_ID
        return canonical_json_bytes(policy_values())

    origin = SimpleNamespace(
        wgs84=SimpleNamespace(longitude_deg=120.0, latitude_deg=30.0, ellipsoid_height_m=100.0),
        geoid_separation_m=10.0,
    )
    context = SimpleNamespace(
        asset_bytes=asset_bytes,
        contract=SimpleNamespace(
            agent=SimpleNamespace(driver=None),
            clock=SimpleNamespace(step_ns=1_000_000_000, max_steps=40),
            scenario=SimpleNamespace(frame_authority=SimpleNamespace(origin=origin)),
        ),
    )
    return NativeParcelParticipant(context, gateway)


@pytest.mark.parametrize("coordinate", ["pickup_east_m", "pickup_north_m"])
def test_pickup_coordinates_are_required(coordinate):
    values = policy_values()
    del values[coordinate]
    with pytest.raises(ValidationError, match=coordinate):
        NativeParcelPolicy.model_validate(values)


@pytest.mark.parametrize("field", [
    "pickup_east_m", "pickup_north_m", "destination_east_m",
    "destination_north_m", "cruise_up_m", "dwell_s",
])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_policy_rejects_nonfinite_flight_and_dwell_inputs(field, value):
    values = policy_values()
    values[field] = value
    with pytest.raises(ValidationError, match=field):
        NativeParcelPolicy.model_validate(values)


def test_pickup_and_delivery_wait_for_flight_receipts_and_ground_dwell():
    gateway = ReceiptGateway()
    participant(gateway).run()

    assert [item["tool_id"] for item in gateway.commands] == [
        "flight.arm", "flight.takeoff", "flight.goto", "flight.land",
        "logistics.parcel.pickup", "flight.arm", "flight.takeoff",
        "flight.goto", "flight.land", "logistics.parcel.dropoff", "flight.disarm",
    ]
    transform = EnuTransform.from_origin(longitude_deg=120.0, latitude_deg=30.0, altitude_m=100.0)
    for index, east in ((2, -450.0), (7, -420.0)):
        longitude, latitude, height = transform.enu_to_geodetic(Vector3(east, -450.0, 20.0))
        assert gateway.commands[index]["arguments"] == {
            "vehicle_id": "vehicle.parcel", "longitude_deg": longitude,
            "latitude_deg": latitude, "altitude_amsl_m": height - 10.0, "yaw_deg": 0.0,
        }
    # Two seconds of dwell include one extra closed sample at each landing.
    for land_index, parcel_index in ((3, 4), (8, 9)):
        assert gateway.commands[parcel_index]["at"].tick == gateway.completed[land_index][1] + 3
    assert len(gateway.completed) == len(gateway.commands)
    assert gateway.finished_at.tick == 40


def test_failed_pickup_landing_stops_before_custody_transfer():
    gateway = ReceiptGateway(fail_pickup_landing=True)
    with pytest.raises(AgentFrameworkError, match="test landing failure"):
        participant(gateway).run()
    assert [item["tool_id"] for item in gateway.commands] == [
        "flight.arm", "flight.takeoff", "flight.goto", "flight.land",
    ]
    assert gateway.finished_at is None
