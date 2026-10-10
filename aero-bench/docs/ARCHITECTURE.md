# AERO-BENCH Execution Architecture

## Executor decision

`docker_reference` is the validated formal single-host Reference Executor. It
is selected explicitly and recorded in evidence; it is not a Kubernetes
fallback. `kubernetes_cluster` remains a separate, deferred contract.

Execution profile and evidence scope are independent. A Suite declares either:

- `executor_validation`: mechanical infrastructure evidence, never a benchmark
  PASS; or
- `formal_benchmark`: production identities, exact OCI repository digests,
  complete execution, independent verification, and public projection.

The bounded Inspection v1 release uses `formal_benchmark` and passed on
2026-08-31. Its exact identities are in
`releases/inspection-v1/release-lock.json`.

## Trust and network graph

```text
participant Agent(s) ── agent-internal ── Harness/Gateway
                                             │
                                      provider-internal
                                             │
                          PX4/Gazebo ── ns-3 ── Business

independent Verifier: network=none
  read-only resolved bundle + runtime seal → one private output volume

Public Projector: sealed ledger + validated public report → Public Trace v2
Cesium Viewer: read-only Public Trace v2 only
```

The Agent cannot resolve or connect to Provider containers. Providers cannot
resolve Agent containers. Only Harness joins both role networks. No workload
receives a Docker socket or host bind mount. Each role receives a separate,
strict workload contract and only bundle files authorized for that audience.
Verifier-private truth is never mounted into the Agent.

Docker workloads run non-root with a read-only root filesystem, dropped
capabilities, bounded scratch/tmpfs, memory/CPU/PID limits, and digest-pinned
images. Every producer owns a separate bounded artifact volume. A network-less
keeper holds that volume after the producer exits so the executor can enumerate
and seal it without granting cross-producer writes.

Before object creation the executor proves the deterministic Docker name is
absent. Every created object receives a per-handle owner token. Cleanup verifies
that token before deletion; it will not delete a replacement resource that only
shares the name.

## Immutable resolution and runtime input scope

```text
SuiteSpec + TaskSpec + TaskPackage + EnvironmentSpec + AgentSpec
→ ConfigResolver
→ immutable ResolvedRunSpec and canonical run_id
→ executor preflight and materialization
→ runtime revalidation of the Harness-visible subset
```

Resolution validates schema-bound configuration, package feasibility, declared
assets, grants, artifact ownership, implementation identity, and image-digest
separation. The executor re-reads the source Suite, repeats its
case/matrix/seed expansion, and requires the serialized run to equal one exact
resolver output.

Harness does not receive Provider-, Agent-, or Verifier-private files merely to
repeat source-Suite validation. At startup it validates the self-authenticating
ResolvedRun, its pinned Task Package and schemas, runtime configs, Agent grants,
and recomputed feasibility against its exact read-only bundle subset. Only the
artifact output mount must be writable.

## Authoritative runtime

Harness owns simulation time and the event ledger. A barrier commits only after
every Provider returns a receipt for the same target tick and simulation time.
The Tool Gateway binds command and observation times to that authority,
validates grants and JSON Schemas, and records authenticated command/observation
identities. Non-idempotent command IDs are consumed before dispatch so response
loss cannot repeat a side effect.

The terminal seal uses two-phase finalization: Harness computes the prospective
terminal chain root, each producer finalizes its declared artifacts against
that root, and only then does Harness append the exact terminal event. No ledger
event may follow it.

### PX4/Gazebo lifecycle

The production flight Provider uses real PX4 SITL, Gazebo Sim, and a
benchmark-built MAVSDK server. Its barrier lifecycle is:

```text
RUNNING → BARRIER_PAUSED → ACTION_STAGED → EXECUTING → BARRIER_PAUSED
```

A command issued at a paused barrier is staged. The Provider resumes/steps
Gazebo, dispatches the MAVSDK command, waits for a real `COMMAND_ACK`, then
pauses at the next exact barrier. Camera collection is taken from the real
Gazebo sensor; trajectory is derived from live simulator telemetry.

The MAVSDK incoming-vehicle-heartbeat timeout is distinct from its outgoing
watchdog:

