# AERO-BENCH

AERO-BENCH is a configuration-first benchmark runtime for low-altitude agents.
It follows the isolation and independent-evaluation boundary used by SWE-bench-
style benchmarks: the benchmark owns the task, environment, tools, evidence
contracts, executor, and verifier; a participant supplies its own model and
policy as a digest-addressed Agent image.

No mock Provider, synthetic telemetry, analytical fallback, compatibility
parser, or Viewer-generated benchmark datum is accepted as formal evidence.

## Workspace layout

- `aero_bench/`, `containers/`, `schemas/`, `tools/`, and `releases/` hold the benchmark runtime, container entry points, contracts, build tools, and declared releases.
- `frontend/src/` holds Viewer code. `frontend/public/` holds browser-ready models and scene packs. `frontend/dist/` is a generated build used by Vite preview.
- `validation/downloaded-assets/` holds the original Baidu and Quark packages. `frontend/assets/incoming/` keeps their inventory and audit files; it does not mirror the large ZIP and preview files. Asset extraction belongs in a named output directory, never the repository root.
- `validation/` holds local run evidence and visual checks. The independent business-system development worktree is kept separately from this repository; `validation/business-system/` contains its review records.

## Independent Agent inspection

The independent Astra inspection task adds granted Business queries, real PX4
flight/GNSS observations, model-visible Gazebo images, and an isolated function
interface with sealed model/Gateway audits. Its two-session acceptance is tracked
separately from the historical reference slice below. See the
[runtime and evidence guide](docs/AGENT_INSPECTION_RUNTIME.md) and
[approved acceptance plan](docs/AGENT_INSPECTION_E2E_PLAN.md).

## Inspection v1 release status

The bounded **Docker Inspection v1 reference slice passed** a real
`formal_benchmark` run on 2026-08-31. This is a GO only for the exact committed
slice in `releases/inspection-v1/`, not for Kubernetes, every benchmark family,
or broader world/traffic composition.

- Run ID: `a10ec4315d9c0ecaf28418f0005fe2778d0dc828f03b985dc114e85b5a0d529e`
- Runner result: `1/1 passed`; preflight ready; no failure classes
- Runtime: real PX4 SITL + Gazebo Sim + patched MAVSDK, real ns-3, and the
  Inspection Business Provider
- Evidence: exactly nine declared artifacts, sealed after a contiguous
  Provider-barrier ledger
- Independent Verifier: `passed`, goal `inspection.success`, metric
  `inspection.success_rate = 1.0`
- Event-chain root:
  `2bf0e5050bc9f0de722dbd5e835026e3a07dcf6aa4f087660c2108b46ccb2ddd`
- Seal-manifest digest:
  `42be57cf473a619bc17663d38c68bd71f8b5805db1fd1a34ee997a86dc0674a0`
- Public Trace SHA-256:
  `79e74c42a04ba709b60e567cad52f4235fc9084a612fb79da7bae864a7d1837c`
- Docker-owned containers, networks, and volumes after cleanup: all empty

The five benchmark-owned images in `release-lock.json` share source revision
`c63f3280581755bb4d0201aa9a2f8e13be81ee4d`. The validation Agent is recorded
separately as an external participant submission; its policy is not benchmark
implementation code. See [Agent submissions](docs/AGENT_SUBMISSIONS.md).

The exact machine-readable release lock, runner summary, runtime seal,
verification output, Public Trace, cleanup record, and Viewer screenshot are
under `releases/inspection-v1/validation/`.

## Formal architecture

```text
SuiteSpec + TaskSpec + EnvironmentSpec + AgentSpec
→ immutable, self-authenticating ResolvedRunSpec
→ formal preflight
→ Docker Reference Executor
→ Harness / Provider Barrier / Gateway / participant Agent
→ exact artifact seal
→ isolated independent Verifier
→ deterministic Public Projector
→ read-only OSM2World Viewer
```

The formal Inspection graph is bounded to three Providers:

