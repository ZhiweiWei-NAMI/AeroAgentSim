from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from aero_bench.control.client import ControlApiClient, ControlApiClientError
from aero_bench.control.manager import ControlManagerError, ControlRunManager
from aero_bench.control.sealed_replay import SealedReplayManager
from aero_bench.control.server import (
    ControlHttpConfig,
    ControlHttpServer,
    ControlHttpServerError,
)
from aero_bench.serialization import canonical_json_bytes


_DEFAULT_BASE_URL = "http://127.0.0.1:8765"
_DEFAULT_ORIGIN = "http://127.0.0.1:5173"
_BOOTSTRAP_TOKEN_ENV = "AERO_BENCH_CONTROL_BOOTSTRAP_TOKEN"
_BOOTSTRAP_CSRF_ENV = "AERO_BENCH_CONTROL_BOOTSTRAP_CSRF"
_RUN_TOKEN_ENV = "AERO_BENCH_CONTROL_RUN_TOKEN"
_RUN_CSRF_ENV = "AERO_BENCH_CONTROL_RUN_CSRF"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aero-bench-control",
        description="Authenticated local AERO-BENCH run control",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="serve the local control API")
    source = serve.add_mutually_exclusive_group(required=True)
    source.add_argument("--suite")
    source.add_argument("--compilation-id", help="Published immutable city compilation ID")
    serve.add_argument("--compilation-root", type=Path)
    serve.add_argument("--execution-output", type=Path,
                       help="Fresh output directory for a compiled run; separate from immutable input")
    serve.add_argument("--runner-config", required=True)
    serve.add_argument("--sealed-run-id", action="append", default=None,
                       help="Serve only existing independently verified CLI replay(s), without execution")
    serve.add_argument("--bind-host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
    )
    serve.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
    )
    serve.add_argument("--bootstrap-token-env", default=_BOOTSTRAP_TOKEN_ENV)
    serve.add_argument("--bootstrap-csrf-env", default=_BOOTSTRAP_CSRF_ENV)

    catalog = subparsers.add_parser("catalog", help="list configured immutable runs")
    _add_client_options(catalog, bootstrap=True)

    start = subparsers.add_parser("start", help="start one configured immutable run")
    _add_client_options(start, bootstrap=True, csrf=True)
    start.add_argument("--run-id", required=True)
    start.add_argument("--start-id", required=True)

    status = subparsers.add_parser("status", help="read run status")
    _add_client_options(status, bootstrap=False)
    status.add_argument("--run-id", required=True)

    for operation in ("pause", "resume", "step", "stop"):
        control = subparsers.add_parser(operation, help=f"{operation} a running run")
        _add_client_options(control, bootstrap=False, csrf=True)
        control.add_argument("--run-id", required=True)
        control.add_argument("--control-id", required=True)

    events = subparsers.add_parser(
        "events",
        help="stream run transitions, SceneStates, and safe public RunEvents",
    )
    _add_client_options(events, bootstrap=False)
    events.add_argument("--run-id", required=True)
    events.add_argument("--after-transition", type=int, default=-1)
    events.add_argument("--after-scene-tick", type=int, default=0)
    events.add_argument("--after-event-sequence", type=int, default=-1)
    return parser


def _add_client_options(
    parser: argparse.ArgumentParser,
    *,
    bootstrap: bool,
    csrf: bool = False,
) -> None:
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    parser.add_argument("--origin", default=_DEFAULT_ORIGIN)
    parser.add_argument(
        "--token-env",
        default=_BOOTSTRAP_TOKEN_ENV if bootstrap else _RUN_TOKEN_ENV,
    )
    if csrf:
        parser.add_argument(
            "--csrf-env",
            default=_BOOTSTRAP_CSRF_ENV if bootstrap else _RUN_CSRF_ENV,
        )


def _environment_credential(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise ValueError(f"required credential environment variable is unavailable: {name}")
    return value


def _client(args: argparse.Namespace) -> ControlApiClient:
    csrf_env = getattr(args, "csrf_env", None)
    return ControlApiClient(
        base_url=args.base_url,
        origin=args.origin,
        bearer_token=_environment_credential(args.token_env),
        csrf_token=(
            _environment_credential(csrf_env) if csrf_env is not None else None
        ),
    )


def _write_json(value: object) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    sys.stdout.buffer.write(canonical_json_bytes(payload) + b"\n")
    sys.stdout.buffer.flush()


def _serve(args: argparse.Namespace) -> int:
    if args.sealed_run_id is not None and args.suite is None:
        raise ValueError("--sealed-run-id requires --suite, not a compiled live execution")
    if args.compilation_id is not None:
        if args.compilation_root is None or args.execution_output is None:
            raise ValueError("compiled control input requires --compilation-root and --execution-output")
    elif args.compilation_root is not None or args.execution_output is not None:
        raise ValueError("compilation options require --compilation-id")
    bootstrap_token = _environment_credential(args.bootstrap_token_env)
    bootstrap_csrf = _environment_credential(args.bootstrap_csrf_env)
    origins = tuple(sorted(set(args.allowed_origins or [_DEFAULT_ORIGIN])))
    if args.allowed_hosts:
        allowed_hosts = tuple(sorted(set(args.allowed_hosts)))
    elif ":" in args.bind_host:
        allowed_hosts = tuple(
            sorted({f"[{args.bind_host}]:{args.port}", f"localhost:{args.port}"})
        )
    else:
        allowed_hosts = tuple(
            sorted({f"{args.bind_host}:{args.port}", f"localhost:{args.port}"})
        )
    config = ControlHttpConfig(
        schema_version="aero-bench.control-http-config/v1",
        bind_host=args.bind_host,
        port=args.port,
        allowed_origins=origins,
        allowed_hosts=allowed_hosts,
    )
    if args.sealed_run_id is not None:
        manager = SealedReplayManager.from_files(
            suite_path=args.suite, runner_config_path=args.runner_config,
            run_ids=tuple(args.sealed_run_id),
        )
    elif args.compilation_id is None:
        manager = ControlRunManager.from_files(
            suite_path=args.suite, runner_config_path=args.runner_config,
        )
    else:
        manager = ControlRunManager.from_compilation(
            compilation_root=args.compilation_root, compilation_id=args.compilation_id,
            runner_config_path=args.runner_config, output_root=args.execution_output,
        )
    server = ControlHttpServer(
        manager=manager,
        config=config,
        bootstrap_token=bootstrap_token,
        bootstrap_csrf_token=bootstrap_csrf,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        client = _client(args)
        if args.command == "catalog":
            _write_json(client.catalog())
        elif args.command == "start":
            _write_json(client.start(run_id=args.run_id, start_id=args.start_id))
        elif args.command == "status":
            _write_json(client.status(args.run_id))
        elif args.command in {"pause", "resume", "step", "stop"}:
            _write_json(
                client.control(
                    args.run_id,
                    operation=args.command,
                    control_id=args.control_id,
                )
            )
        elif args.command == "events":
            for event in client.events(
                args.run_id,
                after_transition=args.after_transition,
                after_scene_tick=args.after_scene_tick,
                after_event_sequence=args.after_event_sequence,
            ):
                _write_json(event)
        else:
            raise ValueError("control command is unsupported")
    except (ControlApiClientError, ControlHttpServerError, ControlManagerError) as error:
        code = getattr(error, "code", "control.failed")
        detail = getattr(error, "detail", "control operation failed")
        print(f"{code}: {detail}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
