from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aero_bench.config.loader import BundleReader
from aero_bench.config.resolver import (
    ResolvedRunSpec,
    TaskPackageResolver,
    validate_runtime_run_bundle,
)
from aero_bench.executor.contracts import HarnessWorkloadContract
from aero_bench.providers.contracts import ProviderSession
from aero_bench.providers.registry import ProviderRegistry, RuntimeEndpoint
from aero_bench.runtime.harness import HarnessCoordinator
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes


_ENVIRONMENT_KEYS = (
    "AERO_BENCH_CONTRACT",
    "AERO_BENCH_BUNDLE_DIR",
    "AERO_BENCH_ARTIFACT_DIR",
    "AERO_BENCH_RUN_ID",
    "AERO_BENCH_SEED",
    "AERO_BENCH_ROLE",
    "AERO_BENCH_WORKLOAD_ID",
    "AERO_BENCH_PROVIDER_ENDPOINTS",
    "AERO_BENCH_PROVIDER_CREDENTIALS",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")


class HarnessBootstrapError(ValueError):
    """Raised when a harness workload environment is not self-consistent."""


def _exception_chain_summary(error: BaseException) -> str:
    """Return a bounded diagnostic without discarding the underlying cause."""

    details: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = " ".join(str(current).split())
        details.append(
            f"{type(current).__name__}: {message}"
            if message
            else type(current).__name__
        )
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return " <- ".join(details)[:4_096]


@dataclass(frozen=True, slots=True)
class HarnessBootstrap:
    """The immutable result of deterministic harness process bootstrap."""

    run: ResolvedRunSpec
    coordinator: HarnessCoordinator
    ledger: EventLedger
    artifact_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.run, ResolvedRunSpec):
            raise TypeError("HarnessBootstrap.run must be ResolvedRunSpec")
        if not isinstance(self.coordinator, HarnessCoordinator):
            raise TypeError("HarnessBootstrap.coordinator must be HarnessCoordinator")
        if not isinstance(self.ledger, EventLedger):
            raise TypeError("HarnessBootstrap.ledger must be EventLedger")
        if self.ledger.records:
            raise ValueError("Harness bootstrap requires a fresh EventLedger")
        if not isinstance(self.artifact_root, Path):
            raise TypeError("HarnessBootstrap.artifact_root must be Path")


