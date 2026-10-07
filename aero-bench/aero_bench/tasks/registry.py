from __future__ import annotations

from aero_bench.config.resolver import TaskPackageResolver
from aero_bench.runtime.hooks import RuntimeHookFactory
from aero_bench.tasks.inspection.integration import InspectionTaskPackageResolver
from aero_bench.tasks.inspection.runtime_hook import InspectionRuntimeHookFactory
from aero_bench.tasks.logistics.integration import LogisticsTaskPackageResolver
from aero_bench.tasks.logistics.native_parcel_integration import (
    NativeParcelTaskPackageResolver, NativeParcelTaskRuntimeHookFactory,
)
from aero_bench.tasks.logistics_arrivals.integration import LogisticsArrivalsTaskPackageResolver
from aero_bench.tasks.urban_recovery_demo.integration import UrbanRecoveryTaskPackageResolver
from aero_bench.tasks.urban_recovery_demo.runtime_hook import UrbanRecoveryRuntimeHookFactory


def builtin_task_package_resolvers() -> tuple[TaskPackageResolver, ...]:
    return (
        InspectionTaskPackageResolver(),
        UrbanRecoveryTaskPackageResolver(),
        LogisticsTaskPackageResolver(),
        NativeParcelTaskPackageResolver(),
        LogisticsArrivalsTaskPackageResolver(),
    )


def builtin_runtime_hook_factories() -> tuple[RuntimeHookFactory, ...]:
    return (InspectionRuntimeHookFactory(), UrbanRecoveryRuntimeHookFactory(),
            NativeParcelTaskRuntimeHookFactory())


__all__ = ["builtin_runtime_hook_factories", "builtin_task_package_resolvers"]
