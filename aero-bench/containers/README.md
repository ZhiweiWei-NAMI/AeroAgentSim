# Production simulation images

These images are real external-runtime selfcheck images for the Docker
Reference Executor. They are not benchmark fixtures and contain no analytical
or kinematic fallback. Only the ns-3 and SUMO images currently expose a
formal Provider service; the PX4/Gazebo image remains selfcheck-only and its
formal Provider path is explicitly BLOCKED.

Pinned external identities:

- PX4 SITL: `381149fb012762f5e38c4a7fdc1b905b28038970`
- Gazebo Sim 8.11.0: `1be3cc376fec778cc725b4eeea463245affa56d3`
- MAVSDK 3.17.2: `9e3ca17faa84aa868caea10a3bbdab7e53810ced`
- ns-3 3.48: `d2add90b452d600cfb4859baed8e9ea633519447`
- SUMO 1.27.1: `7717f2379d9e314a0c81c5cec748444de06a2a91`

Every downloaded archive, wheel, binary, and base image is SHA-256 pinned in
its Dockerfile or generated requirements lock. Pass the source commit used for
the build explicitly:

```text
docker build --build-arg AERO_BENCH_REVISION=$(git rev-parse HEAD) \
  -t aero-bench/px4-gazebo:dev -f containers/px4-gazebo/Dockerfile .
docker build --build-arg AERO_BENCH_REVISION=$(git rev-parse HEAD) \
  -t aero-bench/ns3:dev -f containers/ns3/Dockerfile .
docker build --build-arg AERO_BENCH_REVISION=$(git rev-parse HEAD) \
  -t aero-bench/sumo:dev -f containers/sumo/Dockerfile .
```

The ns-3 entrypoint is the real JSON-line Provider service. Run it with the
same primary isolation boundary used by the executor:

```text
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges aero-bench/ns3:dev
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:size=256m aero-bench/sumo:dev
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:size=2g \
  aero-bench/px4-gazebo:dev
```

The ns-3 service binds only to the explicit
`AERO_BENCH_PROVIDER_BIND_HOST`/`AERO_BENCH_PROVIDER_PORT` workload
environment and verifies the executor-supplied provider contract before
starting the listener. Its explicit `selfcheck` argument runs the real
message-in-the-loop self-check instead of starting the service:

```text
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges aero-bench/ns3:dev selfcheck
```

The provider service additionally requires the executor-supplied
`AERO_BENCH_RUN_ID`, `AERO_BENCH_SEED`, and an empty writable
`AERO_BENCH_ARTIFACT_DIR`, plus `AERO_BENCH_ROLE=provider`,
`AERO_BENCH_WORKLOAD_ID`, `AERO_BENCH_CONTRACT`, and
`AERO_BENCH_BUNDLE_DIR`. A reference run must provide all of these values
explicitly; the service never derives a run ID or chooses a bind address or
port. The listener exposes a side-effect-free `{"operation":"probe"}`
request before `prepare`; it returns `aero-bench.provider-probe/v1` with
`status=accepting` and the verified workload identity. The service writes
exactly one canonical file at the declared
`relative_path` for the `network.delivery` requirement using an atomic
replacement. For a local two-barrier provider regression, use the explicit
`provider-selfcheck` arguments:

```text
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  --tmpfs /artifacts:rw,uid=65532,gid=65532,mode=700 \
  --env AERO_BENCH_RUN_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --env AERO_BENCH_SEED=19 \
  --env AERO_BENCH_ARTIFACT_DIR=/artifacts \
  localhost:5000/aero-bench/ns3@sha256:c94ddf5543b96e7e133458605dcdc4c0fd9ae86d45cdc2eb44a971f62b11dd8b provider-selfcheck \
  --runtime-image localhost:5000/aero-bench/ns3@sha256:c94ddf5543b96e7e133458605dcdc4c0fd9ae86d45cdc2eb44a971f62b11dd8b \
  --run-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --seed 19 --artifact-dir /artifacts
```

The `provider-selfcheck` command requires the run ID, seed, artifact
directory, and digest-pinned runtime image as explicit arguments; the values
above are only invocation examples and are not service defaults.

The SUMO and Harness images use the same one-shot readiness probe. SUMO's
provider probe means that the service can accept `prepare`; it does not claim
that TraCI has been reset. Harness is healthy only after its providers have
been prepared/reset and its Gateway listener is bound. Healthchecks connect
only to `127.0.0.1`, use the executor-declared port, and never retry or
advance a runtime.

Formal Docker preflight verifies this contract from each digest-pinned
provider and Harness image's immutable config: the exec-form healthcheck must
invoke `/opt/aero-bench/readiness_probe.py --timeout-seconds 2` through the
image's Python interpreter. An arbitrary valid healthcheck is not sufficient.

The PX4 check starts a paused Gazebo world, starts PX4 SITL and the official
MAVSDK server, requests exactly 2,500 physics steps of 4 ms, verifies Gazebo
reaches exactly 10 s, and reads authoritative PX4 telemetry through MAVSDK.
This is a real external-engine selfcheck only: current main has no PX4
Provider registration, RPC service, or shared readiness path, so a formal
PX4 Provider run is BLOCKED and this output is not benchmark evidence.
The ns-3 check sends a real UDP payload through a configured point-to-point
device. The running ns-3 service uses the same real UDP sockets and point-
to-point devices for each submitted message; delivery evidence is returned
only after a `step_to` operation has advanced the discrete-event simulator.
The SUMO check runs a generated road network and verifies actual TraCI vehicle
motion.
