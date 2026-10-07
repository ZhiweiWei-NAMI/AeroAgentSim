from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Identifier, StrictModel


class SoftwareIdentity(StrictModel):
    version: Annotated[str, Field(min_length=1)]
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


TRAFFIC_RESTRICTION_CAPABILITY = "sumo.traffic.restrictions"


class SumoTrafficRestriction(StrictModel):
    """A permanent edge restriction applied once at a closed motion tick."""

    event_id: Identifier
    at_tick: Annotated[int, Field(gt=0, strict=True)]
    edge_id: Annotated[str, Field(min_length=1, pattern=r"^[^:\s][^\s]*$")]
    disallowed_classes: tuple[Literal["passenger"], ...] = Field(min_length=1, max_length=1)


class SumoConfig(StrictModel):
    """Strict domain declaration for a SUMO TraCI traffic provider.

    Runtime image, RPC endpoint, protocol identity, and deployment belong on
    ProviderManifest, ProviderWorkloadContract, and RuntimeEndpoint. traci_port
    remains the private SUMO process port, not the provider RPC bind.
    """

    schema_version: Annotated[str, Field(pattern=r"^aero-bench\.sumo/v2$")]
    provider_id: Identifier
    sumo: SoftwareIdentity
    sumo_binary: Identifier
    traci_port: Annotated[int, Field(ge=1024, le=65535)]
    step_length_ns: Annotated[int, Field(gt=0)]
    sumo_args: tuple[Annotated[str, Field(min_length=1)], ...]
    required_commands: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        min_length=1
    )
    command_timeout_ms: Annotated[int, Field(gt=0)]
    restrictions: tuple[SumoTrafficRestriction, ...] = ()

    @field_validator("required_commands")
    @classmethod
    def unique_commands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("SUMO required_commands must be unique")
        return value

    @model_validator(mode="after")
    def process_contract_is_closed(self) -> "SumoConfig":
        ids = tuple(item.event_id for item in self.restrictions)
        edges = tuple(item.edge_id for item in self.restrictions)
        if len(ids) != len(set(ids)) or len(edges) != len(set(edges)):
            raise ValueError("SUMO restriction event IDs and edge targets must be unique")
        if self.restrictions != tuple(sorted(self.restrictions, key=lambda item: (item.at_tick, item.event_id))):
            raise ValueError("SUMO restrictions must be sorted by tick and event ID")
        forbidden = {
            "--configuration-file",
            "-c",
            "--remote-port",
            "--seed",
            "--step-length",
        }
        overridden = {
            argument.split("=", maxsplit=1)[0]
            for argument in self.sumo_args
            if argument.startswith("-")
        }
        if forbidden.intersection(overridden):
            raise ValueError(
                "sumo_args cannot override configuration, port, seed, or step length"
            )
        if self.sumo_binary not in self.required_commands:
            raise ValueError("required_commands must include sumo_binary")
        return self
