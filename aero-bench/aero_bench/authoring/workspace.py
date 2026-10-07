"""The current city editor's authoring boundary, without execution claims."""

from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias

from pydantic import BeforeValidator, ConfigDict, Field, JsonValue, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.providers.rpc import parse_json_object
from aero_bench.tasks.logistics.authoring import AuthoredOrderRequest
from aero_bench.tasks.logistics.airspace_events import XzPoint, validated_open_ring
from aero_bench.tasks.logistics.fleet import AuthoredPerformanceProfile
from aero_bench.tasks.logistics.orders import LogisticsIdentifier


WORKSPACE_V2_SCHEMA = "aero-bench.city-workspace/v2"
WORKSPACE_V3_SCHEMA = "aero-bench.city-workspace/v3"
WORKSPACE_SCHEMA = WORKSPACE_V3_SCHEMA
MAX_SAFE_INTEGER = 2**53 - 1


def _safe_integer(value: object) -> object:
    # JavaScript has one numeric wire type: 1 and 1.0 are the same integer.
    if type(value) not in (int, float):
        raise ValueError("expected a JavaScript safe integer")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError("expected a JavaScript safe integer")
    if abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("integer exceeds JavaScript's exact range")
    return int(value)


# Field precedes the BeforeValidator so pydantic emits JSON Schema minimum/maximum; constraints
# placed after it are dumped as raw, non-standard "ge"/"le" keys that no validator enforces.
SafeInteger: TypeAlias = Annotated[
    int, Field(le=MAX_SAFE_INTEGER), BeforeValidator(_safe_integer)
]
NonNegativeInteger: TypeAlias = Annotated[
    int, Field(ge=0, le=MAX_SAFE_INTEGER), BeforeValidator(_safe_integer)
]
PositiveInteger: TypeAlias = Annotated[
    int, Field(ge=1, le=MAX_SAFE_INTEGER), BeforeValidator(_safe_integer)
]
GeneratedOrderCount: TypeAlias = Annotated[
    int, Field(ge=0, le=10_000), BeforeValidator(_safe_integer)
]
FiniteNumber: TypeAlias = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeNumber: TypeAlias = Annotated[FiniteNumber, Field(ge=0)]
PositiveNumber: TypeAlias = Annotated[FiniteNumber, Field(gt=0)]
Text: TypeAlias = Annotated[str, Field(min_length=1)]
Scalar: TypeAlias = str | FiniteNumber | bool


class WorkspaceModel(StrictModel):
    model_config = ConfigDict(allow_inf_nan=False)


class CityPoint2(WorkspaceModel):
    x: FiniteNumber
    z: FiniteNumber


class CityPoint3(CityPoint2):
    y: FiniteNumber


class CityDraftEnvironment(WorkspaceModel):
    cloudCover: Annotated[FiniteNumber, Field(ge=0, le=1)]
    precipitation: Literal["none", "drizzle", "rain", "snow", "hail"]
    precipitationRateMmPerH: NonNegativeNumber
    visibilityM: PositiveNumber
    windMps: NonNegativeNumber
    windDirectionDeg: Annotated[FiniteNumber, Field(ge=0, lt=360)]
    timeOfDay: Literal["day", "twilight", "night"]
    reflectionsEnabled: bool

    @model_validator(mode="after")
    def precipitation_consistency(self) -> CityDraftEnvironment:
        if self.precipitation == "none" and self.precipitationRateMmPerH != 0:
            raise ValueError("no precipitation requires a zero precipitation rate")
        return self


class CityFleetEntry(WorkspaceModel):
    id: Text
    assetId: Literal["model:holybro-x500", "model:quadcopter-40-preview"]
    count: PositiveInteger
    homeFacilityId: Text | None
    batteryWh: PositiveNumber
    reserveRatio: Annotated[FiniteNumber, Field(ge=0, le=1)]


class CityTraffic(WorkspaceModel):
    vehicles: NonNegativeInteger
    pedestrians: NonNegativeInteger
    bicycles: NonNegativeInteger


