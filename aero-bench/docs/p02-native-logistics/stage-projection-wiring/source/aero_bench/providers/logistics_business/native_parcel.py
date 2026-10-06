"""Explicit native-parcel adapter; the non-physical business adapter stays v3."""
from typing import Literal

from pydantic import model_validator

from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.native_parcel_rpc import NativeParcelSessionConfig

NATIVE_PARCEL_ADAPTER = "logistics.native-parcel"
NATIVE_PARCEL_CAPABILITY = "logistics.parcel.authority"


class NativeParcelBusinessConfig(LogisticsBusinessConfig):
    schema_version: Literal["aero-bench.logistics-native-business/v1"]
    native_parcel: NativeParcelSessionConfig

    @model_validator(mode="after")
    def native_declaration_matches_package(self):
        if self.observation is None:
            raise ValueError("native parcel requires explicit physical observation ingress")
        cfg = self.native_parcel
        ids = cfg.contract.identities
        if len(self.task_package.orders) != 1 or self.task_package.orders[0].order_id != ids.order_id:
            raise ValueError("native single parcel must bind the one canonical order")
        order = self.task_package.orders[0]
        if (order.origin_facility_id != ids.pickup_facility_id
                or order.destination_facility_id != ids.dropoff_facility_id):
            raise ValueError("native parcel source/destination differ from the canonical order")
        pads = tuple(pad for facility in self.task_package.facilities.facilities
                     for pad in facility_landing_pads(facility))
        if cfg.pickup_pad not in pads or cfg.dropoff_pad not in pads:
            raise ValueError("native parcel geometry must match canonical package pads")
        expected = {(ids.carrier_entity_id,pad.facility_id,pad.pad_index)
                    for pad in (cfg.pickup_pad,cfg.dropoff_pad)}
        if {(i.aircraft_id,i.facility_id,i.pad_index) for i in self.observation.spec.items} != expected:
            raise ValueError("native parcel observation plan must cover both exact pads")
        if self.observation.tolerances != cfg.contract.policy.presence_tolerances():
            raise ValueError("native parcel and source observation tolerances differ")
        carrier = cfg.contract.carrier
        binding = next(b for b in self.observation.bindings.aircraft
                       if b.aircraft_id == ids.carrier_entity_id)
        reference = next(r for r in self.observation.pose_references
                         if r.aircraft_id == ids.carrier_entity_id)
        if (binding.provider_id != carrier.provider_id
                or binding.vehicle_id != carrier.native_vehicle_id
                or binding.fleet_entry_id != carrier.fleet_entry_id
                or binding.visual_asset_id != carrier.visual_asset_id
                or reference.pose_reference_above_contact_m != carrier.pose_reference_above_contact_m):
            raise ValueError("parcel carrier/profile calibration differs from source observation binding")
        if not any(b.principal_id == ids.authorized_principal_id
                   and b.actor_id == ids.carrier_entity_id
                   and b.role == "aircraft_agent" for b in self.principal_bindings):
            raise ValueError("native parcel needs the declared principal-to-carrier grant")
        return self


def build_native_parcel_business_session(config, manifest, runtime_endpoint,
                                        run_id, credential, scenario, clock):
    from aero_bench.providers.logistics_business.adapter import SUPPORTED_CAPABILITIES
    from aero_bench.providers.registry import ProviderRegistryError
    if manifest.adapter != NATIVE_PARCEL_ADAPTER or manifest.provider_id != config.provider_id:
        raise ProviderRegistryError("native parcel adapter/provider identity mismatch")
    if set(manifest.capabilities) - (SUPPORTED_CAPABILITIES | {NATIVE_PARCEL_CAPABILITY}):
        raise ProviderRegistryError("unsupported native parcel capability")
    if NATIVE_PARCEL_CAPABILITY not in manifest.capabilities:
        raise ProviderRegistryError("native parcel authority must be explicitly declared")
    if config.native_parcel.step_ns != clock.step_ns or config.native_parcel.max_steps != clock.max_steps:
        raise ProviderRegistryError("native parcel run bound differs from resolved clock")
    if config.scheduled_orders:
        raise ProviderRegistryError("single native parcel does not declare online scheduled orders")
    return NativeParcelBusinessProvider(config=config, manifest=manifest,
        runtime_endpoint=runtime_endpoint, run_id=run_id, session_token=credential,
        scenario=scenario)


from aero_bench.providers.logistics_business.provider import (
    LogisticsBusinessProvider, LogisticsBusinessProviderError,
)


class NativeParcelBusinessProvider(LogisticsBusinessProvider):
    async def handle_command(self, request):
        from aero_bench.providers.contracts import ProviderCommandResult
        from aero_bench.runtime.contracts import CommandReceipt
        from aero_bench.tasks.logistics.native_parcel_rpc import (
            NativeParcelActionRecord, PARCEL_PICKUP_TOOL, PARCEL_DROPOFF_TOOL,
        )
        if request.tool_id not in {PARCEL_PICKUP_TOOL, PARCEL_DROPOFF_TOOL}:
            return await super().handle_command(request)
        self._require_prepared()
        if request.run_id != self._run_id or request.issued_at != self._last_time:
            raise LogisticsBusinessProviderError("parcel request is not at the authoritative run time")
        response = await self._request("command", {"provider_id":self._config.provider_id,
            "run_id":self._run_id, "request":request.model_dump(mode="json")})
        if set(response) != {"receipts", "parcel_action_record"}:
            raise LogisticsBusinessProviderError("parcel command response fields differ")
        receipts = tuple(CommandReceipt.model_validate(r) for r in response["receipts"])
        self._validate_command_receipts(receipts, request, self._config.provider_id)
        record = NativeParcelActionRecord.model_validate(response["parcel_action_record"])
        if record.request != request:
            raise LogisticsBusinessProviderError("parcel response belongs to another request")
        if (receipts[-1].phase == "completed") != (record.outcome.status == "admitted"):
            raise LogisticsBusinessProviderError("parcel command lifecycle contradicts admission")
        return ProviderCommandResult(receipts=receipts)

    async def parcel_scene_frame(self, *, at):
        from aero_bench.tasks.logistics.native_parcel_rpc import (
            NativeParcelSceneFrame, PARCEL_SNAPSHOT_OPERATION,
        )
        self._require_prepared()
        if self._last_time != at:
            raise LogisticsBusinessProviderError("parcel scene request time is not current")
        response = await self._request(PARCEL_SNAPSHOT_OPERATION,
            {"provider_id":self._config.provider_id, "run_id":self._run_id,
             "at":at.model_dump(mode="json")})
        if set(response) != {"frame"}:
            raise LogisticsBusinessProviderError("parcel scene response fields differ")
        frame = NativeParcelSceneFrame.model_validate(response["frame"])
        if frame.scene_state.run_id != self._run_id or frame.scene_state.at != at:
            raise LogisticsBusinessProviderError("parcel frame belongs to another run/time")
        return frame
