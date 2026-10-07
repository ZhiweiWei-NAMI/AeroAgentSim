"""Compose closed PX4 observation ingress and the authoritative parcel projection."""
import hashlib

from aero_bench.config.models import NamedValue
from aero_bench.providers.logistics_business.native_parcel import NativeParcelBusinessConfig
from aero_bench.runtime.events import RunEventAudience
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.native_parcel_rpc import NativeParcelSceneFrame
from aero_bench.tasks.logistics.runtime_hook import (
    LogisticsRuntimeHook, LogisticsRuntimeHookFactory, LogisticsRuntimeHookError,
)
from aero_bench.trace.vocabulary import PUBLIC_PARCEL_PAYLOAD_SCHEMA_ID


class NativeParcelRuntimeHook:
    def __init__(self, *, observation_hook, business, ledger):
        if not isinstance(observation_hook, LogisticsRuntimeHook):
            raise TypeError("native parcel requires the existing LogisticsRuntimeHook")
        if not callable(getattr(business,"parcel_scene_frame",None)):
            raise TypeError("native parcel requires an authoritative business projection RPC")
        self.observation_hook = observation_hook
        self.business = business
        self.ledger = ledger

    async def on_validated_observation(self, observation):
        await self.observation_hook.on_validated_observation(observation)

    async def on_stage_barriers_closed(self, events, *, scene_state, stage_barriers, target):
        # One Harness hook slot: complete the existing observation ingest first.
        # Never await another advance or mutate the already sealed motion state.
        await self.observation_hook.on_stage_barriers_closed(events,
            scene_state=scene_state,stage_barriers=stage_barriers,target=target)
        frame = await self.business.parcel_scene_frame(at=target)
        if not isinstance(frame, NativeParcelSceneFrame) or frame.scene_state != scene_state:
            raise LogisticsRuntimeHookError("parcel projection RPC changed its source SceneState")
        encoded = canonical_json_bytes(frame.model_dump(mode="json"))
        digest = hashlib.sha256(encoded).hexdigest()
        self.ledger.append_event(source="harness",source_kind="harness",
            event_type="logistics.parcel.scene-frame.v1",time=target,
            visibility=(RunEventAudience(scope="private"),RunEventAudience(scope="verifier")),
            payload=(NamedValue(name="frame_digest",value=digest),
                     NamedValue(name="frame_json",value=encoded.decode())))
        parcel = frame.parcel
        fields = {
            "order_id":parcel.order_id,"parcel_id":parcel.parcel_id,
            "custody_holder_id":parcel.custody_holder_id,
            "custody_holder_kind":parcel.custody_holder_kind,
            "destination_id":parcel.destination_id,"parcel_state":parcel.state,
            "authority":parcel.authority,"source_scene_state_digest":parcel.source_scene_state_digest,
            "scenario_digest":parcel.scenario_digest,
            "source_stage_barrier_digest":parcel.source_stage_barrier_digest,
            "source_observation_digest":parcel.source_observation_digest,
            "frame_digest":digest,"x_m":parcel.pose.x_m,"y_m":parcel.pose.y_m,"z_m":parcel.pose.z_m,
            "qw":parcel.pose.orientation.qw,"qx":parcel.pose.orientation.qx,
            "qy":parcel.pose.orientation.qy,"qz":parcel.pose.orientation.qz,
        }
        fields["carrier_entity_id"] = parcel.carrier_entity_id
        self.ledger.append_event(source=self.business.manifest.provider_id,source_kind="provider",
            provider_id=self.business.manifest.provider_id,event_type="public.parcel-projection",
            interaction_type="logistics.parcel_projection.v1",
            payload_schema_id=PUBLIC_PARCEL_PAYLOAD_SCHEMA_ID,
            time=target,entity_id=parcel.parcel_id,
            visibility=(RunEventAudience(scope="public"),),
            payload=tuple(NamedValue(name=k,value=v) for k,v in fields.items()))


class NativeParcelRuntimeHookFactory(LogisticsRuntimeHookFactory):
    def __init__(self, *, config, business_provider_id):
        if not isinstance(config, NativeParcelBusinessConfig):
            raise TypeError("native parcel hook requires its explicitly versioned config")
        super().__init__(config=config,business_provider_id=business_provider_id)

    def create(self, **context):
        original = super().create(**context)
        return NativeParcelRuntimeHook(observation_hook=original,
            business=context["providers"][self._business_provider_id],ledger=context["ledger"])