class CityFacility(WorkspaceModel):
    id: Text
    name: Text
    kind: Literal["vertiport", "hub", "charger"]
    position: CityPoint2
    rotationDeg: FiniteNumber
    widthM: PositiveNumber
    depthM: PositiveNumber
    heightM: PositiveNumber
    capacity: PositiveInteger
    chargingPowerW: NonNegativeNumber


class CityAirspaceSource(WorkspaceModel):
    kind: Literal["manual", "geojson"]
    label: Text
    uri: Text | None = None

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema["properties"]["uri"] = {"type": "string", "minLength": 1, "title": "Uri"}
        return schema

    @model_validator(mode="before")
    @classmethod
    def uri_is_optional_not_nullable(cls, value: object) -> object:
        if isinstance(value, dict) and "uri" in value and value["uri"] is None:
            raise ValueError("source.uri must be a nonempty string when supplied")
        return value


class CityAirspace(WorkspaceModel):
    id: Text
    name: Text
    polygon: tuple[CityPoint2, ...] = Field(min_length=3)
    floorM: NonNegativeNumber
    ceilingM: NonNegativeNumber
    startsAtS: NonNegativeNumber
    endsAtS: NonNegativeNumber | None
    source: CityAirspaceSource

    @model_validator(mode="after")
    def volume_consistency(self) -> CityAirspace:
        if self.ceilingM <= self.floorM:
            raise ValueError("airspace ceiling must exceed its floor")
        if self.endsAtS is not None and self.endsAtS <= self.startsAtS:
            raise ValueError("airspace must end after it starts")
        # Validate with the canonical geometry implementation, but retain the
        # authored ring, including its optional closing point, in the snapshot.
        validated_open_ring(tuple(XzPoint(x=p.x, z=p.z) for p in self.polygon))
        return self


class CityAlgorithms(WorkspaceModel):
    mode: Literal["centralized", "distributed"]
    assignment: Literal["greedy", "auction", "min_cost_flow", "external"]
    routing: Literal["astar", "rrt_star", "external"]
    energy: Literal["reserve_threshold", "external"]
    parameters: dict[Text, Scalar]


class CityDeployment(WorkspaceModel):
    executor: Literal["docker_reference", "kubernetes_cluster"]
    imageRef: Annotated[str, Field(pattern=r"^(?:|\S+@sha256:[0-9a-f]{64})$")]


class CityEvent(WorkspaceModel):
    id: Text
    atS: NonNegativeNumber
    type: Literal[
        "order.created", "weather.changed", "airspace.activated", "charger.outage",
        "traffic.restricted",
    ]
    targetId: str
    payload: dict[str, JsonValue]


class CityActionRule(WorkspaceModel):
    id: Text
    eventType: Text
    action: Literal[
        "accept_order", "plan_route", "follow_route", "takeoff", "land",
        "start_charging", "stop_charging",
    ]
    executorRole: Literal["coordinator", "vehicle"]
    arguments: dict[str, JsonValue]


class CityStateKeyframe(WorkspaceModel):
    id: Text
    atS: NonNegativeNumber
    entityId: Text
    position: CityPoint3
    label: str


class CityLabelRule(WorkspaceModel):
    id: Text
    field: Text
    operator: Literal["eq", "lt", "gt"]
    value: Scalar
    label: Text


class CityOrderRequest(WorkspaceModel):
    """One authoring-only logistics request under the current camelCase shape."""

    id: LogisticsIdentifier
    sourceFacilityId: LogisticsIdentifier
    destinationFacilityId: LogisticsIdentifier
    hubHandoffFacilityId: LogisticsIdentifier | None
    cargoKg: PositiveNumber
    releaseAtS: NonNegativeNumber
    deliverByS: PositiveNumber

    @model_validator(mode="after")
    def domain_shape_is_valid(self) -> CityOrderRequest:
        # Reuse the logistics domain's strict invariants. Facility and hub
        # references are bound by the enclosing v3 workspace.
        AuthoredOrderRequest.model_validate(self.model_dump(mode="json"))
        return self