def bootstrap_harness(
    environment: Mapping[str, str],
    *,
    registry: ProviderRegistry,
    task_package_resolvers: tuple[TaskPackageResolver, ...],
) -> HarnessBootstrap:
    """Build a harness coordinator from explicit, validated workload inputs.

    Bootstrap validates process identity, the complete runtime-authorized contract
    and bundle inputs, Provider endpoint bindings, and the executor-issued
    per-provider credential map. It deliberately does not call ``prepare`` or
    advance the Provider barrier.
    """

    if not task_package_resolvers:
        raise HarnessBootstrapError(
            "task_package_resolvers must be explicit and non-empty"
        )

    values = {
        key: _required_environment_value(environment, key) for key in _ENVIRONMENT_KEYS
    }
    _require_identity(values)

    contract_path = _safe_existing_path(
        values["AERO_BENCH_CONTRACT"],
        key="AERO_BENCH_CONTRACT",
        expected="file",
    )
    bundle_root = _safe_existing_path(
        values["AERO_BENCH_BUNDLE_DIR"],
        key="AERO_BENCH_BUNDLE_DIR",
        expected="directory",
    )
    artifact_root = _safe_existing_path(
        values["AERO_BENCH_ARTIFACT_DIR"],
        key="AERO_BENCH_ARTIFACT_DIR",
        expected="directory",
        require_writable=True,
    )
    _require_empty_artifact_root(artifact_root)

    contract = _load_contract(contract_path)
    run = contract.run
    _require_run_identity(run, values)
    # Registry drift gate before ANY side effect: compare every Provider's
    # adapter -> registered runtime stage -> serialized ResolvedProvider stage
    # (and the exact Provider set). This runs before any session builder is
    # invoked so a drifted, unregistered, or extra Provider fails with zero
    # Provider-session side effects.
    try:
        registry.verify_runtime_stage_binding(
            environment_providers=run.environment.providers,
            resolved_providers=run.scenario.providers,
        )
    except Exception as error:
        raise HarnessBootstrapError(
            "Provider runtime stage drift between registry and ResolvedRun: "
            f"{_exception_chain_summary(error)}"
        ) from error
    endpoints = _load_provider_endpoints(values["AERO_BENCH_PROVIDER_ENDPOINTS"])

    try:
        bundle = validate_runtime_run_bundle(
            run,
            bundle_root=bundle_root,
            task_package_resolvers=task_package_resolvers,
            provider_registry=registry,
        )
    except Exception as error:
        raise HarnessBootstrapError(
            "ResolvedRun bundle/task-package revalidation failed: "
            f"{_exception_chain_summary(error)}"
        ) from error

    declared_providers = {
        provider.provider_id: provider for provider in run.environment.providers
    }
    _require_exact_provider_endpoints(declared_providers, endpoints)
    credentials = _load_provider_credentials(
        values["AERO_BENCH_PROVIDER_CREDENTIALS"], declared_providers
    )

    sessions: dict[str, ProviderSession] = {}
    for provider_id in sorted(declared_providers):
        provider = declared_providers[provider_id]
        endpoint = endpoints[provider_id]
        if endpoint.port != provider.port:
            raise HarnessBootstrapError(
                f"Provider endpoint port does not match {provider_id}: "
                f"expected {provider.port}, got {endpoint.port}"
            )
        _revalidate_provider_config(bundle, provider)
        try:
            sessions[provider_id] = registry.build_session(
                provider=provider,
                bundle=bundle,
                runtime_endpoint=endpoint,
                run_id=run.run_id,
                credential=credentials[provider_id],
                scenario=run.scenario,
                clock=run.environment.clock,
            )
        except Exception as error:
            raise HarnessBootstrapError(
                f"failed to build Provider session for {provider_id}: {error}"
            ) from error

    ledger = EventLedger(run_id=run.run_id)
    try:
        coordinator = HarnessCoordinator(
            run=run,
            providers=sessions,
            ledger=ledger,
            # The artifact mount's parent is read-only in production. Private
            # staging belongs on the workload's scratch mount, not in artifacts.
            scene_history_staging_path=(
                Path(tempfile.gettempdir())
                / f".{artifact_root.name}.{run.run_id}.scene-state-history.jsonl"
                if run.task.package.package_id == "urban.uav-recovery-demo.v1"
                else None
            ),
        )
    except (TypeError, ValueError) as error:
        raise HarnessBootstrapError(
            f"harness/provider contract identity is invalid: {error}"
        ) from error
    return HarnessBootstrap(
        run=run,
        coordinator=coordinator,
        ledger=ledger,
        artifact_root=artifact_root,
    )


def _required_environment_value(environment: Mapping[str, str], key: str) -> str:
    try:
        value = environment[key]
    except (KeyError, TypeError) as error:
        raise HarnessBootstrapError(
            f"missing required environment value: {key}"
        ) from error
    if not isinstance(value, str) or not value:
        raise HarnessBootstrapError(
            f"environment value must be a non-empty string: {key}"
        )
    return value


