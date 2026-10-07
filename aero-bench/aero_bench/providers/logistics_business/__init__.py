"""Logistics business Provider (development checkpoint).

The builtin ProviderRegistry connects this client to the native Logistics
Business RPC workload. Scheduled arrival creation and canonical non-physical
order transitions are supported. Physical pickup/handoff/deliver transitions
remain explicitly unsupported; the physical Logistics runtime flag stays false.
"""

from aero_bench.providers.logistics_business.config import (
    LogisticsBusinessConfig,
    LogisticsInfrastructurePrincipal,
)
from aero_bench.providers.logistics_business.protocol import (
    LOGISTICS_ORDER_CREATE_TOOL,
    PROTOCOL_VERSION,
)
from aero_bench.providers.logistics_business.provider import (
    ORDER_CREATED_EVENT_SCHEMA,
    LogisticsBusinessProvider,
    LogisticsBusinessProviderError,
    LogisticsBusinessProviderNotReady,
    pinned_package_digest,
)
from aero_bench.providers.logistics_business.queries import (
    LOGISTICS_QUERY_FACILITIES,
    LOGISTICS_QUERY_ORDER_DETAIL,
    LOGISTICS_QUERY_ORDERS,
    LOGISTICS_QUERY_TYPES,
    LogisticsBusinessQuery,
    LogisticsBusinessQueryResult,
    LogisticsFacilityView,
    LogisticsOrderView,
)

__all__ = [
    "LOGISTICS_ORDER_CREATE_TOOL",
    "LOGISTICS_QUERY_FACILITIES",
    "LOGISTICS_QUERY_ORDER_DETAIL",
    "LOGISTICS_QUERY_ORDERS",
    "LOGISTICS_QUERY_TYPES",
    "ORDER_CREATED_EVENT_SCHEMA",
    "PROTOCOL_VERSION",
    "LogisticsBusinessConfig",
    "LogisticsBusinessProvider",
    "LogisticsBusinessProviderError",
    "LogisticsBusinessProviderNotReady",
    "LogisticsBusinessQuery",
    "LogisticsBusinessQueryResult",
    "LogisticsFacilityView",
    "LogisticsInfrastructurePrincipal",
    "LogisticsOrderView",
    "pinned_package_digest",
]
