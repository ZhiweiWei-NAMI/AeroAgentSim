"""Clock participant with no Logistics command or private-state access."""

import argparse

from aero_bench.agent.runtime import AgentContext, AgentFrameworkError
from aero_bench.runtime.contracts import SimulationTime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",))
    parser.parse_args()
    context = AgentContext.from_environment()
    if (
        context.contract.agent.tools
        or context.contract.agent.queries
        or context.contract.agent.observations
    ):
        raise AgentFrameworkError(
            "arrival clock participant must have no domain grants"
        )
    with context.gateway() as gateway:
        current = SimulationTime.model_validate(gateway.probe()["current"])
        for ordinal in range(context.contract.clock.max_steps):
            decision = gateway.complete_turn(
                completion_id=f"advance.{ordinal}",
                at=current,
                disposition="advance",
                command_ids=[],
                observation_ids=[],
            )
            if decision.status != "advanced":
                raise AgentFrameworkError(
                    "arrival clock did not advance at its Provider barrier"
                )
            current = decision.at
        decision = gateway.complete_turn(
            completion_id="finish.arrivals",
            at=current,
            disposition="finished",
            command_ids=[],
            observation_ids=[],
        )
        if decision.status != "terminated":
            raise AgentFrameworkError(
                "arrival clock did not terminate at its declared horizon"
            )
    print(f"arrival-clock-finished tick={current.tick}", flush=True)


if __name__ == "__main__":
    main()
