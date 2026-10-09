"""Docker is opt-in; all artifact/cache writes remain inside the caller's scratch root."""

import os
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: real native Docker integration")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("markexpr") == "docker":
        return
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="opt in with -m docker"))


@pytest.fixture(autouse=True)
def standalone_docker_images(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Opt into standalone images without changing checked-in scenarios or launcher semantics.

    Supply all three AEROAGENTSIM_IMAGE_{PX4,SUMO,NS3} variables. Results and
    pytest temporary files stay in the caller's authorized --basetemp directory.
    """
    if request.node.get_closest_marker("docker") is None:
        return
    backends = {"PX4": "px4-gazebo", "SUMO": "sumo", "NS3": "ns3"}
    overrides = {key: os.environ.get(f"AEROAGENTSIM_IMAGE_{key}") for key in backends}
    if not any(value is not None for value in overrides.values()):
        return
    if not all(overrides.values()):
        pytest.fail("standalone Docker verification requires all three image overrides")
    from aeroagentsim.adapters import container, runner

    originals = {
        "PX4": "aeroagentsim/px4-gazebo:dev",
        "SUMO": "aeroagentsim/sumo:dev",
        "NS3": "aeroagentsim/ns3:dev",
    }
    mapping = {}
    for key, backend in backends.items():
        image = overrides[key]
        if image is None or not image.startswith(f"aeroagentsim/{backend}:standalone"):
            pytest.fail(f"invalid standalone image override for {key}: {image!r}")
        mapping[originals[key]] = image
    monkeypatch.setattr(container, "_ALLOWED_IMAGES", frozenset(mapping.values()))
    monkeypatch.setattr(container, "_JOB_LABEL_VALUE", "docker-test")
    monkeypatch.setattr(container, "_CPUS", "16")
    launcher = runner.DockerContainer

    def overridden_container(image, *args, **kwargs):
        return launcher(mapping.get(image, image), *args, **kwargs)

    monkeypatch.setattr(runner, "DockerContainer", overridden_container)
    if hasattr(request.module, "MEASUREMENTS"):
        directory = Path(request.config._tmp_path_factory.getbasetemp())
        monkeypatch.setattr(request.module, "MEASUREMENTS", directory / "measurements.json")