class CityOrderGeneration(WorkspaceModel):
    """Deterministic demand inputs; this never claims that orders were created."""

    seed: NonNegativeInteger
    maxOrders: GeneratedOrderCount
    startAtS: NonNegativeNumber
    endAtS: FiniteNumber
    cargoMinKg: PositiveNumber
    cargoMaxKg: FiniteNumber
    deadlineLeadS: PositiveNumber

    @model_validator(mode="after")
    def ranges_are_valid(self) -> CityOrderGeneration:
        if self.endAtS <= self.startAtS:
            raise ValueError("order generation must end after it starts")
        if self.cargoMaxKg < self.cargoMinKg:
            raise ValueError("maximum generated cargo must not be below the minimum")
        return self

    @classmethod
    def disabled(cls, seed: int) -> CityOrderGeneration:
        """Return the current authoring default with generation disabled."""
        return cls(
            seed=seed, maxOrders=0, startAtS=0, endAtS=3600,
            cargoMinKg=0.1, cargoMaxKg=1, deadlineLeadS=600,
        )


class CityAircraftBody(WorkspaceModel):
    xM: PositiveNumber
    yM: PositiveNumber
    zM: PositiveNumber


class CityPerformanceProfile(WorkspaceModel):
    """A declared fleet estimate; values are never inferred from display geometry."""

    fleetEntryId: LogisticsIdentifier
    sourceLabel: Text
    provenance: Text
    aircraftBody: CityAircraftBody
    cruiseSpeedMps: PositiveNumber
    cruisePowerW: PositiveNumber
    hoverPowerW: PositiveNumber
    chargeEfficiency: Annotated[FiniteNumber, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def domain_shape_is_valid(self) -> CityPerformanceProfile:
        AuthoredPerformanceProfile.model_validate(self.model_dump(mode="json"))
        return self


class CityAuthoredLandscape(WorkspaceModel):
    """An author-declared visual polygon, not surveyed or Provider state."""

    id: Text
    label: Text
    provenance: Literal["authored"]
    kind: Literal["green", "plaza", "planting_strip"]
    polygon: tuple[CityPoint2, ...] = Field(min_length=3)

    @model_validator(mode="after")
    def geometry_is_valid(self) -> CityAuthoredLandscape:
        if not self.id.strip() or not self.label.strip():
            raise ValueError("authored landscape ID and label must contain text")
        validated_open_ring(tuple(XzPoint(x=p.x, z=p.z) for p in self.polygon))
        return self


class _CityWorkspaceDraftV2Base(WorkspaceModel):
    purpose: Literal["scenario-authoring"]
    schema_version: Literal["aero-bench.city-workspace/v2"]
    name: Text
    scenePath: Annotated[
        str, Field(pattern=r"^/city-presentation/[A-Za-z0-9_-]+\.json$")
    ]
    seed: NonNegativeInteger
    environment: CityDraftEnvironment
    fleet: tuple[CityFleetEntry, ...]
    traffic: CityTraffic
    facilities: tuple[CityFacility, ...]
    airspace: tuple[CityAirspace, ...]
    algorithms: CityAlgorithms
    deployment: CityDeployment
    events: tuple[CityEvent, ...]
    actionRules: tuple[CityActionRule, ...]
    stateKeyframes: tuple[CityStateKeyframe, ...]
    labelRules: tuple[CityLabelRule, ...]

    @model_validator(mode="after")
    def references_and_ids(self) -> _CityWorkspaceDraftV2Base:
        for name in (
            "fleet", "facilities", "airspace", "events", "actionRules",
            "stateKeyframes", "labelRules",
        ):
            items = getattr(self, name)
            ids = [item.id for item in items]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} contains duplicate IDs")
        facilities = {item.id: item for item in self.facilities}
        zones = {item.id for item in self.airspace}
        for craft in self.fleet:
            if craft.homeFacilityId is not None and craft.homeFacilityId not in facilities:
                raise ValueError(f"fleet {craft.id} references an unknown home facility")
        for event in self.events:
            if event.type == "airspace.activated" and event.targetId not in zones:
                raise ValueError(f"event {event.id} references unknown airspace")
            if event.type == "charger.outage":
                facility = facilities.get(event.targetId)
                if facility is None or facility.kind != "charger":
                    raise ValueError(f"event {event.id} references an unknown charger")
        return self

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> _CityWorkspaceDraftV2Base:
        return cls.model_validate(parse_json_object(raw))

    def snapshot(self) -> dict[str, object]:
        """Keep optional absence and every authored value; add no formal state."""
        return self.model_dump(mode="json", exclude_unset=True)


