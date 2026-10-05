"""Task-scoped logistics order lifecycle, an authoring/planning domain component.

Stage 3 defines the immutable order request/state/transition contracts and the
pure ``advance_order`` transition function with optimistic version checks.
Orders are hub-mediated: the canonical hub is resolved against the explicit
canonical facility catalogue lowered from the full current frontend facility
records (``compile_authored_facilities``), the itinerary legs are immutable, and
final delivery requires an observed hub handoff that names the canonical hub.
Pickup, handoff and delivery confirmations bind explicit external evidence
references; this package never fabricates telemetry, delivery evidence, or Run
IDs.
"""

from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    LOGISTICS_REQUIRED_CAPABILITIES,
    LOGISTICS_SCHEMA_VERSION,
    LogisticsSceneBinding,
    LogisticsTaskPackage,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.facilities import (
    AuthoredFacility,
    CargoCapability,
    FacilityCapabilities,
    FacilityCatalogue,
    FacilityIdentifier,
    FacilityKind,
    FacilityPlacement,
    FacilityPosition,
    compile_authored_facilities,
)
from aero_bench.tasks.logistics.facility_catalog import (
    cargo_transfer_allowed,
    resolve_canonical_hub,
)
from aero_bench.tasks.logistics.integration import (
    LOGISTICS_RUNTIME_IMPLEMENTED,
    LogisticsPackageResolutionError,
    LogisticsTaskPackageResolver,
    load_logistics_package,
)
from aero_bench.tasks.logistics.orders import (
    LogisticsHistoryRecord,
    LogisticsLedgerState,
    LogisticsOrdersService,
    LogisticsTransitionResult,
    OrderActorGrant,
    OrderActorRole,
    OrderEvent,
    OrderItinerary,
    OrderItineraryLeg,
    OrderRequest,
    OrderState,
    OrderStatus,
    OrderTransition,
    advance_order,
    build_order_itinerary,
    replay_logistics_history,
)

__all__ = [
    "AuthoredFacility",
    "CargoCapability",
    "FacilityCapabilities",
    "FacilityCatalogue",
    "FacilityIdentifier",
    "FacilityKind",
    "FacilityPlacement",
    "FacilityPosition",
    "LOGISTICS_PACKAGE_ID",
    "LOGISTICS_REQUIRED_CAPABILITIES",
    "LOGISTICS_RUNTIME_IMPLEMENTED",
    "LOGISTICS_SCHEMA_VERSION",
    "LogisticsHistoryRecord",
    "LogisticsLedgerState",
    "LogisticsOrdersService",
    "LogisticsPackageResolutionError",
    "LogisticsSceneBinding",
    "LogisticsTaskPackage",
    "LogisticsTaskPackageResolver",
    "LogisticsTransitionResult",
    "OrderActorGrant",
    "OrderActorRole",
    "OrderEvent",
    "OrderItinerary",
    "OrderItineraryLeg",
    "OrderRequest",
    "OrderState",
    "OrderStatus",
    "OrderTransition",
    "advance_order",
    "build_order_itinerary",
    "cargo_transfer_allowed",
    "compile_authored_facilities",
    "load_logistics_package",
    "lower_logistics_task_package",
    "replay_logistics_history",
    "resolve_canonical_hub",
]