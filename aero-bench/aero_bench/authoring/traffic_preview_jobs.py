"""Digest-pinned jobs for independently audited SUMO authoring previews.

The registry is separate from native inspection registrations. A successful job
publishes only its public viewer trace; native SUMO records and audit inputs stay
inside the manager's private output root.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from typing import Annotated, Literal

from pydantic import Field, field_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.providers.rpc import parse_json_object
from aero_bench.serialization import canonical_json_bytes

from .traffic_preview import parse_workspace_preview_demand
from .traffic_preview_contracts import (
    TrafficPreviewArtifact,
    TrafficPreviewAuditIdentity,
    TrafficPreviewJob,
    TrafficPreviewJobError,
    TrafficPreviewProfile,
    TrafficPreviewProfileCatalog,
    TrafficPreviewRequest,
)


_REPOSITORY_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_SUMO_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")


class TrafficPreviewError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PinnedRepositoryFile(StrictModel):
    path: Annotated[str, Field(pattern=_REPOSITORY_PATH.pattern)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]

    @field_validator("path")
    @classmethod
    def path_has_no_navigation_segments(cls, value: str) -> str:
        if any(part in {".", ".."} for part in value.split("/")):
            raise ValueError("repository path must not contain navigation segments")
        return value


class PinnedImplementationInput(StrictModel):
    role: Identifier
    file: PinnedRepositoryFile


class SourceLicenseEvidence(StrictModel):
    """Pinned provenance for the source dataset, not third-party viewer models."""

    file: PinnedRepositoryFile
    status: Literal["documented"]
    source_dataset: Annotated[str, Field(min_length=1)]
    license: Annotated[str, Field(min_length=1)]
    attribution: Annotated[str, Field(min_length=1)]
    offline: Literal[True]
    runtime_remote_requests: Literal[False]


class TrafficPreviewProfileDefinition(StrictModel):
    schema_version: Literal["aero-bench.traffic-preview-profile/v1"]
    profile_id: Identifier
    profile_sha256: Sha256
    scene_path: Annotated[
        str, Field(pattern=r"^/city-presentation/[A-Za-z0-9_-]+\.json$")
    ]
    scene: PinnedRepositoryFile
    network: PinnedRepositoryFile
    engineering_inputs: PinnedRepositoryFile
    mesh_pack_manifest: PinnedRepositoryFile
    road: PinnedRepositoryFile
    rendered_objects: PinnedRepositoryFile
    render_manifest: PinnedRepositoryFile
    source_osm: PinnedRepositoryFile
    effective_fixtures: PinnedRepositoryFile
    source_license: SourceLicenseEvidence
    sumo_image_id: Annotated[str, Field(pattern=_SUMO_IMAGE.pattern)]
    implementation_inputs: tuple[PinnedImplementationInput, ...]


class TrafficPreviewProfileManifest(StrictModel):
    schema_version: Literal["aero-bench.traffic-preview-profile-registry/v1"]
    profiles: tuple[TrafficPreviewProfileDefinition, ...]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _same_json_value(actual: object, expected: object) -> bool:
    try:
        return canonical_json_bytes(actual) == canonical_json_bytes(expected)
    except (TypeError, ValueError):
        return False


def _profile_sha(profile: TrafficPreviewProfileDefinition) -> str:
    document = profile.model_dump(mode="json")
    del document["profile_sha256"]
    return _sha(canonical_json_bytes(document))


def _sumo_image_constant(raw: bytes, label: str) -> str:
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (UnicodeError, SyntaxError) as exc:
        raise TrafficPreviewError("profile_integrity_failed", f"{label} is not valid Python") from exc
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SUMO_IMAGE" for target in node.targets):
            try:
                values.append(ast.literal_eval(node.value))
            except (ValueError, TypeError) as exc:
                raise TrafficPreviewError(
                    "profile_integrity_failed", f"{label} has a nonliteral SUMO_IMAGE",
                ) from exc
    if len(values) != 1 or not isinstance(values[0], str):
        raise TrafficPreviewError(
            "profile_integrity_failed", f"{label} must declare exactly one SUMO_IMAGE",
        )
    return values[0]


@dataclass(frozen=True, slots=True)
class VerifiedTrafficPreviewProfile:
    definition: TrafficPreviewProfileDefinition
    repository_root: Path
    paths: dict[str, Path]

    def public(self) -> TrafficPreviewProfile:
        return TrafficPreviewProfile(
            profile_id=self.definition.profile_id,
            profile_sha256=self.definition.profile_sha256,
            scene_path=self.definition.scene_path,
            scene_sha256=self.definition.scene.sha256,
            source_license_status=self.definition.source_license.status,
            preview_scope="offline-engineering-preview",
        )

    def path(self, role: str) -> Path:
        try:
            return self.paths[role]
        except KeyError as exc:
            raise TrafficPreviewError(
                "profile_integrity_failed", f"traffic preview profile lacks {role}",
            ) from exc


class TrafficPreviewProfileRegistry:
    def __init__(self, profiles: tuple[VerifiedTrafficPreviewProfile, ...]):
        identifiers = [item.definition.profile_id for item in profiles]
        if len(identifiers) != len(set(identifiers)):
            raise TrafficPreviewError("profile_integrity_failed", "duplicate traffic preview profile ID")
        self._profiles = {item.definition.profile_id: item for item in profiles}

    @classmethod
    def from_manifest(
        cls, manifest_path: Path, *, repository_root: Path | None = None,
    ) -> TrafficPreviewProfileRegistry:
        root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
        try:
            document = TrafficPreviewProfileManifest.model_validate(
                parse_json_object(Path(manifest_path).read_bytes()),
            )
        except TrafficPreviewError:
            raise
        except Exception as exc:
            raise TrafficPreviewError(
                "profile_integrity_failed", "traffic preview profile manifest is invalid",
            ) from exc
        profiles = tuple(cls._verify_definition(item, root) for item in document.profiles)
        return cls(profiles)

    @staticmethod
    def _pinned_path(root: Path, reference: PinnedRepositoryFile, label: str) -> Path:
        unresolved = root / reference.path
        try:
            path = unresolved.resolve(strict=True)
        except OSError as exc:
            raise TrafficPreviewError(
                "profile_integrity_failed", f"missing pinned traffic preview input: {label}",
            ) from exc
        if not path.is_relative_to(root) or not path.is_file() or unresolved.is_symlink():
            raise TrafficPreviewError(
                "profile_integrity_failed", f"invalid pinned traffic preview input: {label}",
            )
        raw = path.read_bytes()
        if len(raw) != reference.size_bytes or _sha(raw) != reference.sha256:
            raise TrafficPreviewError(
                "profile_integrity_failed", f"pinned traffic preview input drifted: {label}",
            )
        return path

    @classmethod
    def _verify_definition(
        cls, definition: TrafficPreviewProfileDefinition, root: Path,
    ) -> VerifiedTrafficPreviewProfile:
        if definition.profile_sha256 != _profile_sha(definition):
            raise TrafficPreviewError(
                "profile_integrity_failed", f"traffic preview profile hash drifted: {definition.profile_id}",
            )
        direct = {
            "scene": definition.scene,
            "network": definition.network,
            "engineering_inputs": definition.engineering_inputs,
            "mesh_pack_manifest": definition.mesh_pack_manifest,
            "road": definition.road,
            "rendered_objects": definition.rendered_objects,
            "render_manifest": definition.render_manifest,
            "source_osm": definition.source_osm,
            "effective_fixtures": definition.effective_fixtures,
            "source_license": definition.source_license.file,
        }
        implementation = {item.role: item.file for item in definition.implementation_inputs}
        if len(implementation) != len(definition.implementation_inputs) \
                or not {"builder", "auditor"} <= set(implementation) \
                or set(direct) & set(implementation):
            raise TrafficPreviewError(
                "profile_integrity_failed",
                "traffic preview implementation roles are missing, duplicated, or reserved",
            )
        combined = {**direct, **implementation}
        if len({item.path for item in combined.values()}) != len(combined):
            raise TrafficPreviewError(
                "profile_integrity_failed", "traffic preview profile pins one file under multiple roles",
            )
        paths = {role: cls._pinned_path(root, item, role) for role, item in combined.items()}
        try:
            expected_scene = "/" + paths["scene"].relative_to(root / "frontend/public").as_posix()
        except ValueError as exc:
            raise TrafficPreviewError(
                "profile_integrity_failed", "traffic preview scene is outside frontend/public",
            ) from exc
        if expected_scene != definition.scene_path:
            raise TrafficPreviewError(
                "profile_integrity_failed", "traffic preview scene path differs from its pinned file",
            )
        try:
            provenance = parse_json_object(paths["source_license"].read_bytes())
        except Exception as exc:
            raise TrafficPreviewError(
                "profile_integrity_failed", "traffic preview source licence evidence is invalid",
            ) from exc
        expected_provenance = definition.source_license
        for field_name in (
            "source_dataset", "license", "attribution", "offline", "runtime_remote_requests",
        ):
            if provenance.get(field_name) != getattr(expected_provenance, field_name):
                raise TrafficPreviewError(
                    "profile_integrity_failed", f"traffic preview source licence evidence drifted: {field_name}",
                )
        for role in ("builder", "auditor"):
            if _sumo_image_constant(paths[role].read_bytes(), role) != definition.sumo_image_id:
                raise TrafficPreviewError(
                    "profile_integrity_failed", f"{role} SUMO image differs from its profile pin",
                )
        return VerifiedTrafficPreviewProfile(definition, root, paths)

    def verify(self, profile: VerifiedTrafficPreviewProfile) -> None:
        current = self._verify_definition(profile.definition, profile.repository_root)
        if current.paths != profile.paths:
            raise TrafficPreviewError("profile_integrity_failed", "traffic preview path resolution drifted")

    def get(self, profile_id: str) -> VerifiedTrafficPreviewProfile | None:
        return self._profiles.get(profile_id)

    def catalog(self) -> TrafficPreviewProfileCatalog:
        return TrafficPreviewProfileCatalog(
            schema_version="aero-bench.traffic-preview-profile-catalog/v1",
            profiles=tuple(self._profiles[key].public() for key in sorted(self._profiles)),
        )


@dataclass(frozen=True, slots=True)
class TrafficPreviewRunPaths:
    workspace: Path
    trace: Path
    private_evidence: Path
    audit: Path
    build_log: Path
    audit_log: Path


TrafficPreviewRunner = Callable[
    [VerifiedTrafficPreviewProfile, TrafficPreviewRunPaths, int, Callable[[], None]], None
]


@dataclass(slots=True)
class _Job:
    job_id: str
    profile: VerifiedTrafficPreviewProfile
    request: TrafficPreviewRequest
    workspace_bytes: bytes
    workspace_sha256: str
    state: str = "queued"
    trace: TrafficPreviewArtifact | None = None
    canonical_audit: TrafficPreviewAuditIdentity | None = None
    error: TrafficPreviewJobError | None = None
    directory: Path | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def document(self) -> TrafficPreviewJob:
        with self.lock:
            return TrafficPreviewJob(
                schema_version="aero-bench.traffic-preview-job/v1",
                job_id=self.job_id,
                profile_id=self.profile.definition.profile_id,
                profile_sha256=self.profile.definition.profile_sha256,
                workspace_sha256=self.workspace_sha256,
                workspace_size_bytes=len(self.workspace_bytes),
                duration_seconds=self.request.duration_seconds,
                state=self.state, trace=self.trace,
                canonical_audit=self.canonical_audit, error=self.error,
                preview_scope="offline-engineering-preview",
                formal_provider_bound=False, executed=False, verified=False,
            )


class TrafficPreviewJobManager:
    def __init__(
        self, output_root: Path, registry: TrafficPreviewProfileRegistry,
        *, runner: TrafficPreviewRunner | None = None,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, mode=0o700, exist_ok=False)
        if self.output_root.stat().st_mode & 0o077:
            raise TrafficPreviewError(
                "preview_output_insecure", "traffic preview output root is not private",
            )
        self.registry = registry
        self._runner = runner or self._run_commands
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()
        self._threads: set[threading.Thread] = set()
        self._serial = threading.Semaphore(1)
        self._closing = False

    def close(self) -> None:
        with self._lock:
            self._closing = True
            threads = tuple(self._threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join()

    def __enter__(self) -> TrafficPreviewJobManager:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def submit(self, request: TrafficPreviewRequest) -> TrafficPreviewJob:
        if not isinstance(request, TrafficPreviewRequest):
            raise TypeError("traffic preview manager requires a strict TrafficPreviewRequest")
        request = TrafficPreviewRequest.model_validate(
            parse_json_object(canonical_json_bytes(request.model_dump(mode="json", exclude_unset=True))),
        )
        profile = self.registry.get(request.profile_id)
        if profile is None:
            raise TrafficPreviewError("unknown_traffic_preview_profile", "Traffic preview profile is not registered.")
        if request.profile_sha256 != profile.definition.profile_sha256:
            raise TrafficPreviewError("traffic_preview_profile_conflict", "Traffic preview profile pin has changed.")
        if request.draft.scenePath != profile.definition.scene_path:
            raise TrafficPreviewError(
                "traffic_preview_scene_mismatch", "Workspace scenePath differs from the selected preview profile.",
            )
        self.registry.verify(profile)
        workspace_bytes = canonical_json_bytes(request.draft.snapshot())
        # Re-parse the exact bytes used by the recorder, rather than trusting the
        # request model as a second workspace representation.
        parse_workspace_preview_demand(workspace_bytes)
        identity = {
            "schema_version": "aero-bench.traffic-preview-job-identity/v1",
            "profile_sha256": profile.definition.profile_sha256,
            "request": request.model_dump(mode="json"),
        }
        job_id = _sha(canonical_json_bytes(identity))
        with self._lock:
            if self._closing:
                raise TrafficPreviewError("traffic_preview_manager_closed", "Traffic preview manager is closed.")
            prior = self._jobs.get(job_id)
            if prior is not None:
                return prior.document()
            job = _Job(
                job_id=job_id, profile=profile, request=request,
                workspace_bytes=workspace_bytes, workspace_sha256=_sha(workspace_bytes),
            )
            self._jobs[job_id] = job
            thread = threading.Thread(
                target=self._execute, args=(job,), daemon=False,
                name=f"traffic-preview-{job_id[:12]}",
            )
            self._threads.add(thread)
            thread.start()
            return job.document()

    def status(self, job_id: str) -> TrafficPreviewJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise TrafficPreviewError("unknown_traffic_preview_job", "Traffic preview job is not published.")
        return job.document()

    def published_trace(self, job_id: str, digest: str) -> bytes:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise TrafficPreviewError("unknown_traffic_preview_job", "Traffic preview job is not published.")
        document = job.document()
        if document.state != "ready" or document.trace is None or job.directory is None:
            raise TrafficPreviewError("traffic_preview_not_ready", "Traffic preview trace is not ready.")
        if digest != document.trace.sha256:
            raise TrafficPreviewError("unknown_traffic_preview_asset", "Traffic preview asset is not published.")
        path = job.directory / "public" / "assets" / digest
        if not path.is_file() or path.is_symlink():
            raise TrafficPreviewError("traffic_preview_integrity_failed", "Published traffic preview asset is missing.")
        raw = path.read_bytes()
        if len(raw) != document.trace.size_bytes or _sha(raw) != digest:
            raise TrafficPreviewError("traffic_preview_integrity_failed", "Published traffic preview bytes drifted.")
        return raw

    def _execute(self, job: _Job) -> None:
        staging: Path | None = None
        try:
            with self._serial:
                with job.lock:
                    job.state = "recording"
                staging = Path(tempfile.mkdtemp(prefix=f".{job.job_id}-", dir=self.output_root))
                private = staging / "private"
                private.mkdir(mode=0o700)
                paths = TrafficPreviewRunPaths(
                    workspace=staging / "workspace.json",
                    trace=staging / "traffic.json",
                    private_evidence=private,
                    audit=private / "canonical-motion-audit-v2.json",
                    build_log=private / "build.log",
                    audit_log=private / "audit.log",
                )
                paths.workspace.write_bytes(job.workspace_bytes)
                paths.workspace.chmod(0o400)

                def auditing() -> None:
                    with job.lock:
                        if job.state != "recording":
                            raise TrafficPreviewError(
                                "traffic_preview_state_error", "traffic preview entered auditing out of order",
                            )
                        job.state = "auditing"

                self._runner(job.profile, paths, job.request.duration_seconds, auditing)
                with job.lock:
                    if job.state != "auditing":
                        raise TrafficPreviewError(
                            "traffic_preview_state_error",
                            "traffic preview runner returned before entering the audit phase",
                        )
                self.registry.verify(job.profile)
                trace_raw, audit_raw = self._verify_outputs(job, paths)
                public_assets = staging / "public" / "assets"
                public_assets.mkdir(parents=True)
                trace_sha = _sha(trace_raw)
                (public_assets / trace_sha).write_bytes(trace_raw)
                final = self.output_root / job.job_id
                if final.exists():
                    raise TrafficPreviewError(
                        "traffic_preview_publication_conflict", "Traffic preview destination already exists.",
                    )
                staging.rename(final)
                staging = None
                with job.lock:
                    job.directory = final
                    job.trace = TrafficPreviewArtifact(
                        url=f"/authoring/v1/traffic-previews/{job.job_id}/assets/{trace_sha}",
                        sha256=trace_sha, size_bytes=len(trace_raw),
                        media_type="application/json",
                    )
                    job.canonical_audit = TrafficPreviewAuditIdentity(
                        status="PASS", sha256=_sha(audit_raw), size_bytes=len(audit_raw),
                    )
                    job.state = "ready"
        except TrafficPreviewError as exc:
            self._fail(job, exc.code, exc.message, staging)
        except Exception as exc:
            self._fail(
                job, "traffic_preview_internal_error",
                f"Traffic preview generation failed ({type(exc).__name__}); inspect the private job log.",
                staging,
            )
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())

    def _fail(self, job: _Job, code: str, message: str, staging: Path | None) -> None:
        if staging is not None and staging.exists():
            failures = self.output_root / ".failures"
            failures.mkdir(mode=0o700, exist_ok=True)
            destination = failures / job.job_id
            if not destination.exists():
                staging.rename(destination)
        with job.lock:
            job.state = "failed"
            job.trace = None
            job.canonical_audit = None
            job.error = TrafficPreviewJobError(code=code, message=message)

    @staticmethod
    def _safe_recording_failure(log_path: Path) -> str:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "SUMO preview recording failed; inspect the private build log."
        prefixes = (
            "ValueError: Requested demand cannot depart",
            "ValueError: SUMO preview seed",
            "ValueError: SUMO preview demand counts",
            "ValueError: Native network has",
            "ValueError: Native route capability gate is BLOCKED",
            "ValueError: Rendered traffic lost a declared observed fleet type",
        )
        for line in reversed(lines):
            if line.startswith(prefixes):
                return line.removeprefix("ValueError: ")
        return "SUMO preview recording failed; inspect the private build log."

    @staticmethod
    def _run_logged(command: list[str], log_path: Path) -> int:
        with log_path.open("wb") as output:
            return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False).returncode

    def _run_commands(
        self, profile: VerifiedTrafficPreviewProfile, paths: TrafficPreviewRunPaths,
        duration: int, auditing: Callable[[], None],
    ) -> None:
        def source(role: str) -> str:
            return str(profile.path(role))

        build = [
            sys.executable, source("builder"),
            "--network", source("network"), "--pack", source("mesh_pack_manifest"),
            "--output", str(paths.trace), "--duration", str(duration),
            "--road", source("road"), "--rendered-objects", source("rendered_objects"),
            "--render-objects-manifest", source("render_manifest"),
            "--source-osm", source("source_osm"),
            "--effective-fixtures", source("effective_fixtures"),
            "--private-evidence-dir", str(paths.private_evidence),
            "--workspace", str(paths.workspace),
        ]
        if self._run_logged(build, paths.build_log) != 0:
            raise TrafficPreviewError(
                "traffic_preview_recording_failed", self._safe_recording_failure(paths.build_log),
            )
        auditing()
        stem = paths.trace.stem
        audit = [
            sys.executable, source("auditor"),
            "--traffic", str(paths.trace),
            "--native", str(paths.private_evidence / f"{stem}.native-sumo-v1.json"),
            "--routes", str(paths.private_evidence / f"{stem}.native-sumo-v1.routes.xml"),
            "--recording-inputs", str(paths.private_evidence / f"{stem}.recording-inputs-v2.json"),
            "--road", source("road"), "--objects", source("rendered_objects"),
            "--render-manifest", source("render_manifest"),
            "--pack", source("mesh_pack_manifest"), "--network", source("network"),
            "--source-osm", source("source_osm"),
            "--effective-fixtures", source("effective_fixtures"),
            "--engineering-inputs", source("engineering_inputs"),
            "--workspace", str(paths.workspace), "--output", str(paths.audit),
        ]
        if self._run_logged(audit, paths.audit_log) != 0:
            raise TrafficPreviewError(
                "traffic_preview_audit_failed",
                "Independent canonical motion audit failed; inspect the private audit log.",
            )

    @staticmethod
    def _verify_outputs(job: _Job, paths: TrafficPreviewRunPaths) -> tuple[bytes, bytes]:
        try:
            trace_raw, audit_raw = paths.trace.read_bytes(), paths.audit.read_bytes()
            trace, audit = parse_json_object(trace_raw), parse_json_object(audit_raw)
        except Exception as exc:
            raise TrafficPreviewError(
                "traffic_preview_integrity_failed", "Recorder or auditor did not produce valid JSON objects.",
            ) from exc
        expected = parse_workspace_preview_demand(job.workspace_bytes).source_identity()
        demand = expected["traffic"]
        recorded_demand = trace.get("demand")
        authored = recorded_demand.get("authored") if isinstance(recorded_demand, dict) else None
        valid_authored = isinstance(authored, dict) and all(
            isinstance(kind, str) and type(value) is int and value >= 0
            for kind, value in authored.items()
        )
        recorded_motors = (
            sum(value for kind, value in authored.items() if kind != "bicycle")
            if valid_authored else None
        )
        recorded_people = (
            recorded_demand.get("persons") if isinstance(recorded_demand, dict) else None
        )
        if trace.get("schema_version") != "aero-bench.city-sumo-preview/v2" \
                or trace.get("artifact_class") != "offline-engineering-preview" \
                or trace.get("source_kind") != "offline-sumo-engineering-preview" \
                or type(trace.get("duration_seconds")) is not int \
                or trace.get("duration_seconds") != job.request.duration_seconds \
                or not _same_json_value(trace.get("demand_authoring"), expected) \
                or type(trace.get("seed")) is not int \
                or trace.get("seed") != expected["seed"] \
                or recorded_motors != demand["vehicles"] \
                or (authored.get("bicycle", 0) if valid_authored else None) != demand["bicycles"] \
                or type(recorded_people) is not int \
                or recorded_people != demand["pedestrians"]:
            raise TrafficPreviewError(
                "traffic_preview_integrity_failed", "Public trace differs from its exact workspace demand.",
            )
        trace_ref = {"sha256": _sha(trace_raw), "size_bytes": len(trace_raw)}
        if audit.get("schema_version") != "aero-bench.city-canonical-motion-audit/v2" \
                or audit.get("artifact_class") != "offline-engineering-preview-audit" \
                or audit.get("status") != "PASS" or audit.get("failures") != [] \
                or not _same_json_value(audit.get("demand_authoring"), expected) \
                or not _same_json_value(audit.get("inputs", {}).get("traffic"), trace_ref):
            raise TrafficPreviewError(
                "traffic_preview_audit_failed", "Independent audit does not PASS the exact public trace.",
            )
        return trace_raw, audit_raw


__all__ = [
    "PinnedImplementationInput",
    "PinnedRepositoryFile",
    "SourceLicenseEvidence",
    "TrafficPreviewError",
    "TrafficPreviewJobManager",
    "TrafficPreviewProfileDefinition",
    "TrafficPreviewProfileManifest",
    "TrafficPreviewProfileRegistry",
    "TrafficPreviewRunPaths",
    "VerifiedTrafficPreviewProfile",
]
