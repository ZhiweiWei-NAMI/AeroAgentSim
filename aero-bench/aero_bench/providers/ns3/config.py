from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, StrictModel


NETWORK_MODEL = "wifi-adhoc-scene-mobility/v1"
NETWORK_MODEL_CAPABILITY = "network.wifi-scene-mobility"
MAILBOX_CAPABILITY = "network.agent-mailbox"
PROPAGATION_MODEL = "log-distance-with-scene-volumes/v1"
LOG_DISTANCE_EXPONENT = 3.0
BUILDING_ATTENUATION_DB = 12.0
RX_NOISE_FIGURE_DB = 7.0
THERMAL_NOISE_DENSITY_DBM_HZ = -174.0
WifiStandard: TypeAlias = Literal["802.11n", "802.11ac", "802.11ax"]


def ns3_capabilities(standards: tuple[WifiStandard, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                "network.delivery",
                MAILBOX_CAPABILITY,
                NETWORK_MODEL_CAPABILITY,
                *(f"wifi.{standard}" for standard in standards),
            }
        )
    )


class SoftwareIdentity(StrictModel):
    version: Annotated[str, Field(min_length=1)]
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class Ns3Config(StrictModel):
    """Strict domain declaration for a real ns-3 discrete-event backend.

    Runtime image, RPC endpoint, protocol identity, and deployment belong on
    ProviderManifest, ProviderWorkloadContract, and RuntimeEndpoint.
    """

    schema_version: Annotated[str, Field(pattern=r"^aero-bench\.ns3/v3$")]
    provider_id: Identifier
    ns3: SoftwareIdentity
    network_model: Literal["wifi-adhoc-scene-mobility/v1"]
    supported_wifi_standards: tuple[WifiStandard, ...] = Field(min_length=1)
    required_commands: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        min_length=1
    )
    command_timeout_ms: Annotated[int, Field(gt=0, le=600_000)]

    @model_validator(mode="after")
    def declarations_are_canonical(self) -> "Ns3Config":
        if self.network_model != NETWORK_MODEL:
            raise ValueError("network_model is not supported by the ns-3 backend")
        if self.supported_wifi_standards != tuple(
            sorted(self.supported_wifi_standards)
        ) or len(self.supported_wifi_standards) != len(
            set(self.supported_wifi_standards)
        ):
            raise ValueError("supported_wifi_standards must be sorted and unique")
        if len(self.required_commands) != len(set(self.required_commands)):
            raise ValueError("required_commands must be unique")
        return self
