"""Strict editor lowering fixtures; these are not formal execution evidence."""

import hashlib
from types import SimpleNamespace

import pytest

from aero_bench.authoring.order_events import lower_order_events
from aero_bench.authoring.workspace import CityEvent
from aero_bench.config.models import ClockSpec, FileRef, ProviderRef, SchemaBoundFile
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    SCHEDULED_ARRIVAL_CAPABILITY,
)
from aero_bench.serialization import canonical_json_bytes
from tests.providers.test_logistics_scheduled_arrivals import scheduled_config
from tests.providers.test_logistics_business_service import _contract, SERVICE_PORT


def declared_run(root, *, capability=True, package_id="logistics.arrivals.v1"):
    root.mkdir()

    def write(name, value):
        raw = canonical_json_bytes(value)
        (root / name).write_bytes(raw)
        return FileRef(path=name, sha256=hashlib.sha256(raw).hexdigest())

    config = scheduled_config()
    refs = SchemaBoundFile(
        file=write("business.json", config),
        schema_file=write(
            "business.schema.json", LogisticsBusinessConfig.model_json_schema()
        ),
    )
    raw = _contract(
        {
            "config": refs.file.model_dump(mode="json"),
            "schema": refs.schema_file.model_dump(mode="json"),
        },
        SERVICE_PORT,
    ).provider.model_dump(mode="json")
    raw["config"] = refs.model_dump(mode="json")
    if capability:
        raw["capabilities"].append(SCHEDULED_ARRIVAL_CAPABILITY)
    return SimpleNamespace(
        task=SimpleNamespace(package=SimpleNamespace(package_id=package_id)),
        environment=SimpleNamespace(
            providers=(ProviderRef.model_validate(raw),),
            clock=ClockSpec(
                authority="provider_barrier",
                step_ns=1_000_000_000,
                max_steps=2,
                provider_timeout_ms=10000,
            ),
        ),
    )


def event(**changes):
    scheduled = scheduled_config()["scheduled_orders"][0]
    raw = {
        "id": scheduled["event_id"],
        "type": "order.created",
        "atS": 1.0,
        "targetId": scheduled["order"]["order_id"],
        "payload": {"actorId": scheduled["actor_id"], "order": scheduled["order"]},
    }
    raw.update(changes)
    return CityEvent.model_validate(raw)


def test_order_created_lowers_to_exact_native_schedule(tmp_path):
    root = tmp_path / "input"
    run = declared_run(root)
    lowered, blockers = lower_order_events((event(),), run=run, root=root)
    assert blockers == ()
    assert (
        lowered[0].model_dump(mode="json") == scheduled_config()["scheduled_orders"][0]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"atS": 0.5},
        {"atS": 3},
        {"targetId": "another.order"},
        {"payload": {"authored": True}},
    ],
)
def test_order_creation_rejects_unsupported_editor_inputs(tmp_path, changes):
    root = tmp_path / "input"
    run = declared_run(root)
    lowered, blockers = lower_order_events((event(**changes),), run=run, root=root)
    assert not lowered
    assert blockers[0].code == "event.order_creation_invalid"


@pytest.mark.parametrize(
    "capability,package", [(False, "logistics.arrivals.v1"), (True, "inspection.v1")]
)
def test_order_creation_requires_declared_native_profile_and_capability(
    tmp_path, capability, package
):
    root = tmp_path / "input"
    run = declared_run(root, capability=capability, package_id=package)
    lowered, blockers = lower_order_events((event(),), run=run, root=root)
    assert not lowered
    assert blockers[0].code == "event.provider_capability_unavailable"


def test_duplicate_editor_order_creation_is_a_compiler_blocker(tmp_path):
    root = tmp_path / "input"
    run = declared_run(root)
    lowered, blockers = lower_order_events((event(), event()), run=run, root=root)
    assert len(lowered) == 1 and len(blockers) == 1
