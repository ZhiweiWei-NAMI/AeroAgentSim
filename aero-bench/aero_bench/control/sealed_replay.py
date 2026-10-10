"""Read-only delivery of existing CLI evidence; never restore a runtime session."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.loader import load_suite, sha256_file
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.control.contracts import (
    CatalogRun,
    ControlCatalog,
    ReplayAccessResponse,
    RunAccessCredentials,
)
from aero_bench.control.manager import (
    ControlManagerError,
    ControlRunManager,
    _MAX_PUBLIC_ASSET_BYTES,
    _MAX_PUBLIC_REPLAY_MANIFEST_BYTES,
    _MAX_PUBLIC_TRACE_BYTES,
)
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.providers.rpc import parse_json_object
from aero_bench.runner.contracts import RunnerSummary, RunSummary
from aero_bench.runner.execution import (
    _seal_identity,
    _verification_identity,
    load_runner_config,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.contracts import (
    PublicReplayIndex,
    PublicReplayManifest,
    PublicTrace,
)
from aero_bench.trace.projector import (
    SealedPublicArtifacts,
    _project_report,
    project_public_scenario,
)
from aero_bench.verifier.output import load_and_validate_verification_output


def _open_file(path: Path):
    """Walk directory descriptors so links cannot redirect any path component."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("sealed path must be absolute and normalized")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        result = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
    finally:
        os.close(descriptor)
    return os.fdopen(result, "rb")


