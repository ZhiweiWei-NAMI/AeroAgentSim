from __future__ import annotations

import shutil
from collections.abc import Callable

from aero_bench.config.models import StrictModel
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig


class Px4PreflightReport(StrictModel):
    provider_id: str
    checked_commands: tuple[str, ...]
    available_commands: tuple[str, ...]
    missing_commands: tuple[str, ...]
    image_digest_pinned: bool

    @property
    def ready(self) -> bool:
        return not self.missing_commands and self.image_digest_pinned


class Px4PreflightError(RuntimeError):
    pass


def inspect_environment(
    config: Px4GazeboConfig,
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> Px4PreflightReport:
    """Check only real prerequisites; no local or simplified replacement is selected."""

    available = tuple(
        command
        for command in config.required_commands
        if executable_lookup(command) is not None
    )
    missing = tuple(
        command for command in config.required_commands if command not in available
    )
    return Px4PreflightReport(
        provider_id=config.provider_id,
        checked_commands=config.required_commands,
        available_commands=available,
        missing_commands=missing,
        image_digest_pinned=True,
    )


def require_environment(
    config: Px4GazeboConfig,
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> Px4PreflightReport:
    report = inspect_environment(config, executable_lookup=executable_lookup)
    if not report.ready:
        raise Px4PreflightError(
            f"PX4/Gazebo/MAVSDK preflight blocked for {config.provider_id}: "
            f"missing commands={list(report.missing_commands)}"
        )
    return report