- `flight`: PX4 SITL, Gazebo Sim, MAVSDK command acknowledgement, real camera
  observation, and telemetry-derived trajectory;
- `network`: ns-3 message-in-the-loop delivery evidence;
- `business`: authoritative work-order lifecycle and business-history chain.

Harness writes the authoritative event log and theoretical bounds. The
participant writes detection and report evidence. Verifier-private truth is
bundle-owned and never mounted into the Agent. The resulting exact-nine
inventory is:

1. business state
2. trajectory
3. delivery
4. observation
5. truth
6. detection
7. report
8. theoretical bounds
9. event log

World Scene is intentionally not a fourth Provider in this exact-nine case;
adding its artifact would change the declared benchmark case. The PX4/Gazebo
Provider instead publishes authoritative WGS84 state for the UAV and declared
inspection target plus a telemetry-derived trajectory. The ns-3 link endpoints
are authoritative logical identifiers; the Viewer displays them without
inventing positions.

The urban package `urban.uav-recovery-demo.v1` is independent of this Inspection graph. Its PX4/Gazebo backend records authoritative telemetry, physics, airspace, force, wind, and contact evidence without RGB capture; the browser renders the public Shanghai OSM2World scene and replays sealed trajectory/SceneState shards. Urban readiness therefore requires the sealed replay index and complete tick coverage, while the Inspection camera path above remains unchanged.

## Run the committed slice

The runtime is offline: all source downloads and hash checks happen while the
images are built; formal execution performs no network downloads. The committed
RunnerConfig expects the repository digests listed in
`releases/inspection-v1/release-lock.json` to exist in the local registry and
Docker daemon.

```bash
python -m aero_bench.runner.cli \
  releases/inspection-v1/suite.yaml \
  releases/inspection-v1/runner.local.yaml
```

Missing images or identity drift are preflight blockers and produce a non-zero
result. They are never converted into skips.

To submit another Agent, replace the bundle's `AgentSpec` with a participant's
own production, digest-addressed image and resolve a new run. The benchmark
provides only the generic `AgentContext` and `GatewayClient` SDK boundary.

## OSM2World / SUMO Viewer

The Viewer is a standalone Vite/Three.js application under `frontend/`. Its configured
Shanghai scene is an offline OpenStreetMap extract
(`frontend/public/osm2world/shanghai-hongqiao.osm.json`) converted in the browser by
the official OSM2World web module (`O2WConverter` + `standard.properties`). Public
traces replace the source geometry with their authoritative scenario while keeping
the same OSM2World style. Replay accepts only strict `aero-bench.public-trace/v3`
documents. The visual clock follows recorded simulation-time intervals and never
invents samples.

```bash
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run preview -- --host 127.0.0.1 --port 4173
```

Open `http://127.0.0.1:4173/?scene=1` for the full-screen configured OSM2World city,
`http://127.0.0.1:4173/` for the control console, or
`http://127.0.0.1:4173/?view=replay` for sealed replay selection. The formal
SUMO source is generated by `aero_bench.providers.sumo.scene` and exported by
`tools/export_sumo_trajectories.py`; the checked-in release contains the real
600 s TraCI JSONL artifact under
`releases/urban-infrastructure-inspection-v1/world/sumo/trajectories.jsonl`.

## Run the integrated stack

With the Python dependencies and frontend dependencies already available, start
authoring, Control and the viewer with one command. Replay mode serves an
existing sealed run without starting an execution:

```bash
python tools/run_stack.py \
  --control-suite /path/to/suite.yaml \
  --control-runner-config /path/to/runner.yaml \
  --control-sealed-run-id '<sealed-run-id>' \
  --authoring-output /tmp/aero-authoring \
  --native-scenes-manifest /path/to/native-scenes.json
```

For a published Studio compilation, start Control for that compilation ID:

