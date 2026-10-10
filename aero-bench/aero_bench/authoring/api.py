"""Loopback HTTP entrypoint for registered OSM sources and authoring jobs."""

from __future__ import annotations

import argparse
import hashlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from aero_bench.providers.rpc import ProviderRpcError, parse_json_object
from aero_bench.serialization import canonical_json_bytes

from .compilation_contracts import CityCompileRequest
from .draft_compiler import CityDraftCompiler
from .native_registry import NativeSceneRegistry
from .publication import PublicationError, ScenePackPublisher
from .selection import SceneSelection, SceneSelectionError, SceneSourceRegistry
from .traffic_preview_contracts import TrafficPreviewRequest
from .traffic_preview_jobs import (
    TrafficPreviewError,
    TrafficPreviewJobManager,
    TrafficPreviewProfileRegistry,
)


_SOURCE = re.compile(r"/authoring/v1/sources/([A-Za-z0-9_-]+)\Z")
_JOB = re.compile(r"/authoring/v1/scenes/([0-9a-f]{64})\Z")
_MANIFEST = re.compile(r"/authoring/v1/scenes/([0-9a-f]{64})/pack/manifest\.json\Z")
_ASSET = re.compile(r"/authoring/v1/scenes/([0-9a-f]{64})/pack/assets/([0-9a-f]{64})\Z")
_PRESENTATION_MANIFEST = re.compile(r"/authoring/v1/scenes/([0-9a-f]{64})/presentation/manifest\.json\Z")
_PRESENTATION_ASSET = re.compile(r"/authoring/v1/scenes/([0-9a-f]{64})/presentation/assets/([0-9a-f]{64})\Z")
_COMPILATION = re.compile(r"/authoring/v1/compilations/([0-9a-f]{64})\Z")
_NATIVE_SCENE = re.compile(r"/authoring/v1/native-scenes/([a-z][a-z0-9_.-]*)/scene\Z")
_NATIVE_ASSET = re.compile(r"/authoring/v1/native-scenes/([a-z][a-z0-9_.-]*)/assets/([0-9a-f]{64})\Z")
_TRAFFIC_PREVIEW = re.compile(r"/authoring/v1/traffic-previews/([0-9a-f]{64})\Z")
_TRAFFIC_PREVIEW_ASSET = re.compile(
    r"/authoring/v1/traffic-previews/([0-9a-f]{64})/assets/([0-9a-f]{64})\Z",
)


class AuthoringHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    publisher: ScenePackPublisher
    compiler: CityDraftCompiler | None
    traffic_preview_manager: TrafficPreviewJobManager | None


