from __future__ import annotations

import shutil
from collections.abc import Callable

from aero_bench.config.models import StrictModel
from aero_bench.providers.sumo.config import SumoConfig


class SumoPreflightReport(StrictModel):
    provider_id: str
    checked_commands: tuple[str, ...]
    available_commands: tuple[str, ...]
    missing_commands: tuple[str, ...]
    image_digest_pinned: bool

    @property
    def ready(self) -> bool:
        return not self.missing_commands and self.image_digest_pinned


class SumoPreflightError(RuntimeError):
    pass


def inspect_environment(
    config: SumoConfig,
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> SumoPreflightReport:
    available = tuple(
        command
        for command in config.required_commands
        if executable_lookup(command) is not None
    )
    missing = tuple(
        command for command in config.required_commands if command not in available
    )
    return SumoPreflightReport(
        provider_id=config.provider_id,
        checked_commands=config.required_commands,
        available_commands=available,
        missing_commands=missing,
        image_digest_pinned=True,
    )


def require_environment(
    config: SumoConfig,
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> SumoPreflightReport:
    report = inspect_environment(config, executable_lookup=executable_lookup)
    if not report.ready:
        raise SumoPreflightError(
            f"SUMO TraCI preflight blocked for {config.provider_id}: "
            f"missing commands={list(report.missing_commands)}"
        )
    return report
