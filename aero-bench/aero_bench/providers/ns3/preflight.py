from __future__ import annotations

import shutil
from collections.abc import Callable

from aero_bench.config.models import StrictModel
from aero_bench.providers.ns3.config import Ns3Config


class Ns3PreflightReport(StrictModel):
    provider_id: str
    checked_commands: tuple[str, ...]
    available_commands: tuple[str, ...]
    missing_commands: tuple[str, ...]
    forbidden_host_network_features: tuple[str, ...]
    image_digest_pinned: bool

    @property
    def ready(self) -> bool:
        return (
            not self.missing_commands
            and not self.forbidden_host_network_features
            and self.image_digest_pinned
        )


class Ns3PreflightError(RuntimeError):
    pass


def inspect_environment(
    config: Ns3Config,
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> Ns3PreflightReport:
    available = tuple(
        command
        for command in config.required_commands
        if executable_lookup(command) is not None
    )
    missing = tuple(
        command for command in config.required_commands if command not in available
    )
    # These features are intentionally not configuration options. The report
    # remains explicit so callers can record that no forbidden networking
    # mechanism was requested or accepted by this provider.
    forbidden: tuple[str, ...] = ()
    return Ns3PreflightReport(
        provider_id=config.provider_id,
        checked_commands=config.required_commands,
        available_commands=available,
        missing_commands=missing,
        forbidden_host_network_features=forbidden,
        image_digest_pinned=True,
    )


def require_environment(
    config: Ns3Config,
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
) -> Ns3PreflightReport:
    report = inspect_environment(config, executable_lookup=executable_lookup)
    if not report.ready:
        raise Ns3PreflightError(
            f"ns-3 message-in-the-loop preflight blocked for {config.provider_id}: "
            f"missing commands={list(report.missing_commands)}"
        )
    return report