class AuthoringHandler(BaseHTTPRequestHandler):
    server: AuthoringHTTPServer

    def _send(self, code: int, raw: bytes, media: str, *, etag: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if etag is not None:
            self.send_header("ETag", f'"{etag}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _json(self, code: int, document: dict) -> None:
        self._send(code, canonical_json_bytes(document), "application/json; charset=utf-8")

    def _error(self, code: int, name: str, message: str) -> None:
        self._json(code, {
            "schema_version": "aero-bench.authoring-error/v1",
            "error": {"code": name, "message": message},
        })

    def _handle_failure(self, exc: Exception) -> None:
        if isinstance(exc, TrafficPreviewError):
            status = {
                "unknown_traffic_preview_profile": 404,
                "unknown_traffic_preview_job": 404,
                "unknown_traffic_preview_asset": 404,
                "traffic_preview_not_ready": 409,
                "traffic_preview_profile_conflict": 409,
                "traffic_preview_publication_conflict": 409,
                "traffic_preview_scene_mismatch": 400,
                "traffic_preview_manager_closed": 503,
                "profile_integrity_failed": 500,
                "traffic_preview_integrity_failed": 500,
                "preview_output_insecure": 500,
            }.get(exc.code, 500)
            self._error(status, exc.code, exc.message)
        elif isinstance(exc, PublicationError):
            status = {
                "unknown_job": 404, "unknown_asset": 404,
                "pack_not_ready": 409, "publication_conflict": 409,
                "generator_unavailable": 503,
                "invalid_pack": 500, "invalid_presentation": 500,
            }.get(exc.code, 400)
            self._error(status, exc.code, exc.message)
        elif isinstance(exc, SceneSelectionError):
            status = 404 if "source_id is not registered" in str(exc) else 400
            self._error(status, "invalid_scene_selection", str(exc))
        elif isinstance(exc, ValidationError):
            failures = "; ".join(
                f"{'/'.join(map(str, item['loc']))}: {item['type']}"
                for item in exc.errors(include_input=False, include_context=False)
            )
            self._error(400, "invalid_compile_request", failures)
        elif isinstance(exc, ProviderRpcError):
            self._error(400, "invalid_compile_request", str(exc))
        else:
            self.log_error("authoring API failed: %r", exc)
            self._error(500, "authoring_internal_error", "作者服务处理失败；请查看服务端日志")

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        if path.query or path.fragment:
            self._error(404, "unknown_route", "未知作者 API 路径")
            return
        try:
            if path.path == "/authoring/v1/traffic-preview-profiles":
                if self.server.traffic_preview_manager is None:
                    self._error(
                        503, "traffic_preview_unconfigured",
                        "Traffic preview generation is not configured.",
                    )
                else:
                    catalog = self.server.traffic_preview_manager.registry.catalog()
                    self._json(200, catalog.model_dump(mode="json"))
                return
            preview_asset = _TRAFFIC_PREVIEW_ASSET.fullmatch(path.path)
            if preview_asset is not None:
                if self.server.traffic_preview_manager is None:
                    self._error(
                        503, "traffic_preview_unconfigured",
                        "Traffic preview generation is not configured.",
                    )
                    return
                raw = self.server.traffic_preview_manager.published_trace(
                    preview_asset[1], preview_asset[2],
                )
                self._send(200, raw, "application/json; charset=utf-8", etag=preview_asset[2])
                return
            preview_job = _TRAFFIC_PREVIEW.fullmatch(path.path)
            if preview_job is not None:
                if self.server.traffic_preview_manager is None:
                    self._error(
                        503, "traffic_preview_unconfigured",
                        "Traffic preview generation is not configured.",
                    )
                    return
                job = self.server.traffic_preview_manager.status(preview_job[1])
                self._json(200, job.model_dump(mode="json"))
                return
            scene_match = _NATIVE_SCENE.fullmatch(path.path)
            asset_match = _NATIVE_ASSET.fullmatch(path.path)
            if scene_match is not None or asset_match is not None:
                if self.server.compiler is None:
                    self._error(503, "compiler_unconfigured", "Draft compilation is not configured.")
                    return
                match = scene_match if scene_match is not None else asset_match
                scene = self.server.compiler.registry.get(match[1])
                if scene is None:
                    self._error(404, "unknown_native_scene", "Native scene registration is not published.")
                    return
                if scene_match is not None:
                    raw = scene.public_scene_bytes()
                    media = "application/json; charset=utf-8"
                else:
                    try:
                        raw, media = scene.public_asset_bytes(asset_match[2])
                    except KeyError:
                        self._error(404, "unknown_native_scene_asset", "Asset is not public in this native registration.")
                        return
                self._send(200, raw, media, etag=hashlib.sha256(raw).hexdigest())
                return
            if path.path == "/authoring/v1/native-scenes":
                if self.server.compiler is None:
                    self._error(503, "compiler_unconfigured", "Draft compilation is not configured.")
                else:
                    self._json(200, self.server.compiler.registry.catalog().model_dump(mode="json"))
                return
            match = _COMPILATION.fullmatch(path.path)
            if match is not None:
                if self.server.compiler is None:
                    self._error(503, "compiler_unconfigured", "Draft compilation is not configured.")
                    return
                try:
                    result = self.server.compiler.result(match[1])
                except KeyError:
                    self._error(404, "unknown_compilation", "Compilation ID is not published by this session.")
                    return
                self._json(200, result.model_dump(mode="json"))
                return
            if path.path == "/authoring/v1/sources":
                self._json(200, self.server.publisher.sources())
                return
            match = _SOURCE.fullmatch(path.path)
            if match is not None:
                source = self.server.publisher.source_bytes(match[1])
                self._send(200, source, "application/json; charset=utf-8", etag=hashlib.sha256(source).hexdigest())
                return
            match = _JOB.fullmatch(path.path)
            if match is not None:
                self._json(200, self.server.publisher.status(match[1]))
                return
            match = _MANIFEST.fullmatch(path.path)
            if match is not None:
                raw = self.server.publisher.published_file(match[1], "manifest").read_bytes()
                status = self.server.publisher.status(match[1])
                expected = status["pack"]["manifest"]
                if hashlib.sha256(raw).hexdigest() != expected["sha256"] or len(raw) != expected["size_bytes"]:
                    raise PublicationError("invalid_pack", "已发布 manifest 摘要不符")
                self._send(200, raw, "application/json; charset=utf-8", etag=expected["sha256"])
                return
            match = _ASSET.fullmatch(path.path)
            if match is not None:
                raw = self.server.publisher.published_file(match[1], "asset", match[2]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != match[2]:
                    raise PublicationError("invalid_pack", "已发布资产摘要不符")
                self._send(200, raw, "application/octet-stream", etag=match[2])
                return
            match = _PRESENTATION_MANIFEST.fullmatch(path.path)
            if match is not None:
                raw = self.server.publisher.published_file(match[1], "presentation_manifest").read_bytes()
                expected = self.server.publisher.status(match[1])["presentation"]["manifest"]
                if hashlib.sha256(raw).hexdigest() != expected["sha256"] or len(raw) != expected["size_bytes"]:
                    raise PublicationError("invalid_presentation", "静态城市 manifest 摘要不符")
                self._send(200, raw, "application/json; charset=utf-8", etag=expected["sha256"])
                return
            match = _PRESENTATION_ASSET.fullmatch(path.path)
            if match is not None:
                raw = self.server.publisher.published_file(match[1], "presentation_asset", match[2]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != match[2]:
                    raise PublicationError("invalid_presentation", "静态城市资产摘要不符")
                self._send(200, raw, "application/octet-stream", etag=match[2])
                return
            self._error(404, "unknown_route", "未知作者 API 路径")
        except Exception as exc:
            self._handle_failure(exc)

    def do_POST(self) -> None:
        if self.path == "/authoring/v1/traffic-previews":
            if self.server.traffic_preview_manager is None:
                self._error(
                    503, "traffic_preview_unconfigured",
                    "Traffic preview generation is not configured.",
                )
                return
            length = self.headers.get("Content-Length")
            if length is None or not length.isdecimal() or not 0 < int(length) <= 1_048_576:
                self._error(
                    400, "invalid_traffic_preview_request",
                    "Traffic preview request size is invalid.",
                )
                return
            if self.headers.get_content_type() != "application/json":
                self._error(
                    415, "invalid_content_type",
                    "Traffic preview requests require application/json.",
                )
                return
            try:
                request = TrafficPreviewRequest.model_validate(
                    parse_json_object(self.rfile.read(int(length))),
                )
                job = self.server.traffic_preview_manager.submit(request)
                self._json(202, job.model_dump(mode="json"))
            except (ValidationError, ProviderRpcError) as exc:
                if isinstance(exc, ValidationError):
                    detail = "; ".join(
                        f"{'/'.join(map(str, item['loc']))}: {item['type']}"
                        for item in exc.errors(include_input=False, include_context=False)
                    )
                else:
                    detail = str(exc)
                self._error(400, "invalid_traffic_preview_request", detail)
            except Exception as exc:
                self._handle_failure(exc)
            return
        if self.path == "/authoring/v1/compilations":
            if self.server.compiler is None:
                self._error(503, "compiler_unconfigured", "Draft compilation is not configured.")
                return
            length = self.headers.get("Content-Length")
            if length is None or not length.isdecimal() or not 0 < int(length) <= 1_048_576:
                self._error(400, "invalid_compile_request", "Compile request size is invalid.")
                return
            if self.headers.get_content_type() != "application/json":
                self._error(415, "invalid_content_type", "Compile requests require application/json.")
                return
            try:
                request = CityCompileRequest.model_validate(parse_json_object(self.rfile.read(int(length))))
                result = self.server.compiler.compile(request)
                self._json(422 if result.status == "blocked" else 201, result.model_dump(mode="json"))
            except Exception as exc:
                self._handle_failure(exc)
            return
        if self.path != "/authoring/v1/scenes":
            self._error(404, "unknown_route", "未知作者 API 路径")
            return
        length = self.headers.get("Content-Length")
        if length is None or not length.isdecimal() or not 0 < int(length) <= 65536:
            self._error(400, "invalid_scene_selection", "SceneSelection 请求大小无效")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(415, "invalid_content_type", "SceneSelection 必须使用 application/json")
            return
        try:
            selection = SceneSelection.from_json_bytes(self.rfile.read(int(length)))
            self._json(202, self.server.publisher.submit(selection))
        except Exception as exc:
            self._handle_failure(exc)

    def do_HEAD(self) -> None:
        self._error(405, "method_not_allowed", "不支持该请求方法")

    def do_PUT(self) -> None:
        self._error(405, "method_not_allowed", "不支持该请求方法")

    def do_DELETE(self) -> None:
        self._error(405, "method_not_allowed", "不支持该请求方法")


def make_server(
    host: str, port: int, publisher: ScenePackPublisher,
    *, compiler: CityDraftCompiler | None = None,
    traffic_preview_manager: TrafficPreviewJobManager | None = None,
) -> AuthoringHTTPServer:
    server = AuthoringHTTPServer((host, port), AuthoringHandler)
    server.publisher = publisher
    server.compiler = compiler
    server.traffic_preview_manager = traffic_preview_manager
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Registered local OSM authoring API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8124)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources-manifest", type=Path,
                        help="Digest-pinned OSM source registry; paths are relative to the repository root")
    parser.add_argument("--compilation-output", type=Path,
                        help="Fresh immutable draft compilation output directory")
    parser.add_argument("--native-scenes-manifest", type=Path,
                        help="Explicit digest-pinned native scene registration manifest")
    parser.add_argument("--traffic-preview-profiles-manifest", type=Path,
                        help="Explicit digest-pinned offline traffic-preview profiles")
    parser.add_argument("--traffic-preview-output", type=Path,
                        help="Fresh private output directory for traffic-preview jobs")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1"}:
        parser.error("authoring API must bind to loopback")
    if args.native_scenes_manifest is not None and args.compilation_output is None:
        parser.error("native scene registration requires --compilation-output")
    if (args.traffic_preview_profiles_manifest is None) != (args.traffic_preview_output is None):
        parser.error("traffic preview profiles and output must be configured together")
    root = Path(__file__).resolve().parents[2]
    registry = (SceneSourceRegistry.from_manifest(args.sources_manifest, repository_root=root)
                if args.sources_manifest is not None else None)
    publisher = ScenePackPublisher(args.output, registry=registry)
    native_registry = (NativeSceneRegistry.from_manifest(args.native_scenes_manifest)
                       if args.native_scenes_manifest is not None else NativeSceneRegistry())
    compiler = (CityDraftCompiler(args.compilation_output, native_registry)
                if args.compilation_output is not None else None)
    preview_registry = (TrafficPreviewProfileRegistry.from_manifest(
        args.traffic_preview_profiles_manifest, repository_root=root,
    ) if args.traffic_preview_profiles_manifest is not None else None)
    preview_manager = (TrafficPreviewJobManager(args.traffic_preview_output, preview_registry)
                       if preview_registry is not None else None)
    with make_server(
        args.host, args.port, publisher, compiler=compiler,
        traffic_preview_manager=preview_manager,
    ) as server:
        print(f"Authoring API listening on {args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        finally:
            if preview_manager is not None:
                preview_manager.close()
            publisher.close()


if __name__ == "__main__":
    main()
