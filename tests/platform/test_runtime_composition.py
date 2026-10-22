"""Runtime composition follows resolved factories, not reserved plugin names."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel import Partition
from aerokernel.sdk import ContextEngine

from aeroagentsim.engines import behaviour, kinematic, predicate
from aeroagentsim.models import MotionModel
from aeroagentsim.platform import Simulation, plugins
from aeroagentsim.platform import simulation as runtime
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog, Factory
from aeroagentsim.scenario import Scenario, load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "scenarios"


class InstalledEngine(ContextEngine):
    """Declare authored authority without depending on any builtin config ABI."""

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        fields = {
            field
            for rule in build.manifest.rules
            if rule.partition == build.id
            for field in rule.fields
        }
        fields.update(
            exact.field
            for exact in build.manifest.exact
            if exact.partition == build.id
        )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=tuple(sorted(fields)),
                lifecycle=True,
            )
        )


def installed_catalog(
    monkeypatch: pytest.MonkeyPatch, factories: dict[str, Factory]
) -> EngineCatalog:
    entries = tuple(
        SimpleNamespace(name=name, load=lambda factory=factory: factory)
        for name, factory in factories.items()
    )

    def query(*, group: str) -> tuple[SimpleNamespace, ...]:
        assert group == "aeroagentsim.engines"
        return entries

    monkeypatch.setattr(plugins, "entry_points", query)
    return EngineCatalog()


def predicate_scenario(*, with_behaviour: bool = True) -> Scenario:
    scenario = load_scenario(SCENARIOS / "predicates-demo.yaml")
    if not with_behaviour:
        return scenario
    document = copy.deepcopy(scenario.document)
    document["engines"]["behaviour"] = {
        "plugin": "behaviour",
        "config": {"packages": []},
    }
    return load_scenario(document, base=SCENARIOS)


def motion_document() -> dict[str, Any]:
    document = copy.deepcopy(load_scenario(SCENARIOS / "p1-slice.yaml").document)
    document["entities"] = document["entities"][:1]
    document["engines"] = {"motion": document["engines"]["motion"]}
    document["bindings"] = {
        "rules": [document["bindings"]["rules"][0]],
        "lifecycle": [document["bindings"]["lifecycle"][0]],
    }
    document.pop("presentation", None)
    return document


def test_installed_predicate_is_not_replaced_by_recorded_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = predicate_scenario()
    calls: list[EngineBuild] = []

    def installed(build: EngineBuild) -> predicate.Predicate:
        calls.append(build)
        # Even an explicit forwarding factory owns its choice of adapter.
        return predicate.build(build)

    catalog = installed_catalog(
        monkeypatch, {"predicate": installed, "behaviour": behaviour.build}
    )
    simulation = Simulation(scenario, catalog=catalog)
    try:
        engines = {engine.partitions[0].id: engine for engine in simulation.engines}
        assert [build.id for build in calls] == ["predicate"]
        assert calls[0].config == scenario.engines["predicate"]["config"]
        assert type(engines["behaviour"]) is behaviour.Behaviour
        assert type(engines["predicate"]) is predicate.Predicate
        assert (
            "aas.behaviour.predicate_evaluated"
            not in engines["predicate"].partition.emits
        )
    finally:
        simulation.close()


def test_installed_behaviour_does_not_convert_builtin_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = predicate_scenario()
    calls: list[EngineBuild] = []

    def installed(build: EngineBuild) -> InstalledEngine:
        calls.append(build)
        return InstalledEngine(build)

    catalog = installed_catalog(
        monkeypatch, {"predicate": predicate.build, "behaviour": installed}
    )
    simulation = Simulation(scenario, catalog=catalog)
    try:
        engines = {engine.partitions[0].id: engine for engine in simulation.engines}
        assert [build.id for build in calls] == ["behaviour"]
        assert type(engines["behaviour"]) is InstalledEngine
        assert type(engines["predicate"]) is predicate.Predicate
        assert (
            "aas.behaviour.predicate_evaluated"
            not in engines["predicate"].partition.emits
        )
    finally:
        simulation.close()


@pytest.mark.parametrize("installed_builtins", [False, True])
@pytest.mark.parametrize("with_behaviour", [False, True])
def test_builtin_predicate_composition_survives_builtin_entry_points(
    monkeypatch: pytest.MonkeyPatch,
    installed_builtins: bool,
    with_behaviour: bool,
) -> None:
    scenario = predicate_scenario(with_behaviour=with_behaviour)
    factories: dict[str, Factory] = {}
    if installed_builtins:
        # The distribution itself installs these genuine builtin factories.
        factories["predicate"] = predicate.build
        if with_behaviour:
            factories["behaviour"] = behaviour.build
    catalog = installed_catalog(monkeypatch, factories)
    simulation = Simulation(scenario, catalog=catalog)
    try:
        engines = {engine.partitions[0].id: engine for engine in simulation.engines}
        expected = (
            behaviour.RecordedPredicate if with_behaviour else predicate.Predicate
        )
        assert type(engines["predicate"]) is expected
        if with_behaviour:
            assert type(engines["behaviour"]) is behaviour.Behaviour
            assert (
                "aas.behaviour.predicate_evaluated"
                in engines["predicate"].partition.emits
            )
        simulation.start()
        view = simulation.run_until(1_000_000_000)
        assert view.sample_frames("stationary")
        if with_behaviour:
            assert engines["predicate"].prior_record is not None
    finally:
        simulation.close()


def test_installed_kinematic_does_not_prebuild_builtin_motion_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = motion_document()
    # Intentionally lacks the builtin motion_model's step_ns/energy contract.
    config = {"backend": "vendor-native", "connection": {"fleet": "external"}}
    document["engines"]["motion"]["config"] = config
    scenario = load_scenario(document, base=SCENARIOS)
    calls: list[EngineBuild] = []

    def installed(build: EngineBuild) -> InstalledEngine:
        calls.append(build)
        assert build.config == config
        assert build.models == {}
        return InstalledEngine(build)

    def unexpected_model(*args: Any, **kwargs: Any) -> MotionModel:
        raise AssertionError("installed kinematic config reached builtin motion_model")

    monkeypatch.setattr(runtime, "motion_model", unexpected_model)
    catalog = installed_catalog(monkeypatch, {"kinematic": installed})
    simulation = Simulation(scenario, catalog=catalog)
    try:
        assert [build.id for build in calls] == ["motion"]
        assert len(simulation.engines) == 1
        assert type(simulation.engines[0]) is InstalledEngine
        assert calls[0].config == config
        assert calls[0].models == {}
    finally:
        simulation.close()


@pytest.mark.parametrize("installed_builtin", [False, True])
def test_builtin_motion_model_is_prebuilt_once_and_shared_before_mover_build(
    monkeypatch: pytest.MonkeyPatch, installed_builtin: bool
) -> None:
    document = motion_document()
    # A planner can sort before its mover and must still see the same model.
    document["engines"]["a-planner"] = {"plugin": "model-probe", "config": {}}
    scenario = load_scenario(document, base=SCENARIOS)
    observed: list[MotionModel] = []
    created: list[MotionModel] = []
    original = runtime.motion_model

    def probe(build: EngineBuild) -> InstalledEngine:
        observed.append(build.models["motion"])
        return InstalledEngine(build)

    def counted_model(*args: Any, **kwargs: Any) -> MotionModel:
        model = original(*args, **kwargs)
        created.append(model)
        return model

    monkeypatch.setattr(runtime, "motion_model", counted_model)
    monkeypatch.setattr(kinematic, "motion_model", counted_model)
    factories: dict[str, Factory] = {"model-probe": probe}
    if installed_builtin:
        factories["kinematic"] = kinematic.build
    catalog = installed_catalog(monkeypatch, factories)
    simulation = Simulation(scenario, catalog=catalog)
    try:
        planner, mover = simulation.engines
        assert type(planner) is InstalledEngine
        assert type(mover) is kinematic.Kinematic
        assert len(created) == len(observed) == 1
        assert created[0] is observed[0] is mover.model
        assert planner.build.models is mover.build.models
        assert mover.consumption_model is observed[0].consumption
    finally:
        simulation.close()


def test_public_platform_import_is_headless_in_fresh_python(tmp_path: Path) -> None:
    # Collection/conftest may already import services in this pytest process.
    script = """
import json
import sys

import aeroagentsim.platform as platform
from aeroagentsim import RunSession, Simulation

assert platform.RunSession is RunSession
assert platform.Simulation is Simulation
print(json.dumps(sorted(sys.modules)))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "src"), str(ROOT / "aerokernel"), env.get("PYTHONPATH", ""))
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    loaded = json.loads(result.stdout)
    forbidden = (
        "aeroagentsim.services",
        "fastapi",
        "starlette",
        "uvicorn",
        "httpx",
        "httpx2",
        "aiohttp",
        "aeroagentsim.core",
        "simpy",
    )
    unexpected = sorted(
        name
        for name in loaded
        if any(name == root or name.startswith(root + ".") for root in forbidden)
    )
    assert not unexpected, f"headless platform import loaded: {unexpected}"