def _require_identity(values: Mapping[str, str]) -> None:
    if values["AERO_BENCH_ROLE"] != "harness":
        raise HarnessBootstrapError("AERO_BENCH_ROLE must be harness")
    if values["AERO_BENCH_WORKLOAD_ID"] != "harness":
        raise HarnessBootstrapError("AERO_BENCH_WORKLOAD_ID must be harness")
    if _SHA256.fullmatch(values["AERO_BENCH_RUN_ID"]) is None:
        raise HarnessBootstrapError(
            "AERO_BENCH_RUN_ID must be a lowercase SHA-256 digest"
        )
    if values["AERO_BENCH_RUN_ID"] == "0" * 64:
        raise HarnessBootstrapError("AERO_BENCH_RUN_ID cannot be a placeholder digest")
    if _DECIMAL_INTEGER.fullmatch(values["AERO_BENCH_SEED"]) is None:
        raise HarnessBootstrapError(
            "AERO_BENCH_SEED must be a canonical decimal integer"
        )


def _require_run_identity(run: ResolvedRunSpec, values: Mapping[str, str]) -> None:
    if run.run_id != values["AERO_BENCH_RUN_ID"]:
        raise HarnessBootstrapError(
            "AERO_BENCH_RUN_ID does not match the ResolvedRun run_id"
        )
    if run.seed != int(values["AERO_BENCH_SEED"]):
        raise HarnessBootstrapError(
            "AERO_BENCH_SEED does not match the ResolvedRun seed"
        )


def _safe_existing_path(
    value: str,
    *,
    key: str,
    expected: str,
    require_writable: bool = False,
) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or path.anchor != "/"
        or str(path) != value
        or "\\" in value
        or "\x00" in value
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise HarnessBootstrapError(
            f"{key} must be an absolute normalized path without traversal"
        )
    _reject_symlink_components(path, key=key)
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise HarnessBootstrapError(f"{key} is unavailable: {value}") from error
    if expected == "file" and not stat.S_ISREG(mode):
        raise HarnessBootstrapError(f"{key} must name a regular file")
    if expected == "directory" and not stat.S_ISDIR(mode):
        raise HarnessBootstrapError(f"{key} must name a directory")
    required_access = os.R_OK if expected == "file" else os.R_OK | os.X_OK
    if not os.access(path, required_access):
        raise HarnessBootstrapError(f"{key} is not readable: {value}")
    if expected == "directory" and require_writable and not os.access(path, os.W_OK):
        raise HarnessBootstrapError(f"{key} is not writable: {value}")
    return path