class CityWorkspaceDraftV2(_CityWorkspaceDraftV2Base):
    """The retired input schema accepted only by the explicit v2 migration path."""


class CityWorkspaceDraftV3(_CityWorkspaceDraftV2Base):
    """Complete v3 contract shared by the active model and explicit migration."""

    schema_version: Literal["aero-bench.city-workspace/v3"]
    orders: tuple[CityOrderRequest, ...]
    orderGeneration: CityOrderGeneration
    performanceProfiles: tuple[CityPerformanceProfile, ...]
    authoredLandscape: tuple[CityAuthoredLandscape, ...]

    @model_validator(mode="after")
    def logistics_and_landscape_references(self) -> CityWorkspaceDraftV3:
        order_ids = [order.id for order in self.orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("orders contains duplicate IDs")
        profile_ids = [profile.fleetEntryId for profile in self.performanceProfiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("performanceProfiles contains duplicate fleet entry IDs")
        landscape_ids = [item.id for item in self.authoredLandscape]
        if len(landscape_ids) != len(set(landscape_ids)):
            raise ValueError("authoredLandscape contains duplicate IDs")

        facilities = {facility.id: facility for facility in self.facilities}
        for order in self.orders:
            source = facilities.get(order.sourceFacilityId)
            if source is None:
                raise ValueError(f"order {order.id} references an unknown source facility")
            destination = facilities.get(order.destinationFacilityId)
            if destination is None:
                raise ValueError(f"order {order.id} references an unknown destination facility")
            if source.kind == "charger" or destination.kind == "charger":
                raise ValueError(f"order {order.id} uses a facility without cargo transfer")
            handoff_id = order.hubHandoffFacilityId
            if handoff_id is None:
                if source.kind != "hub" and destination.kind != "hub":
                    raise ValueError(f"order {order.id} requires an explicit hub handoff")
            else:
                if handoff_id in {order.sourceFacilityId, order.destinationFacilityId}:
                    raise ValueError(f"order {order.id} hub handoff must differ from its endpoints")
                handoff = facilities.get(handoff_id)
                if handoff is None or handoff.kind != "hub":
                    raise ValueError(f"order {order.id} references an unknown logistics hub")

        fleet_ids = {craft.id for craft in self.fleet}
        for profile in self.performanceProfiles:
            if profile.fleetEntryId not in fleet_ids:
                raise ValueError(
                    f"performance profile references unknown fleet entry {profile.fleetEntryId}"
                )
        # Workspace v2 facilities and fleet entries do not declare hub storage
        # or maximum payload. This boundary validates references and shapes only;
        # it neither invents those capabilities nor claims order feasibility.
        return self


class CityWorkspaceDraft(CityWorkspaceDraftV3):
    """The active strict workspace-v3 authoring boundary."""


def migrate_workspace_v2_payload(value: object) -> dict[str, object]:
    """Strictly validate v2 and build the exact JSON payload for the v3 parser."""

    if isinstance(value, _CityWorkspaceDraftV2Base):
        value = value.snapshot()
    draft = CityWorkspaceDraftV2.model_validate(value)
    payload = draft.snapshot()
    payload.update({
        "schema_version": WORKSPACE_V3_SCHEMA,
        "orders": [],
        "orderGeneration": {
            "seed": draft.seed,
            "maxOrders": 0,
            "startAtS": 0,
            "endAtS": 3600,
            "cargoMinKg": 0.1,
            "cargoMaxKg": 1,
            "deadlineLeadS": 600,
        },
        "performanceProfiles": [],
        "authoredLandscape": [],
    })
    return payload


def migrate_workspace_v2(value: object) -> CityWorkspaceDraft:
    """Explicitly validate a v2 draft and return the active strict v3 model."""

    return CityWorkspaceDraft.model_validate(migrate_workspace_v2_payload(value))
