from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.loader import BundleReader, load_suite, sha256_file
from aero_bench.config.models import FileRef
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.executor import DockerExecutor, Executor
from aero_bench.providers.registry import ProviderRegistry, builtin_provider_registry
from aero_bench.runtime.evidence import load_sealed_event_ledger
from aero_bench.runtime.scene_history import read_scene_state_history_jsonl
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace import (
    PUBLIC_PROJECTOR_VERSION,
    PUBLIC_TRACE_SCHEMA_VERSION,
    PublicReplayFile,
    PublicReplayIndex,
    PublicReplayManifest,
    PublicReplayShard,
    project_public_trace,
)
from aero_bench.verifier.output import ValidatedVerificationOutput

from aero_bench.runner.contracts import (
    ArtifactIdentity,
    PublicReplayIdentity,
    PublicTraceIdentity,
    RunSummary,
    RunnerConfig,
    RunnerSummary,
    SealIdentity,
    VerificationIdentity,
)


_PUBLIC_TRACE_RELATIVE_PATH = "public/public-trace.json"


class RunnerError(RuntimeError):
    """A stable runner boundary error that does not expose command details."""


def load_runner_config(path: str | Path) -> RunnerConfig:
    try:
        config_path = Path(path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise RunnerError("RunnerConfig path is unavailable") from None
    if not config_path.is_file():
        raise RunnerError("RunnerConfig path must name a regular file")
    try:
        reader = BundleReader(config_path.parent)
        reference = FileRef(
            path=config_path.name,
            sha256=sha256_file(config_path),
        )
        raw = reader.load_document(reference)
    except Exception:
        raise RunnerError("RunnerConfig document could not be loaded") from None
    if not isinstance(raw, dict):
        raise RunnerError("RunnerConfig document must be a mapping")
    try:
        return RunnerConfig.model_validate(raw)
    except ValidationError:
        raise RunnerError("RunnerConfig is invalid") from None


def build_docker_executor(
    config: RunnerConfig, *, provider_registry: ProviderRegistry
) -> DockerExecutor:
    return DockerExecutor(
        docker_binary=config.docker_binary,
        input_mount_path=config.input_mount_path,
        artifact_mount_path=config.artifact_mount_path,
        seal_mount_path=config.seal_mount_path,
        volume_keeper_mount_path=config.volume_keeper_mount_path,
        input_volume_size_bytes=config.input_volume_size_bytes,
        artifact_volume_size_bytes=config.artifact_volume_size_bytes,
        scratch_size_bytes=config.scratch_size_bytes,
        pids_limit=config.pids_limit,
        workload_uid=config.workload_uid,
        workload_gid=config.workload_gid,
        provider_bind_host=config.provider_bind_host,
        gateway_bind_host=config.gateway_bind_host,
        readiness_timeout_seconds=config.readiness_timeout_seconds,
        volume_keeper_image=config.volume_keeper_image,
        volume_keeper_command=config.volume_keeper_command,
        volume_keeper_cpu_millicores=config.volume_keeper_cpu_millicores,
        volume_keeper_memory_mib=config.volume_keeper_memory_mib,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=provider_registry,
        attempt_id=config.attempt_id,
        model_auth_file=config.model_auth_file,
        model_https_proxy=config.model_https_proxy,
    )


def run_suite(
    suite_path: str | Path,
    runner_config_path: str | Path,
) -> RunnerSummary:
    config = load_runner_config(runner_config_path)
    provider_registry = builtin_provider_registry()
    try:
        loaded_suite_path = Path(suite_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise RunnerError("Suite path is unavailable") from None
    try:
        loaded_suite = load_suite(loaded_suite_path)
        runs = resolve_suite(
            str(loaded_suite_path),
            executor_kind=config.executor_kind,
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=provider_registry,
        )
    except Exception:
        raise RunnerError("Suite could not be loaded or resolved") from None

    try:
        executor = build_docker_executor(config, provider_registry=provider_registry)
    except Exception:
        raise RunnerError(
            "Docker Reference Executor could not be constructed"
        ) from None
    if not isinstance(executor, Executor):
        raise RunnerError("Docker Reference Executor does not implement Executor")

    output_root = Path(config.output_root)
    try:
        output_root.mkdir(mode=0o700)
        output_root.chmod(0o700)
    except FileExistsError:
        raise RunnerError("output_root must be fresh and not already exist") from None
    except OSError:
        raise RunnerError("output_root could not be created") from None

    summaries: list[RunSummary] = []
    for run in runs:
        summaries.append(
            _run_one(
                executor,
                run,
                bundle_root=loaded_suite.root,
                output_root=output_root,
                runtime_timeout_seconds=config.runtime_timeout_seconds,
                verifier_timeout_seconds=config.verifier_timeout_seconds,
            )
        )

    summary = RunnerSummary(
        schema_version="aero-bench.runner-summary/v1",
        executor_kind=config.executor_kind,
        suite_sha256=sha256_file(loaded_suite.suite_path),
        runner_config_sha256=sha256_file(Path(runner_config_path).resolve()),
        run_count=len(summaries),
        execution_complete_count=sum(item.execution_complete for item in summaries),
        passed_count=sum(item.status == "passed" for item in summaries),
        runs=tuple(summaries),
    )
    try:
        summary_path = output_root / "runner-summary.json"
        descriptor = os.open(
            summary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(summary.model_dump(mode="json")) + b"\n")
    except OSError:
        raise RunnerError("runner summary could not be written") from None
    return summary


def _run_one(
    executor: Executor,
    run: ResolvedRunSpec,
    *,
    bundle_root: Path,
    output_root: Path,
    runtime_timeout_seconds: int,
    verifier_timeout_seconds: int,
) -> RunSummary:
    from aero_bench.runner.session import RunExecutionSession

    return RunExecutionSession(
        executor=executor,
        run=run,
        bundle_root=bundle_root,
        output_root=output_root,
        runtime_timeout_seconds=runtime_timeout_seconds,
        verifier_timeout_seconds=verifier_timeout_seconds,
    ).execute()


def _project_and_write_public_trace(
    *,
    run: ResolvedRunSpec,
    seal: SealManifest,
    validated: ValidatedVerificationOutput | None,
    bundle_root: Path,
    run_root: Path,
    seal_root: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> PublicTraceIdentity:
    if seal_root is None:
        seal_root = run_root / "runtime-seal"
    if progress is not None:
        progress("reading and validating sealed event ledger")
    ledger = load_sealed_event_ledger(
        run=run,
        seal=seal,
        seal_root=seal_root,
    )
    scene_artifacts = tuple(
        artifact
        for artifact in seal.artifacts
        if artifact.artifact_type == "scene.state-history"
    )
    if len(scene_artifacts) != 1:
        raise RunnerError(
            "runtime seal does not contain one SceneState history artifact"
        )
    scene_artifact = scene_artifacts[0]
    scene_source = _closed_source(
        root=seal_root,
        relative_path=scene_artifact.relative_path,
        label="SceneState history",
    )
    aborted_before_motion = (
        ledger.records[-1].event.event_type == "run.aborted"
        and scene_artifact.size_bytes == 0
    )
    if progress is not None:
        progress("reading and validating recorded SceneState history")
    try:
        scene_states = read_scene_state_history_jsonl(
            scene_source,
            aborted_before_first_motion=aborted_before_motion,
        )
    except ValueError as error:
        raise RunnerError("sealed SceneState history is invalid") from error
    report = (
        validated.report
        if validated is not None and run.execution_scope == "formal_benchmark"
        else None
    )
    if progress is not None:
        progress("projecting public state and authorized interactions")
    trace = project_public_trace(
        run=run,
        ledger=ledger,
        seal=seal,
        scene_states=scene_states,
        report=report,
    )
    if progress is not None:
        progress("encoding public trace and publishing indexed replay")
    payload = canonical_json_bytes(trace.model_dump(mode="json")) + b"\n"
    trace_digest = hashlib.sha256(payload).hexdigest()
    replay = _publish_public_replay(
        run=run,
        seal=seal,
        trace=trace,
        trace_payload=payload,
        trace_digest=trace_digest,
        bundle_root=bundle_root,
        seal_root=seal_root,
        run_root=run_root,
    )
    return PublicTraceIdentity(
        schema_version=PUBLIC_TRACE_SCHEMA_VERSION,
        projector_version=PUBLIC_PROJECTOR_VERSION,
        relative_path=_PUBLIC_TRACE_RELATIVE_PATH,
        sha256=trace_digest,
        event_chain_root=ledger.chain_root,
        replay=replay,
    )


def _publish_public_replay(
    *,
    run: ResolvedRunSpec,
    seal: SealManifest,
    trace: object,
    trace_payload: bytes,
    trace_digest: str,
    bundle_root: Path,
    seal_root: Path,
    run_root: Path,
) -> PublicReplayIdentity:
    from aero_bench.trace.contracts import PublicTrace

    if not isinstance(trace, PublicTrace):
        raise TypeError("public replay requires PublicTrace v3")
    if (
        hashlib.sha256(trace_payload).hexdigest() != trace_digest
        or canonical_json_bytes(trace.model_dump(mode="json")) + b"\n" != trace_payload
        or trace.run_id != run.run_id
        or trace.scenario_digest != run.scenario.scenario_digest
        or trace.event_chain_root != seal.event_chain_root
    ):
        raise RunnerError("public replay trace bytes differ from their authority")
    public_root = run_root / "public"
    if public_root.exists() or public_root.is_symlink():
        raise RunnerError("public replay output path is not fresh")
    staging_root = Path(tempfile.mkdtemp(prefix=".public-", dir=run_root))
    try:
        replay_root = staging_root / "replay"
        replay_root.mkdir(mode=0o700)
        (replay_root / "assets").mkdir(mode=0o700)
        (replay_root / "artifacts").mkdir(mode=0o700)

        _write_exclusive(staging_root / "public-trace.json", trace_payload)
        _write_exclusive(replay_root / "public-trace.json", trace_payload)
        replay_files: dict[str, PublicReplayFile] = {
            "public-trace.json": PublicReplayFile(
                relative_path="public-trace.json",
                sha256=trace_digest,
                size_bytes=len(trace_payload),
            )
        }

        resolved_assets = {asset.asset_id: asset for asset in run.scenario.assets}
        for asset in trace.scenario.assets:
            resolved = resolved_assets.get(asset.asset_id)
            if resolved is None or (
                resolved.classification != "public"
                or resolved.file.sha256 != asset.sha256
                or resolved.byte_size != asset.size_bytes
                or resolved.file.path != asset.selector
            ):
                raise RunnerError(
                    "public scenario asset differs from ResolvedScenario"
                )
            replay_path = f"assets/{asset.sha256}"
            _publish_replay_content(
                source=_closed_source(
                    root=bundle_root,
                    relative_path=resolved.file.path,
                    label=f"scenario asset {asset.asset_id}",
                ),
                target=replay_root / replay_path,
                expected_sha256=asset.sha256,
                expected_size_bytes=asset.size_bytes,
            )
            replay_files[replay_path] = PublicReplayFile(
                relative_path=replay_path,
                sha256=asset.sha256,
                size_bytes=asset.size_bytes,
            )
            if asset.license_sha256 is not None:
                if resolved.world is None or (
                    resolved.world.license.file.path != asset.license_selector
                    or resolved.world.license.file.sha256 != asset.license_sha256
                    or resolved.world.license.byte_size != asset.license_size_bytes
                ):
                    raise RunnerError(
                        "public scenario license differs from ResolvedScenario"
                    )
                license_path = f"assets/{asset.license_sha256}"
                _publish_replay_content(
                    source=_closed_source(
                        root=bundle_root,
                        relative_path=resolved.world.license.file.path,
                        label=f"scenario license {asset.asset_id}",
                    ),
                    target=replay_root / license_path,
                    expected_sha256=asset.license_sha256,
                    expected_size_bytes=resolved.world.license.byte_size,
                )
                replay_files[license_path] = PublicReplayFile(
                    relative_path=license_path,
                    sha256=asset.license_sha256,
                    size_bytes=resolved.world.license.byte_size,
                )

        sealed_by_id = {artifact.artifact_id: artifact for artifact in seal.artifacts}
        for artifact in trace.runtime_artifacts:
            sealed = sealed_by_id.get(artifact.artifact_id)
            if sealed is None or (
                sealed.visibility != "public"
                or sealed.relative_path != artifact.selector
                or sealed.sha256 != artifact.sha256
                or sealed.size_bytes != artifact.size_bytes
            ):
                raise RunnerError(
                    "public runtime artifact differs from the runtime seal"
                )
            replay_path = f"artifacts/{artifact.sha256}"
            _publish_replay_content(
                source=_closed_source(
                    root=seal_root,
                    relative_path=sealed.relative_path,
                    label=f"runtime artifact {artifact.artifact_id}",
                ),
                target=replay_root / replay_path,
                expected_sha256=artifact.sha256,
                expected_size_bytes=artifact.size_bytes,
            )
            replay_files[replay_path] = PublicReplayFile(
                relative_path=replay_path,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
            )

        history_reference = trace.scene_state_history_artifact
        histories = tuple(
            artifact for artifact in trace.runtime_artifacts
            if artifact.artifact_id == history_reference.artifact_id
            and artifact.artifact_type == "scene.state-history"
            and artifact.selector == history_reference.selector
            and artifact.sha256 == history_reference.digest
        )
        if len(histories) != 1:
            raise RunnerError("public replay does not bind one sealed SceneState history")
        history_file = replay_files[f"artifacts/{histories[0].sha256}"]
        replay_index_reference = None
        if trace.scenario.replay_mode == "indexed":
            shards: list[PublicReplayShard] = []
            history_digest = hashlib.sha256()
            history_size = 0
            shard_size = 256
            for start in range(0, len(trace.scene_states), shard_size):
                states = trace.scene_states[start : start + shard_size]
                shard_payload = b"".join(
                    canonical_json_bytes(state.model_dump(mode="json")) + b"\n"
                    for state in states
                )
                history_digest.update(shard_payload)
                history_size += len(shard_payload)
                shard_digest = hashlib.sha256(shard_payload).hexdigest()
                shard_path = f"artifacts/{shard_digest}"
                _write_content_addressed(replay_root / shard_path, shard_payload)
                replay_files[shard_path] = PublicReplayFile(
                    relative_path=shard_path,
                    sha256=shard_digest,
                    size_bytes=len(shard_payload),
                )
                shards.append(
                    PublicReplayShard(
                        relative_path=shard_path,
                        sha256=shard_digest,
                        size_bytes=len(shard_payload),
                        first_tick=states[0].at.tick,
                        last_tick=states[-1].at.tick,
                        scene_state_count=len(states),
                    )
                )
            if history_digest.hexdigest() != history_file.sha256 or history_size != history_file.size_bytes:
                raise RunnerError("replay shards differ from the sealed SceneState history bytes")
            replay_index = PublicReplayIndex(
                schema_version="aero-bench.public-replay-index/v1",
                run_id=run.run_id,
                scenario_digest=run.scenario.scenario_digest,
                event_chain_root=seal.event_chain_root,
                first_tick=trace.scene_states[0].at.tick if trace.scene_states else 0,
                last_tick=trace.scene_states[-1].at.tick if trace.scene_states else 0,
                scene_state_count=len(trace.scene_states),
                shards=tuple(shards),
            )
            index_payload = canonical_json_bytes(replay_index.model_dump(mode="json")) + b"\n"
            index_digest = hashlib.sha256(index_payload).hexdigest()
            index_path = f"artifacts/{index_digest}"
            _write_content_addressed(replay_root / index_path, index_payload)
            replay_files[index_path] = PublicReplayFile(
                relative_path=index_path,
                sha256=index_digest,
                size_bytes=len(index_payload),
            )
            replay_index_reference = PublicReplayFile(
                relative_path=index_path,
                sha256=index_digest,
                size_bytes=len(index_payload),
            )

        manifest = PublicReplayManifest(
            schema_version="aero-bench.public-replay-manifest/v1",
            run_id=run.run_id,
            scenario_digest=run.scenario.scenario_digest,
            event_chain_root=seal.event_chain_root,
            trace_sha256=trace_digest,
            files=tuple(replay_files[key] for key in sorted(replay_files)),
            replay_mode=trace.scenario.replay_mode,
            replay_index=replay_index_reference,
            scene_state_history=history_file,
        )
        manifest_payload = (
            canonical_json_bytes(manifest.model_dump(mode="json", exclude_none=True)) + b"\n"
        )
        manifest_digest = hashlib.sha256(manifest_payload).hexdigest()
        _write_exclusive(replay_root / "replay-manifest.json", manifest_payload)
        _fsync_directory(replay_root / "artifacts")
        _fsync_directory(replay_root / "assets")
        _fsync_directory(replay_root)
        _fsync_directory(staging_root)
        _rename_directory_noreplace(staging_root, public_root)
        _fsync_directory(run_root)
        if (
            sha256_file(public_root / "public-trace.json") != trace_digest
            or sha256_file(public_root / "replay" / "replay-manifest.json")
            != manifest_digest
        ):
            raise RunnerError("public replay bytes changed during publication")
        return PublicReplayIdentity(
            schema_version="aero-bench.public-replay-manifest/v1",
            relative_path="public/replay/replay-manifest.json",
            sha256=manifest_digest,
            file_count=len(manifest.files),
            total_size_bytes=sum(item.size_bytes for item in manifest.files),
        )
    except BaseException:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
        raise


def _closed_source(*, root: Path, relative_path: str, label: str) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        source = root / relative_path
        if source.is_symlink():
            raise RunnerError(f"{label} cannot be a symbolic link")
        resolved_source = source.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RunnerError(f"{label} is unavailable") from error
    if not resolved_source.is_relative_to(resolved_root) or not resolved_source.is_file():
        raise RunnerError(f"{label} is outside its immutable root")
    return resolved_source


def _publish_replay_content(
    *,
    source: Path,
    target: Path,
    expected_sha256: str,
    expected_size_bytes: int,
) -> None:
    if target.is_symlink():
        raise RunnerError("content-addressed replay path cannot be a symbolic link")
    if target.exists():
        if (
            not target.is_file()
            or target.stat().st_size != expected_size_bytes
            or sha256_file(target) != expected_sha256
        ):
            raise RunnerError("content-addressed replay path has conflicting bytes")
        return
    digest = hashlib.sha256()
    size_bytes = 0
    created = False
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            while True:
                chunk = input_stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size_bytes += len(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        if created:
            target.unlink(missing_ok=True)
        raise RunnerError("public replay content could not be copied") from error
    if size_bytes != expected_size_bytes or digest.hexdigest() != expected_sha256:
        target.unlink(missing_ok=True)
        raise RunnerError("public replay content differs from its authority")


def _write_content_addressed(target: Path, payload: bytes) -> None:
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    if target.is_symlink():
        raise RunnerError("content-addressed replay path cannot be a symbolic link")
    if target.exists():
        if not target.is_file() or target.stat().st_size != len(payload) or sha256_file(target) != expected_sha256:
            raise RunnerError("content-addressed replay path has conflicting bytes")
        return
    _write_exclusive(target, payload)


def _write_exclusive(target: Path, payload: bytes) -> None:
    created = False
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        if created:
            target.unlink(missing_ok=True)
        raise RunnerError("public replay document could not be written") from error


def _rename_directory_noreplace(source: Path, target: Path) -> None:
    # Plain rename/replace can clobber an empty directory created concurrently.
    # This Linux delivery path must fail closed if no atomic no-replace primitive exists.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise RunnerError("atomic no-overwrite publication is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise RunnerError("public replay could not be atomically published without overwrite") from OSError(
            error, os.strerror(error), str(target)
        )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise RunnerError("public replay directory could not be durably published") from error


def _seal_identity(seal: SealManifest | None) -> SealIdentity | None:
    if seal is None:
        return None
    return SealIdentity(
        schema_version=seal.schema_version,
        run_id=seal.run_id,
        attempt_id=seal.attempt_id,
        execution_scope=seal.execution_scope,
        manifest_digest=seal.manifest_digest,
        event_chain_root=seal.event_chain_root,
        artifacts=tuple(
            ArtifactIdentity(
                artifact_id=artifact.artifact_id,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
            )
            for artifact in seal.artifacts
        ),
    )


def _verification_identity(
    output: ValidatedVerificationOutput | None,
) -> VerificationIdentity | None:
    if output is None:
        return None
    return VerificationIdentity(
        schema_version=output.report.schema_version,
        run_id=output.report.run_id,
        execution_scope=output.report.execution_scope,
        status=output.report.status,
        output_manifest_digest=output.seal.manifest_digest,
        goal_ids=tuple(goal.goal_id for goal in output.report.goals),
    )


__all__ = [
    "RunnerError",
    "build_docker_executor",
    "load_runner_config",
    "run_suite",
]