def _reject_symlink_components(path: Path, *, key: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise HarnessBootstrapError(f"{key} is unavailable: {path}") from error
        if stat.S_ISLNK(mode):
            raise HarnessBootstrapError(f"{key} cannot contain a symbolic link")


def _require_empty_artifact_root(path: Path) -> None:
    try:
        entries = tuple(path.iterdir())
    except OSError as error:
        raise HarnessBootstrapError(
            "AERO_BENCH_ARTIFACT_DIR cannot be inspected"
        ) from error
    if entries:
        raise HarnessBootstrapError(
            "AERO_BENCH_ARTIFACT_DIR must be empty at harness start"
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _parse_canonical_json(raw: bytes | str, *, label: str) -> object:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    try:
        value = json.loads(
            encoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HarnessBootstrapError(f"{label} is not strict JSON") from error
    canonical = canonical_json_bytes(value)
    if encoded not in (canonical, canonical + b"\n"):
        raise HarnessBootstrapError(f"{label} is not canonical JSON")
    return value


def _load_contract(path: Path) -> HarnessWorkloadContract:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise HarnessBootstrapError("AERO_BENCH_CONTRACT cannot be read") from error
    value = _parse_canonical_json(raw, label="AERO_BENCH_CONTRACT")
    if not isinstance(value, dict):
        raise HarnessBootstrapError("AERO_BENCH_CONTRACT must contain an object")
    try:
        contract = HarnessWorkloadContract.model_validate(value)
    except (TypeError, ValueError) as error:
        raise HarnessBootstrapError(
            "AERO_BENCH_CONTRACT is not a valid HarnessWorkloadContract v6"
        ) from error
    if canonical_json_bytes(contract.model_dump(mode="json")) not in (
        raw,
        raw[:-1] if raw.endswith(b"\n") else b"",
    ):
        raise HarnessBootstrapError("AERO_BENCH_CONTRACT is not canonical JSON")
    return contract


def _load_provider_endpoints(raw: str) -> dict[str, RuntimeEndpoint]:
    value = _parse_canonical_json(raw, label="AERO_BENCH_PROVIDER_ENDPOINTS")
    if not isinstance(value, dict):
        raise HarnessBootstrapError("AERO_BENCH_PROVIDER_ENDPOINTS must be an object")
    endpoints: dict[str, RuntimeEndpoint] = {}
    for provider_id, endpoint_raw in value.items():
        try:
            endpoints[provider_id] = RuntimeEndpoint.model_validate(endpoint_raw)
        except (TypeError, ValueError) as error:
            raise HarnessBootstrapError(
                f"Provider endpoint is invalid for {provider_id}"
            ) from error
    identities = [(endpoint.host, endpoint.port) for endpoint in endpoints.values()]
    if len(identities) != len(set(identities)):
        raise HarnessBootstrapError(
            "AERO_BENCH_PROVIDER_ENDPOINTS contains duplicate endpoints"
        )
    return endpoints


def _require_exact_provider_endpoints(
    providers: Mapping[str, Any], endpoints: Mapping[str, RuntimeEndpoint]
) -> None:
    provider_ids = set(providers)
    endpoint_ids = set(endpoints)
    missing = sorted(provider_ids - endpoint_ids)
    unknown = sorted(endpoint_ids - provider_ids)
    if missing or unknown:
        raise HarnessBootstrapError(
            "Provider endpoints must exactly match ResolvedRun providers: "
            f"missing={missing}, unknown={unknown}"
        )


def _load_provider_credentials(
    raw: str,
    providers: Mapping[str, Any],
) -> dict[str, str]:
    """Parse the executor-issued per-provider credential map.

    The map is the only channel that carries provider credentials into the
    Harness. It must be canonical JSON keyed by exactly the declared provider
    IDs, and every value must be a non-placeholder SHA-256 secret as issued
    by the executor.
    """

    value = _parse_canonical_json(raw, label="AERO_BENCH_PROVIDER_CREDENTIALS")
    if not isinstance(value, dict):
        raise HarnessBootstrapError(
            "AERO_BENCH_PROVIDER_CREDENTIALS must be a JSON object"
        )
    credentials: dict[str, str] = {}
    for provider_id, token in value.items():
        if (
            not isinstance(token, str)
            or _SHA256.fullmatch(token) is None
            or token == "0" * 64
        ):
            raise HarnessBootstrapError(
                f"Provider credential is not an executor-issued secret for "
                f"{provider_id}"
            )
        credentials[provider_id] = token
    missing = sorted(set(providers) - set(credentials))
    unknown = sorted(set(credentials) - set(providers))
    if missing or unknown:
        raise HarnessBootstrapError(
            "Provider credentials must exactly match ResolvedRun providers: "
            f"missing={missing}, unknown={unknown}"
        )
    if len(set(credentials.values())) != len(credentials):
        raise HarnessBootstrapError(
            "Provider credentials must be distinct per declared provider"
        )
    return credentials


def _revalidate_provider_config(bundle: BundleReader, provider: Any) -> None:
    try:
        _reject_bundle_symlinks(bundle, provider.config.file.path)
        _reject_bundle_symlinks(bundle, provider.config.schema_file.path)
        bundle.validate_schema_bound_file(provider.config)
    except (OSError, ValueError) as error:
        raise HarnessBootstrapError(
            f"Provider config failed bundle revalidation: {provider.provider_id}"
        ) from error


def _reject_bundle_symlinks(bundle: BundleReader, relative_path: str) -> None:
    candidate = bundle.root / relative_path
    _reject_symlink_components(candidate, key=f"bundle file {relative_path}")


__all__ = ["HarnessBootstrap", "HarnessBootstrapError", "bootstrap_harness"]
