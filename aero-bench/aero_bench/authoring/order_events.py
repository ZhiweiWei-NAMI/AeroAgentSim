"""Lower editor order.created into the declared native Business schedule."""

from decimal import Decimal
import hashlib

import yaml

from aero_bench.authoring.compilation_contracts import CompilationBlocker
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import EnvironmentSpec, FileRef, TaskSpec
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    ScheduledOrderCreation,
    SCHEDULED_ARRIVAL_CAPABILITY,
)
from aero_bench.tasks.logistics_arrivals.contracts import PACKAGE_ID
from aero_bench.tasks.logistics_arrivals.contracts import LogisticsArrivalsPackage
from aero_bench.serialization import canonical_json_bytes


def lower_order_events(events, *, run, root):
    providers = [
        item
        for item in run.environment.providers
        if item.adapter == "logistics.business"
    ]
    capable = (
        run.task.package.package_id == PACKAGE_ID
        and len(providers) == 1
        and SCHEDULED_ARRIVAL_CAPABILITY in providers[0].capabilities
    )
    config = (
        LogisticsBusinessConfig.model_validate(
            BundleReader(root).validate_schema_bound_file(providers[0].config)
        )
        if capable
        else None
    )
    lowered, blockers = [], []
    for index, event in enumerate(events):
        if event.type != "order.created":
            continue
        field = f"/draft/events/{index}"
        if not capable:
            blockers.append(
                CompilationBlocker(
                    code="event.provider_capability_unavailable",
                    field=field + "/type",
                    message="order.created requires logistics.arrivals.v1 and the declared logistics.orders.scheduled-arrivals capability; current adapters: "
                    + ", ".join(item.adapter for item in run.environment.providers)
                    + ".",
                )
            )
            continue
        tick = Decimal(str(event.atS)) * 1_000_000_000 / run.environment.clock.step_ns
        try:
            if (
                tick != tick.to_integral_value()
                or not 1 <= tick <= run.environment.clock.max_steps
            ):
                raise ValueError(
                    "creation must select an exact positive tick within the run horizon"
                )
            if set(event.payload) != {"actorId", "order"}:
                raise ValueError(
                    "creation payload requires exactly actorId and canonical order"
                )
            item = ScheduledOrderCreation(
                event_id=event.id,
                at_tick=int(tick),
                actor_id=event.payload["actorId"],
                order=event.payload["order"],
            )
            if event.targetId != item.order.order_id:
                raise ValueError("targetId must equal the canonical order_id")
            if (
                item.order.release_time_s * 1_000_000_000
                < item.at_tick * run.environment.clock.step_ns
            ):
                raise ValueError("order release cannot precede native creation")
            if (
                item.order.release_time_s * 1_000_000_000
                > run.environment.clock.max_steps * run.environment.clock.step_ns
            ):
                raise ValueError(
                    "order release must be observable within the declared horizon"
                )
            candidate = tuple(
                sorted(
                    (*lowered, item), key=lambda value: (value.at_tick, value.event_id)
                )
            )
            LogisticsBusinessConfig.model_validate(
                {
                    **config.model_dump(mode="json"),
                    "scheduled_orders": [
                        value.model_dump(mode="json") for value in candidate
                    ],
                }
            )
            lowered.append(item)
        except (TypeError, ValueError) as exc:
            blockers.append(
                CompilationBlocker(
                    code="event.order_creation_invalid", field=field, message=str(exc)
                )
            )
    return tuple(
        sorted(lowered, key=lambda item: (item.at_tick, item.event_id))
    ), tuple(blockers)


def rewrite_order_schedule(*, suite_root, task, environment_ref, schedule):
    """Rewrite a private staging bundle only; publishing remains compiler-owned."""
    if task.package.package_id != PACKAGE_ID or not schedule:
        raise ValueError(
            "order schedule rewriting requires the non-physical arrivals task and nonempty schedule"
        )
    reader = BundleReader(suite_root)
    environment = reader.load_yaml(environment_ref, EnvironmentSpec)
    provider = next(
        item for item in environment.providers if item.adapter == "logistics.business"
    )
    if SCHEDULED_ARRIVAL_CAPABILITY not in provider.capabilities:
        raise ValueError(
            "cannot rewrite events without the declared scheduled-arrivals capability"
        )
    config = LogisticsBusinessConfig.model_validate(
        reader.validate_schema_bound_file(provider.config)
    )
    package = LogisticsArrivalsPackage.model_validate(
        reader.validate_schema_bound_file(task.package.config)
    )
    if package.business_config != provider.config:
        raise ValueError(
            "arrivals Task package does not pin the native Business configuration"
        )
    config = LogisticsBusinessConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "scheduled_orders": [item.model_dump(mode="json") for item in schedule],
        }
    )

    def write(reference, document):
        path = suite_root / reference.path
        raw = canonical_json_bytes(document)
        path.write_bytes(raw)
        updated = FileRef(path=reference.path, sha256=hashlib.sha256(raw).hexdigest())
        return updated.model_dump(mode="json")

    config_refs = {
        "file": write(provider.config.file, config.model_dump(mode="json")),
        "schema_file": write(
            provider.config.schema_file, LogisticsBusinessConfig.model_json_schema()
        ),
    }
    env_raw = environment.model_dump(mode="json")
    next(
        item
        for item in env_raw["providers"]
        if item["provider_id"] == provider.provider_id
    )["config"] = config_refs
    environment = EnvironmentSpec.model_validate(env_raw)
    env_path = suite_root / environment_ref.path
    env_path.write_text(
        yaml.safe_dump(environment.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    updated_env_ref = FileRef(
        path=environment_ref.path,
        sha256=hashlib.sha256(env_path.read_bytes()).hexdigest(),
    )
    package = LogisticsArrivalsPackage.model_validate(
        {**package.model_dump(mode="json"), "business_config": config_refs}
    )
    package_ref = write(task.package.config.file, package.model_dump(mode="json"))
    task_raw = task.model_dump(mode="json")
    task_raw["package"]["config"]["file"] = package_ref
    return TaskSpec.model_validate(task_raw), updated_env_ref