def _path(root: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if (
        value.is_absolute()
        or value == PurePosixPath(".")
        or ".." in value.parts
        or str(value) != relative
        or "\\" in relative
    ):
        raise ValueError("sealed relative path is invalid")
    return root.joinpath(*value.parts)


def _check_file(
    path: Path,
    *,
    digest: str | None = None,
    size: int | None = None,
    limit: int | None = None,
    read: bool = False,
) -> bytes:
    with _open_file(path) as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or (size is not None and before.st_size != size)
            or (limit is not None and before.st_size > limit)
        ):
            raise ValueError("sealed file size or type is invalid")
        checksum = hashlib.sha256()
        content = bytearray()
        count = 0
        while chunk := stream.read(min(1024 * 1024, before.st_size - count + 1)):
            count += len(chunk)
            if count > before.st_size:
                raise ValueError("sealed file grew while being read")
            checksum.update(chunk)
            if read:
                content.extend(chunk)
        after = os.fstat(stream.fileno())
        if (
            count != before.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise ValueError("sealed file changed while being read")
        if digest is not None and checksum.hexdigest() != digest:
            raise ValueError("sealed file digest is invalid")
    return bytes(content)


def _model(
    path: Path,
    model,
    *,
    limit: int = 16 * 1024 * 1024,
    digest: str | None = None,
    exclude_none: bool = False,
):
    payload = _check_file(path, digest=digest, limit=limit, read=True)
    value = model.model_validate(parse_json_object(payload))
    if (
        payload
        != canonical_json_bytes(
            value.model_dump(mode="json", exclude_none=exclude_none)
        )
        + b"\n"
    ):
        raise ValueError("sealed document is not canonical JSON")
    return value


def _closed_inventory(root: Path, expected: set[str]) -> None:
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("sealed root is not a non-link directory")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("sealed inventory contains a symbolic link")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("sealed inventory contains a special file")
    if actual != expected:
        raise ValueError("sealed inventory contains missing or undeclared files")


def _load_seal(root: Path, name: str, requirements) -> SealManifest:
    seal = _model(root / name, SealManifest)
    declared = {item.artifact_id: item for item in requirements}
    if set(declared) != {item.artifact_id for item in seal.artifacts}:
        raise ValueError("seal differs from the declared artifact inventory")
    for artifact in seal.artifacts:
        requirement = declared[artifact.artifact_id]
        if any(
            getattr(artifact, key) != getattr(requirement, key)
            for key in ("artifact_type", "producer_id", "visibility", "relative_path")
        ):
            raise ValueError("sealed artifact differs from its requirement")
        _check_file(
            _path(root, artifact.relative_path),
            digest=artifact.sha256,
            size=artifact.size_bytes,
            limit=requirement.max_size_bytes,
        )
    _closed_inventory(root, {name} | {item.relative_path for item in seal.artifacts})
    return seal


def _validate_public(
    run: ResolvedRunSpec, summary: RunSummary, root: Path, runtime: SealManifest, report
) -> PublicReplayManifest:
    identity = summary.public_trace
    assert identity is not None
    manifest = _model(
        _path(root, identity.replay.relative_path),
        PublicReplayManifest,
        digest=identity.replay.sha256,
        exclude_none=True,
        limit=_MAX_PUBLIC_REPLAY_MANIFEST_BYTES,
    )
    if (
        (
            manifest.run_id,
            manifest.scenario_digest,
            manifest.event_chain_root,
            manifest.trace_sha256,
        )
        != (
            run.run_id,
            run.scenario.scenario_digest,
            runtime.event_chain_root,
            identity.sha256,
        )
        or len(manifest.files) != identity.replay.file_count
        or sum(item.size_bytes for item in manifest.files)
        != identity.replay.total_size_bytes
    ):
        raise ValueError("replay manifest differs from the recorded run")
    replay_root = root / "public/replay"
    inventory = {item.relative_path: item for item in manifest.files}
    for item in manifest.files:
        _check_file(
            _path(replay_root, item.relative_path),
            digest=item.sha256,
            size=item.size_bytes,
            limit=(
                _MAX_PUBLIC_TRACE_BYTES
                if item.relative_path == "public-trace.json"
                else _MAX_PUBLIC_ASSET_BYTES
            ),
        )
    _closed_inventory(replay_root, set(inventory) | {"replay-manifest.json"})
    trace = _model(
        _path(root, identity.relative_path),
        PublicTrace,
        digest=identity.sha256,
        limit=_MAX_PUBLIC_TRACE_BYTES,
    )
    if (
        (
            trace.run_id,
            trace.suite_id,
            trace.case_id,
            trace.execution_scope,
            trace.scenario_digest,
            trace.event_chain_root,
        )
        != (
            run.run_id,
            run.suite_id,
            run.case_id,
            run.execution_scope,
            run.scenario.scenario_digest,
            runtime.event_chain_root,
        )
        or trace.phase != "verified"
        or trace.verifier_public
        != _project_report(report, resources=SealedPublicArtifacts(runtime))
        or trace.scenario != project_public_scenario(run)
        or manifest.replay_mode != trace.scenario.replay_mode
    ):
        raise ValueError("public trace differs from the verified execution")
    expected = {
        "public-trace.json": (
            identity.sha256,
            inventory["public-trace.json"].size_bytes,
        )
    }

    def bind(path, digest, size):
        if path in expected and expected[path] != (digest, size):
            raise ValueError("public replay has conflicting byte identities")
        expected[path] = (digest, size)

    for asset in trace.scenario.assets:
        bind(asset.replay_path, asset.sha256, asset.size_bytes)
        if asset.license_replay_path is not None:
            bind(
                asset.license_replay_path,
                asset.license_sha256,
                asset.license_size_bytes,
            )
    sealed = {item.artifact_id: item for item in runtime.artifacts}
    if {item.artifact_id for item in trace.runtime_artifacts} != {
        item.artifact_id for item in runtime.artifacts if item.visibility == "public"
    }:
        raise ValueError("public trace omits or adds sealed public artifacts")
    for artifact in trace.runtime_artifacts:
        record = sealed[artifact.artifact_id]
        if record.visibility != "public" or (
            artifact.sha256,
            artifact.size_bytes,
            artifact.selector,
            artifact.artifact_type,
        ) != (
            record.sha256,
            record.size_bytes,
            record.relative_path,
            record.artifact_type,
        ):
            raise ValueError("replay artifact differs from the authoritative seal")
        bind(artifact.replay_path, artifact.sha256, artifact.size_bytes)
    history = next(
        item
        for item in trace.runtime_artifacts
        if item.artifact_id == trace.scene_state_history_artifact.artifact_id
    )
    if manifest.scene_state_history is not None and (
        manifest.scene_state_history.relative_path,
        manifest.scene_state_history.sha256,
        manifest.scene_state_history.size_bytes,
    ) != (history.replay_path, history.sha256, history.size_bytes):
        raise ValueError("replay history differs from the runtime seal")
    if manifest.replay_index is None:
        history_digest = hashlib.sha256()
        for state in trace.scene_states:
            history_digest.update(
                canonical_json_bytes(state.model_dump(mode="json")) + b"\n"
            )
        if history_digest.hexdigest() != history.sha256:
            raise ValueError("embedded replay differs from the sealed complete history")
    if manifest.replay_index is not None:
        ref = manifest.replay_index
        index = _model(
            _path(replay_root, ref.relative_path), PublicReplayIndex, digest=ref.sha256
        )
        if (index.run_id, index.scenario_digest, index.event_chain_root) != (
            run.run_id,
            run.scenario.scenario_digest,
            runtime.event_chain_root,
        ) or index.scene_state_count != len(trace.scene_states):
            raise ValueError("replay index differs from the trace")
        bind(ref.relative_path, ref.sha256, ref.size_bytes)
        history_digest = hashlib.sha256()
        for shard in index.shards:
            bind(shard.relative_path, shard.sha256, shard.size_bytes)
            payload = _check_file(
                _path(replay_root, shard.relative_path),
                digest=shard.sha256,
                size=shard.size_bytes,
                limit=_MAX_PUBLIC_ASSET_BYTES,
                read=True,
            )
            states = trace.scene_states[shard.first_tick - 1 : shard.last_tick]
            if payload != b"".join(
                canonical_json_bytes(state.model_dump(mode="json")) + b"\n"
                for state in states
            ):
                raise ValueError("replay shard differs from its declared tick range")
            history_digest.update(payload)
        if history_digest.hexdigest() != history.sha256:
            raise ValueError("replay shards differ from the sealed complete history")
    if expected != {
        path: (item.sha256, item.size_bytes) for path, item in inventory.items()
    }:
        raise ValueError("replay inventory does not close over its trace")
    return manifest


@dataclass(frozen=True, slots=True)
class _SealedReplay:
    run: ResolvedRunSpec
    root: Path
    summary: RunSummary
    manifest: PublicReplayManifest


class SealedReplayManager:
    """Serve verified CLI publications without an executor, session or transitions."""

    def __init__(self, *, replays: tuple[_SealedReplay, ...], suite_sha256: str):
        if not replays or len({item.run.run_id for item in replays}) != len(replays):
            raise ValueError("sealed replay selection must be non-empty and unique")
        self._replays = {item.run.run_id: item for item in replays}
        self._credentials: dict[str, RunAccessCredentials] = {}
        self._lock = threading.RLock()
        self._catalog = ControlCatalog(
            schema_version="aero-bench.control-catalog/v1",
            suite_id=replays[0].run.suite_id,
            suite_sha256=suite_sha256,
            runs=tuple(
                sorted(
                    (
                        CatalogRun(
                            run_id=item.run.run_id,
                            suite_id=item.run.suite_id,
                            case_id=item.run.case_id,
                            execution_scope=item.run.execution_scope,
                            world_id=item.run.scenario.world_id,
                            scenario_digest=item.run.scenario.scenario_digest,
                            task_id=item.run.task.task_id,
                            seed=item.run.seed,
                            launch_site_id=item.run.launch_site_id,
                            matrix_axis_ids=tuple(
                                sorted(axis.axis_id for axis in item.run.overrides)
                            ),
                        )
                        for item in replays
                    ),
                    key=lambda item: item.run_id,
                )
            ),
        )

    @classmethod
    def from_files(
        cls,
        *,
        suite_path: str | Path,
        runner_config_path: str | Path,
        run_ids: tuple[str, ...],
    ) -> "SealedReplayManager":
        try:
            suite = load_suite(suite_path)
            config = load_runner_config(runner_config_path)
            runs = resolve_suite(
                str(suite.suite_path),
                executor_kind=config.executor_kind,
                task_package_resolvers=builtin_task_package_resolvers(),
                provider_registry=builtin_provider_registry(),
            )
            output = Path(config.output_root)
            runner = _model(output / "runner-summary.json", RunnerSummary)
            if (
                runner.suite_sha256 != sha256_file(suite.suite_path)
                or runner.runner_config_sha256 != sha256_file(Path(runner_config_path))
                or runner.executor_kind != config.executor_kind
                or len({item.run_id for item in runner.runs}) != len(runner.runs)
                or {item.run_id for item in runner.runs} != {run.run_id for run in runs}
            ):
                raise ValueError("Runner summary differs from the resolved inputs")
            selected = set(run_ids)
            if (
                not selected
                or len(selected) != len(run_ids)
                or not selected <= {run.run_id for run in runs}
            ):
                raise ValueError(
                    "sealed replay selection is empty, duplicated or unknown"
                )
            summaries = {item.run_id: item for item in runner.runs}
            replays = []
            for run in runs:
                if run.run_id not in selected:
                    continue
                root = output / run.run_id
                summary = summaries[run.run_id]
                if (
                    summary.seal is None
                    or summary.verification is None
                    or summary.public_trace is None
                    or not summary.preflight.ready
                    or summary.preflight.blocker_codes
                    or run.execution_scope != "formal_benchmark"
                    or summary.execution_scope != run.execution_scope
                    or summary.status != summary.verification.status
                ):
                    raise ValueError(
                        "run has no terminal independently verified formal replay"
                    )
                runtime = _load_seal(
                    root / "runtime-seal",
                    "seal-manifest.json",
                    run.artifact_requirements,
                )
                verification = _load_seal(
                    root / "verification",
                    "verification-manifest.json",
                    run.verification_outputs,
                )
                if _seal_identity(runtime) != summary.seal:
                    raise ValueError("runtime seal differs from the recorded run")
                validated = load_and_validate_verification_output(
                    run=run,
                    runtime_seal=runtime,
                    output_seal=verification,
                    output_root=root / "verification",
                    runtime_root=root / "runtime-seal",
                )
                if _verification_identity(validated) != summary.verification:
                    raise ValueError("verification differs from the recorded verdict")
                manifest = _validate_public(
                    run, summary, root, runtime, validated.report
                )
                replays.append(_SealedReplay(run, root, summary, manifest))
            return cls(replays=tuple(replays), suite_sha256=runner.suite_sha256)
        except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as error:
            raise ControlManagerError("replay.invalid", str(error)) from error

    @property
    def catalog(self) -> ControlCatalog:
        return self._catalog

    def issue_read_access(self, run_id: str) -> ReplayAccessResponse:
        if run_id not in self._replays:
            raise ControlManagerError(
                "catalog.run_unknown", "run is not in the replay catalog"
            )
        with self._lock:
            if run_id not in self._credentials:
                self._credentials[run_id] = RunAccessCredentials(
                    schema_version="aero-bench.run-access-credentials/v1",
                    run_id=run_id,
                    operator_token=secrets.token_hex(32),
                    csrf_token=secrets.token_hex(32),
                )
            return ReplayAccessResponse(
                schema_version="aero-bench.replay-access-response/v1",
                credentials=self._credentials[run_id],
                read_only=True,
            )

    def _authorize(self, run_id: str, operator_token: str) -> _SealedReplay:
        with self._lock:
            access = self._credentials.get(run_id)
            if access is None or not ControlRunManager._credential_matches(
                operator_token, access.operator_token
            ):
                raise ControlManagerError(
                    "authentication.failed", "run authentication failed"
                )
            return self._replays[run_id]

    def _read(
        self,
        replay: _SealedReplay,
        relative: str,
        digest: str,
        limit: int,
        size: int | None = None,
    ) -> bytes:
        try:
            return _check_file(
                _path(replay.root, relative),
                digest=digest,
                size=size,
                limit=limit,
                read=True,
            )
        except (OSError, ValueError) as error:
            raise ControlManagerError(
                "projection.invalid", "sealed public file failed integrity checks"
            ) from error

    def public_trace_document(self, run_id: str, *, operator_token: str) -> bytes:
        replay = self._authorize(run_id, operator_token)
        identity = replay.summary.public_trace
        assert identity is not None
        trace_file = next(
            item
            for item in replay.manifest.files
            if item.relative_path == "public-trace.json"
        )
        return self._read(
            replay,
            identity.relative_path,
            identity.sha256,
            _MAX_PUBLIC_TRACE_BYTES,
            trace_file.size_bytes,
        )

    def public_replay_manifest_document(
        self, run_id: str, *, operator_token: str
    ) -> bytes:
        replay = self._authorize(run_id, operator_token)
        identity = replay.summary.public_trace
        assert identity is not None
        return self._read(
            replay,
            identity.replay.relative_path,
            identity.replay.sha256,
            _MAX_PUBLIC_REPLAY_MANIFEST_BYTES,
        )

    def public_asset(
        self, run_id: str, digest: str, *, operator_token: str
    ) -> tuple[bytes, str]:
        replay = self._authorize(run_id, operator_token)
        item = next(
            (
                item
                for item in replay.manifest.files
                if item.sha256 == digest and item.relative_path != "public-trace.json"
            ),
            None,
        )
        if item is None:
            raise ControlManagerError(
                "asset.not_found", "asset is not in the public replay inventory"
            )
        asset = next(
            (
                asset
                for asset in project_public_scenario(replay.run).assets
                if asset.sha256 == digest
            ),
            None,
        )
        media_type = (
            asset.media_type if asset is not None else "application/octet-stream"
        )
        return self._read(
            replay,
            "public/replay/" + item.relative_path,
            item.sha256,
            _MAX_PUBLIC_ASSET_BYTES,
            item.size_bytes,
        ), media_type

    def start(self, request):
        raise ControlManagerError(
            "replay.read_only", "sealed replay cannot start a workload"
        )

    def _reject_live(self, run_id: str, *, operator_token: str, **kwargs):
        self._authorize(run_id, operator_token)
        raise ControlManagerError(
            "replay.read_only",
            "sealed replay has no live session, controls or transitions",
        )

    status = snapshot = runtime_projection = control = check_csrf = _reject_live
    transitions_after = wait_for_transitions = _reject_live

    def shutdown(self, *, join_timeout_seconds: float = 30.0) -> None:
        with self._lock:
            self._credentials.clear()
