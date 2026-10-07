"""Deterministic parcel transport through the real Gateway and PX4 commands."""

from __future__ import annotations

import argparse
from typing import Literal

from pydantic import Field

from aero_bench.agent.inspection_reference import InspectionReferenceParticipant
from aero_bench.agent.runtime import AgentContext, AgentFrameworkError
from aero_bench.config.models import Identifier, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics.orders import LogisticsIdentifier

POLICY_ASSET_ID = "asset.native-parcel-policy"
NATIVE_PARCEL_AGENT_VERSION = "0.4.0-native-parcel-participant.1"


class NativeParcelPolicy(StrictModel):
    schema_version: Literal["aero-bench.native-parcel-policy/v1"]
    vehicle_id: Identifier
    carrier_actor_id: LogisticsIdentifier
    order_id: Identifier
    parcel_id: Identifier
    destination_east_m: float
    destination_north_m: float
    cruise_up_m: float = Field(gt=0)
    dwell_s: float = Field(gt=0)


class NativeParcelParticipant(InspectionReferenceParticipant):
    """Reuse the existing flight navigation and closed-barrier turn methods."""

    def __init__(self, context, gateway):
        if context.contract.agent.driver is not None:
            raise AgentFrameworkError("native parcel participant has no model driver")
        self.context = context
        self.gateway = gateway
        self.policy = NativeParcelPolicy.model_validate_json(context.asset_bytes(POLICY_ASSET_ID))
        self.current = SimulationTime.model_validate(gateway.probe()["current"])
        self._sequence = 0
        self._commands = []
        self._observations = []

    def command(self, tool_id, arguments):
        command_id = self._identity("command")
        self.gateway.decision_summary(
            summary_id=self._identity("decision"), at=self.current,
            summary=f"Parcel transport requests {tool_id} through its declared Provider.",
            command_id=command_id,
        )
        result = self.gateway.command(command_id=command_id, tool_id=tool_id,
                                      at=self.current, arguments=arguments)
        self._commands.append(command_id)
        if any(receipt.phase == "failed" for receipt in result.receipts):
            raise AgentFrameworkError(f"parcel command failed: {result.model_dump(mode='json')}")
        while True:
            receipt = self.gateway.command_status(command_id=command_id, at=self.current)
            if receipt.phase == "completed":
                print(f"parcel-command tick={self.current.tick} tool={tool_id} phase=completed", flush=True)
                return
            if receipt.phase == "failed":
                raise AgentFrameworkError(f"parcel command failed: {receipt.detail}")
            self.advance()

    def run(self):
        vehicle = {"vehicle_id": self.policy.vehicle_id}
        parcel = {"actor_id": self.policy.carrier_actor_id,
                  "order_id": self.policy.order_id, "parcel_id": self.policy.parcel_id}
        # Closed samples include the two endpoints of the declared dwell window.
        self.wait(self.policy.dwell_s + self.context.contract.clock.step_ns / 1e9)
        self.command("logistics.parcel.pickup", parcel)
        self.command("flight.arm", vehicle)
        self.command("flight.takeoff", {**vehicle, "altitude_m": self.policy.cruise_up_m})
        self.wait(self.policy.dwell_s)
        self.goto(self.policy.destination_east_m, self.policy.destination_north_m,
                  self.policy.cruise_up_m)
        self.command("flight.land", vehicle)
        self.wait(self.policy.dwell_s + self.context.contract.clock.step_ns / 1e9)
        self.command("logistics.parcel.dropoff", parcel)
        self.command("flight.disarm", vehicle)
        while self.current.tick < self.context.contract.clock.max_steps:
            self.advance()
        decision = self.gateway.complete_turn(
            completion_id=self._identity("finish"), at=self.current,
            disposition="finished", command_ids=self._commands,
            observation_ids=self._observations,
        )
        if decision.status != "terminated":
            raise AgentFrameworkError("parcel participant did not terminate at its horizon")
        print(f"native-parcel-finished tick={self.current.tick}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",))
    parser.parse_args()
    context = AgentContext.from_environment()
    with context.gateway() as gateway:
        NativeParcelParticipant(context, gateway).run()


if __name__ == "__main__":
    main()