```text
(maximum_agent_decision_wall_time_ms + fixed_margin_ms) / 1000
```

The pinned MAVSDK source carries the release patch that exposes
`--incoming-heartbeat-timeout-s`. `--heartbeat-watchdog-timeout` is not used as
a substitute.

### Inspection runtime hook

The registered Inspection hook keeps domain transitions out of the generic
Gateway:

- a validated observation from the declared flight Provider authenticates the
  idempotent `observation_ready` transition;
- report delivery is accepted only after real ns-3 reaches the delivery event;
- Business completes the work order only when the delivered payload digest
  matches the submitted report digest.

All internal actions use explicit provider-actor identities and are recorded in
the same authoritative ledger and Business history as participant-originated
commands.

## Exact-nine evidence and independent verification

The bounded Inspection case declares exactly these artifacts:

| Kind | Producer | Path |
|---|---|---|
| business state | Business | `public/business/state.json` |
| trajectory | PX4/Gazebo | `public/flight/trajectory.json` |
| delivery | ns-3 | `public/network/delivery.json` |
| observation | PX4/Gazebo | `private/flight/observation.json` |
| truth | bundle | `private/bundle/truth.json` |
| detection | participant | `public/agent/detections.json` |
| report | participant | `public/agent/report.json` |
| theoretical bounds | Harness | `public/harness/theoretical-bounds.json` |
| event log | Harness | `private/harness/event.log` |

The seal enumerates every producer volume and the complete seal root. Missing,
extra, duplicate, changed, oversized, linked, or special files fail closed. The
canonical event log is replayed to derive the seal's event-chain root; a caller
cannot supply another root.

The isolated Verifier receives the immutable ResolvedRun, read-only seal, exact
pinned schemas and private truth, but no network. It strictly parses canonical
evidence bytes and checks the nine seal records, global ledger, Business local
chain, trajectory, observation bytes and digest, ns-3 delivery, report/detection
bindings, theoretical bounds, and hidden truth. It writes exactly one public
verification report. A pass requires complete, non-vacuous goal coverage and a
finite JSON numeric metric.

## Public projection and Viewer

After verification, the deterministic Public Projector consumes the validated
sealed ledger, seal manifest, and public verifier report. It resolves each
public metric evidence reference to an artifact ID, selector, visibility, and
sealed digest, then writes canonical `aero-bench.public-trace/v2` bytes outside
the immutable runtime seal. A projection error makes the runner result an
error; it cannot leave a nominally passed run pointing at missing bytes.

The Cesium Viewer accepts only strict Public Trace v2 selected through its file
chooser. It never reads private evidence or invents data. Cesium initialization
or render failure is contained so textual evidence remains usable. The exact
Inspection v1 trace contains authoritative PX4/Gazebo WGS84 state for `uav.1`
and `target.1`, a telemetry-derived trajectory, Business/network events, PX4
command receipts, an ns-3 delivery link, provider and task status, and
Verifier-bound public evidence. Logical network endpoint IDs do not imply a
published pose and remain non-geospatial labels.

## Release identity

The release lock separates five benchmark-owned workloads from the external
participant submission. All managed images carry matching component,
implementation-kind, source, revision, and version labels and share revision
`c63f3280581755bb4d0201aa9a2f8e13be81ee4d`. The participant's image,
revision, and source identity are recorded independently. Reusing the Verifier
image for the volume-keeper role is declared as an executor auxiliary role, not
a sixth managed build.

Formal runtime performs no network downloads. Upstream source archives and
hash-locked Python wheels are fetched and verified during image build;
compilation stages intended to be offline run with network disabled.

## Deferred boundaries

- WorldPackage contracts exist, but broader World Scene materialization is not
  part of this exact-nine formal case.
- SUMO and Airspace are not claimed as exercised by Inspection v1.
- Kubernetes remains deferred until a real kubeconfig, Namespace/RBAC,
  enforced NetworkPolicy, storage, registry access, and trusted cluster-side
  seal publisher exist. Docker never silently substitutes for it.
- The passed reference case proves the runtime and evaluator on one bounded
  submission; it does not guarantee arbitrary participant performance.
