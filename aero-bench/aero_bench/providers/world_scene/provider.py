from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from aero_bench.config.models import ClockSpec, Identifier, Sha256, StrictModel
from aero_bench.providers.contracts import (
    ProviderCommandResult,
    ProviderManifest,
    ProviderSession,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.world_scene.config import WorldSceneConfig
from aero_bench.providers.world_scene.protocol import (
    JsonLineTransport,
    WorldSceneTransport,
)
from aero_bench.runtime.contracts import (
    CommandRequest,
    FinalizedArtifact,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    SimulationTime,
    StepReceipt,
    StepRequest,
)
from aero_bench.world.resolved import ResolvedScenario


#: The wire protocol this client speaks; the service reports the same value
#: at readiness. The bundle-side protocol identity is
#: `ProviderManifest.protocol_schema`.
PROTOCOL_VERSION = "aero-bench.world-scene-rpc/v2"


class WorldSceneProviderError(RuntimeError):
    pass


class WorldSceneProviderNotReady(WorldSceneProviderError):
    pass


class WorldSceneReadiness(StrictModel):
    status: Literal["ready"]
    provider_id: Identifier
    protocol_version: str
    runtime_image: str
    scenario_schema_version: Literal["aero-bench.resolved-scenario/v4"]
    scenario_digest: Sha256
    world_schema_version: Literal["aero-bench.world/v2"]
    world_id: Identifier
    world_digest: Sha256
    source_asset_digest: Sha256
    scenario_asset_digest: Sha256
    static_authority: Literal["scenario.compiler"]
    scene_version: str
    scene_commit: str


class WorldSceneSnapshotResponse(StrictModel):
    snapshot_digest: Sha256


class WorldSceneProvider(ProviderSession):
    """Client for the read-only ResolvedScenario projection workload.

    The scenario compiler is the sole authority for static declarations. This
    session carries no independent world package or scene identity: it checks
    that the workload mirrors the exact digest-bound ``ResolvedScenario/v2``
    projection supplied by the executor and never computes or owns motion.

    The session presents the executor-issued, run-scoped session token on
    every frame, including the first ``prepare`` and ``shutdown``. The
    executor injects the same token into the world-scene workload alone,
    so the workload pins it before any state exists and a peer on the
    provider network cannot race the first prepare or forge the attested
    principal by replaying public identity fields.
    """

    _STATE_SCHEMA = "world.scene.projection.v2"
    _EVIDENCE_ARTIFACT_TYPE = "world.scene.evidence"

    def __init__(
        self,
        *,
        config: WorldSceneConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        session_token: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ):
        if manifest.provider_id != config.provider_id:
            raise ValueError("world-scene manifest/provider configuration IDs differ")
        if (
            config.scene.version != manifest.implementation.version
            or config.scene.commit != manifest.implementation.source_revision
        ):
            raise ValueError(
                "world-scene config identity differs from workload implementation"
            )
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise TypeError("world-scene runtime_endpoint must be a RuntimeEndpoint")
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("world-scene scenario must be a ResolvedScenario")
        if not isinstance(clock, ClockSpec):
            raise TypeError("world-scene clock must be a ClockSpec")
        scenario_provider = next(
            (
                provider
                for provider in scenario.providers
                if provider.provider_id == config.provider_id
            ),
            None,
        )
        if scenario_provider is None:
            raise ValueError("world-scene workload is absent from ResolvedScenario")
        if scenario_provider.capability_ids != tuple(sorted(manifest.capabilities)):
            raise ValueError(
                "world-scene capabilities differ from ResolvedScenario projection"
            )
        if any(
            entity.state == "static"
            and (
                entity.owner_kind != "scenario"
                or entity.owner_id != "scenario.compiler"
                or entity.source_provider_id is not None
            )
            for entity in scenario.entities
        ):
            raise ValueError(
                "world-scene static declarations must remain scenario-compiler owned"
            )
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError("world-scene run_id must be a SHA-256 digest") from error
        if validated_run_id == "0" * 64:
            raise ValueError("world-scene run_id cannot be a placeholder digest")
        try:
            validated_session_token = TypeAdapter(Sha256).validate_python(session_token)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "world-scene session_token must be a SHA-256 digest"
            ) from error
        if validated_session_token == "0" * 64:
            raise ValueError("world-scene session_token cannot be a placeholder digest")
        self._config = config
        self._manifest = manifest
        self._runtime_endpoint = runtime_endpoint
        self._run_id = validated_run_id
        self._scenario = scenario
        self._clock = clock
        # Executor-issued, run-scoped channel credential. The registry owns its
        # validation chain; the workload pins it against its own injected copy
        # and rejects any frame that cannot present it.
        self._session_token = validated_session_token
        self._transport: WorldSceneTransport | None = None
        self._prepared = False
        self._last_time: SimulationTime | None = None

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    @staticmethod
    async def connect_runtime(endpoint: RuntimeEndpoint) -> WorldSceneTransport:
        """Connect to the materializer-owned endpoint; the only transport path."""

        return await JsonLineTransport.connect(endpoint)

    async def _request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._transport is None:
            self._transport = await self.connect_runtime(self._runtime_endpoint)
        # Every frame presents the pinned executor-issued token, first
        # prepare included; the pinned value always wins.
        return await self._transport.request(
            operation, {**payload, "session_token": self._session_token}
        )

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise WorldSceneProviderNotReady(
                "world-scene provider has not completed readiness"
            )

    def _artifact_requirements(self) -> list[dict[str, object]]:
        requirements = self._manifest.artifact_requirements
        if len(requirements) != 1:
            raise WorldSceneProviderError(
                "world-scene provider requires exactly one declared "
                "world.scene.evidence artifact"
            )
        requirement = requirements[0]
        if (
            requirement.artifact_type != self._EVIDENCE_ARTIFACT_TYPE
            or requirement.producer_id != self._config.provider_id
            or requirement.visibility != "private"
            or requirement.source_asset_id is not None
        ):
            raise WorldSceneProviderError(
                "world-scene evidence must be private with this provider as producer"
            )
        return [requirement.model_dump(mode="json")]

    def _config_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self._config.provider_id,
            "run_id": self._run_id,
            "runtime_image": self._manifest.runtime_image,
            "config_digest": self._manifest.config_digest,
            "artifact_requirements": self._artifact_requirements(),
            "scenario_digest": self._scenario.scenario_digest,
        }

    async def prepare(self) -> None:
        if self._prepared:
            raise WorldSceneProviderError("world-scene provider prepare called twice")
        response = await self._request("prepare", self._config_payload())
        try:
            readiness = WorldSceneReadiness.model_validate(response)
        except ValidationError as exc:
            raise WorldSceneProviderError(
                "world-scene provider readiness response is invalid"
            ) from exc
        if readiness.status != "ready":
            raise WorldSceneProviderError(
                f"world-scene provider did not become ready: {readiness.status!r}"
            )
        if readiness.provider_id != self._config.provider_id:
            raise WorldSceneProviderError(
                "world-scene readiness provider_id differs from declaration"
            )
        if readiness.protocol_version != PROTOCOL_VERSION:
            raise WorldSceneProviderError(
                "world-scene provider protocol version mismatch"
            )
        if readiness.runtime_image != self._manifest.runtime_image:
            raise WorldSceneProviderError(
                "world-scene provider image identity mismatch"
            )
        scenario = self._scenario
        if (
            readiness.scenario_schema_version != scenario.schema_version
            or readiness.scenario_digest != scenario.scenario_digest
            or readiness.world_schema_version != scenario.world_schema_version
            or readiness.world_id != scenario.world_id
            or readiness.world_digest != scenario.world_digest
            or readiness.source_asset_digest != scenario.source_asset_digest
            or readiness.scenario_asset_digest != scenario.scenario_asset_digest
            or readiness.static_authority != "scenario.compiler"
        ):
            raise WorldSceneProviderError(
                "world-scene workload differs from the compiler-owned scenario projection"
            )
        if (
            readiness.scene_version != self._config.scene.version
            or readiness.scene_commit != self._config.scene.commit
        ):
            raise WorldSceneProviderError(
                "world-scene service identity does not match configuration"
            )
        self._prepared = True

    def _parse_receipt(
        self,
        response: Mapping[str, Any],
        *,
        target: SimulationTime,
        operation: str,
    ) -> StepReceipt:
        try:
            receipt = StepReceipt.model_validate(response.get("receipt"))
        except ValidationError as exc:
            raise WorldSceneProviderError(
                f"world-scene {operation} response does not contain a valid "
                "StepReceipt"
            ) from exc
        if receipt.run_id != self._run_id:
            raise WorldSceneProviderError(
                "world-scene StepReceipt run identity mismatch"
            )
        if receipt.provider_id != self._config.provider_id:
            raise WorldSceneProviderError(
                "world-scene StepReceipt provider identity mismatch"
            )
        if receipt.reached != target:
            raise WorldSceneProviderError(
                "world-scene service did not reach the requested barrier time"
            )
        projection_events = tuple(
            event
            for event in receipt.events
            if event.payload_schema_id == self._STATE_SCHEMA
        )
        if len(projection_events) != 1:
            raise WorldSceneProviderError(
                "world-scene response must contain exactly one compiler projection event"
            )
        projection_payload = {
            item.name: item.value for item in projection_events[0].payload
        }
        if len(projection_payload) != len(projection_events[0].payload) or (
            projection_payload.get("scenario_digest")
            != self._scenario.scenario_digest
            or projection_payload.get("static_authority") != "scenario.compiler"
        ):
            raise WorldSceneProviderError(
                "world-scene event is not bound to the compiler-owned scenario"
            )
        return receipt

    async def reset(self, *, seed: int) -> StepReceipt:
        self._require_prepared()
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("world-scene reset seed must be an integer")
        if seed != self._scenario.seed:
            raise WorldSceneProviderError(
                "world-scene reset seed differs from ResolvedScenario"
            )
        response = await self._request(
            "reset",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "seed": seed,
            },
        )
        target = SimulationTime(tick=0, sim_time_ns=0)
        receipt = self._parse_receipt(response, target=target, operation="reset")
        self._last_time = target
        return receipt

    async def step_to(self, request: StepRequest) -> StepReceipt:
        self._require_prepared()
        if request.run_id != self._run_id:
            raise WorldSceneProviderError(
                "world-scene step request belongs to another run"
            )
        if self._last_time is None:
            raise WorldSceneProviderError("world-scene reset must precede step_to")
        expected = SimulationTime(
            tick=self._last_time.tick + 1,
            sim_time_ns=self._last_time.sim_time_ns + self._clock.step_ns,
        )
        if request.target != expected or request.target.tick > self._clock.max_steps:
            raise WorldSceneProviderError(
                "world-scene step target must equal the next ResolvedRun clock barrier"
            )
        receipt = self._parse_receipt(
            await self._request(
                "step_to",
                {
                    "provider_id": self._config.provider_id,
                    "run_id": self._run_id,
                    "request": request.model_dump(mode="json"),
                },
            ),
            target=request.target,
            operation="step_to",
        )
        self._last_time = receipt.reached
        return receipt

    async def handle_command(self, request: CommandRequest) -> ProviderCommandResult:
        raise WorldSceneProviderError(
            f"world-scene workload does not own tool {request.tool_id!r}; it mirrors "
            "the compiler-owned scenario projection only"
        )

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt:
        self._require_prepared()
        if request.run_id != self._run_id or request.terminal_time != self._last_time:
            raise WorldSceneProviderError(
                "world-scene finalization identity or time mismatch"
            )
        response = await self._request(
            "finalize",
            {
                "provider_id": self._config.provider_id,
                "run_id": self._run_id,
                "request": request.model_dump(mode="json"),
            },
        )
        try:
            receipt = ProviderFinalizationReceipt.model_validate(
                response.get("receipt")
            )
        except ValidationError as exc:
            raise WorldSceneProviderError(
                "world-scene finalization response is invalid"
            ) from exc
        expected = {
            requirement.artifact_id: requirement
            for requirement in self._manifest.artifact_requirements
        }
        actual: dict[str, FinalizedArtifact] = {
            artifact.artifact_id: artifact for artifact in receipt.artifacts
        }
        if (
            receipt.run_id != self._run_id
            or receipt.provider_id != self._config.provider_id
            or receipt.event_chain_root != request.event_chain_root
            or set(actual) != set(expected)
            or any(
                artifact.size_bytes > expected[artifact_id].max_size_bytes
                for artifact_id, artifact in actual.items()
            )
        ):
            raise WorldSceneProviderError(
                "world-scene finalization receipt identity is invalid"
            )
        return receipt

    async def snapshot_digest(self) -> str:
        self._require_prepared()
        response = await self._request(
            "snapshot",
            {"provider_id": self._config.provider_id, "run_id": self._run_id},
        )
        try:
            snapshot = WorldSceneSnapshotResponse.model_validate(response)
        except ValidationError as exc:
            raise WorldSceneProviderError(
                "world-scene snapshot response has no valid digest"
            ) from exc
        return TypeAdapter(Sha256).validate_python(snapshot.snapshot_digest)

    async def shutdown(self) -> None:
        if self._transport is None:
            return
        try:
            if self._prepared:
                response = await self._transport.request(
                    "shutdown",
                    {
                        "provider_id": self._config.provider_id,
                        "run_id": self._run_id,
                        "session_token": self._session_token,
                    },
                )
                if response.get("status") != "stopped":
                    raise WorldSceneProviderError(
                        "world-scene provider did not acknowledge shutdown"
                    )
        finally:
            await self._transport.close()
            self._prepared = False
            self._transport = None
            self._last_time = None