```bash
python tools/run_stack.py \
  --control-compilation-id '<compilation-id>' \
  --control-compilation-root /path/to/compilations \
  --control-execution-output /path/to/fresh-execution-output \
  --control-runner-config /path/to/runner.yaml \
  --authoring-output /tmp/aero-authoring \
  --compilation-output /path/to/compilations \
  --native-scenes-manifest /path/to/native-scenes.json
```

The launcher picks three distinct free loopback ports and pins Control's allowed
origin to the viewer port. Override them with `--authoring-port`, `--control-port`
and `--frontend-port`; an occupied port fails before any service starts. The
default frontend is Vite dev on the current source. To use a separate existing
build, pass `--frontend preview --frontend-dist /path/to/build`;
`frontend/dist` is refused. The launcher does not build or install dependencies.

Startup prints the run directory, each readiness URL and its 900-second timeout.
All three services start at once and are then awaited in order. The viewer is
ready when its workspace-traces catalog answers; the viewer scans the workspace
traces when it starts, and that scan overlaps Control startup.
Once all services are ready, it prints the console, replay and Studio URLs, the
Control base URL to enter in the console, and the credentials-file path. Read
that private JSON file locally and type its `bootstrap_token` and
`bootstrap_csrf` values into the console. Credentials are never printed or
passed in command arguments. Use `--credentials-file /path/to/new-file.json`
to choose the path; otherwise it is under `$XDG_RUNTIME_DIR`, or in a private
temporary directory. The file has mode 0600 and is removed on shutdown.

Add `--check` to either example to run five HTTP smoke checks: viewer HTML,
the proxied authoring native-scenes JSON catalog, the workspace-traces catalog,
an authenticated Control catalog with a non-empty runs list containing every
requested sealed run ID, and a 403 `origin.rejected` response for a wrong origin.
Each check prints PASS or FAIL. The launcher stops all its services afterward
and exits zero only when every check passes. These checks prove HTTP wiring,
authentication and origin enforcement; they do not execute a benchmark or test
browser rendering.

Ctrl-C or SIGTERM stops all child process groups, using TERM followed by KILL
after five seconds when needed. Logs and child PID files remain in the printed
run directory (`--run-dir /path/to/new-directory` selects it). A failed readiness
check names the service and prints its last 40 log lines, then stops the stack.
When `--compilation-output` is omitted, authoring stores compilations under that
run directory.

A running Control instance serves one suite or one compilation and does not
pick up new Studio compilations. To run a draft compiled in the Studio:

1. Start the stack with a fixed `--compilation-output /path/to/compilations`,
   so compilations outlive the launcher's run directory.
2. Compile in the Studio. The compiled card ("已编译，尚未运行") shows the
   compilation ID after `编译`.
3. Stop the launcher with Ctrl-C and start it again with
   `--control-compilation-id '<that id>'`,
   `--control-compilation-root /path/to/compilations` (the same directory),
   a `--control-execution-output` path that does not exist yet (Control refuses
   an existing one with `output.not_fresh`), and the same
   `--compilation-output`.
4. Open the console URL, enter the new credentials, and start the run.

## Scope boundaries

- `docker_reference` is the validated single-host Reference Executor.
- `kubernetes_cluster` remains explicitly deferred pending a real cluster,
  RBAC, NetworkPolicy enforcement, storage, registry access, and a trusted
  cluster-side seal publisher. Docker is not used as its fallback.
- The Shanghai viewer composition is a read-only public projection; formal
  PX4/Gazebo authority remains separate from the SUMO traffic provider.
- The committed evidence proves one bounded reference case and does not imply
  that arbitrary participant Agents will pass.
- A broad post-flow Python pytest/ruff sweep was intentionally deferred; the
  formal run, release-lock validation, focused frontend checks, production
  Viewer build, exact-trace browser load, and handoff checks are recorded
  separately.

See [Architecture](docs/ARCHITECTURE.md),
[Validation](docs/VALIDATION.md), and [Handoff](HANDOFF.md) for exact boundaries
and evidence identities.
