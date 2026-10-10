# External observation and semantic integration foundation

This slice supplies independent Python contracts and read-only adapters. BENCH
remains the intended physics/time authority and monitoring host. Its existing
Three/Vite viewer will own the shared frame/cursor/selection store and host the
Atlas panel. This change adds no frontend, API endpoints, simulator processes,
live attachment, or controls.

All included data is authored synthetic data. No BENCH implementation, actual
trace, map, asset, credential, or Atlas source bundle is included. The BENCH
mapping derives from the reviewed interface specification for server commit
`ea295073cdd310a31fa2e29317a3571bc716bb87`; this checkout contains no private source.

## Modules and versions

| Module | Responsibility |
| --- | --- |
| `integration.contracts` | Immutable identity, ENU observations, decimal nanoseconds, render transform and JSON payload export |
| `integration.normalization` | Strict `aeroagentsim.observation/v1` decoding; no implicit zero motion |
| `integration.replay` | Small `aeroagentsim.replay-index/v1` reader with injected opaque artifact lookup and exact/previous seek |
| `integration.semantics` | Exact field projection, isolated binding history and optional native evaluator protocol |
| `integration.selection` | `aeroagentsim.selection/v1` and `aeroagentsim.shared-view/v1` reducer with complete-key race guards |
| `integration.bench` | Documented motion paths and three independent source stream cursors; no HTTP/SSE connection |
| `integration.network` | Separate provider aggregate receipt, network geometry and directed link properties |
| `integration.bench_replay` | Source index/header/integrity guards that delegate native file/shard validation to the existing private reader |

The modules do not import native application globals, `SimulationManager`,
`RunRepository`, SimPy or TraCI. Importing the public AeroAgentSim package still
loads its existing package exports, including the `Environment` class; the
integration does not construct an environment or invoke its lifecycle.

## Identity and time

`FrameKey` contains the attachment ID, canonical run ID, run epoch, manifest
revision, canonical frame sequence, SHA256 of observation bytes and stage evidence
key. `ViewKey` adds evaluation revision and binding epoch. Every cursor, selection
and evidence result uses the complete key. A frame sequence alone is insufficient.

Neutral nanoseconds are canonical nonnegative decimal strings. Integer
subtraction occurs before conversion to shared relative seconds:
`(sim_time_ns - engine_origin_ns) / 1e9`. The engine origin is fixed by the caller
for the run; it is not rebased on seeking or selecting entities. The fixtures use
times above JavaScript's exact integer range. A browser-side source parser must
preserve source integer nanoseconds before any conversion to decimal strings.

Motion uses ENU metres and m/s. Rendering is `[E, N, U] -> [E, U, -N]`. Native named
quaternion fields remain named; this projection does not infer an unprovided
body-to-world convention. ENU up, AGL, AMSL, terrain AMSL and ellipsoid height
remain distinct quantities.

## Neutral replay

The neutral index references immutable bytes by unique opaque IDs. The supplied
`MappingArtifactReader` copies the authorized mapping. There is no file path or
URL ingestion API. A trusted caller materializes authorized artifacts outside the
reader and injects their bytes. Frame/index hashes, identity, units, chronology,
complete entity sets and declared lifetimes are checked before seeking.

The authored neutral index requires nonempty frames and includes inclusive first
and last frame bounds. Gaps are half-open intervals. Exact seek returns an indexed
frame or an explicit `not_indexed`, bounds or gap result. Previous seek returns
the preceding observation, preserving its original timestamp and the separate
requested timestamp. It never interpolates, relabels a previous frame as current,
clamps out-of-range requests, or carries an observation across a declared gap.

Entity motion validity is explicit and exclusive: `valid_until_ns` is the first
unknown instant unless a new valid sample arrives at that instant. A frame may
contain an active entity with null motion. The small neutral fixture declares
lifetimes in advance; an active entity cannot silently disappear from a complete
frame, and an entity ID cannot be reused within one epoch.

This eager reader is a synthetic/reference reader, not a second production BENCH
replay engine. BENCH sealed replay uses embedded/indexed manifests, tick ranges
and shards. The thin source guards preserve empty indexes, validate run/scenario/
event-chain identity, ordered shard ranges, counts up to 256, safe relative paths,
byte size and SHA256. Shard reads resolve through an explicit path-to-opaque-ID
allowlist. `history_shards()` retains all previous shards to preserve old latches.
It locates candidate shards; the private native decoder must validate actual
scene identities, counts, exact ticks and source range errors.

`PublicReplayFile`, native path-regex membership, shard body schemas and replay
file-reference closure are unexpanded boundaries. The injected file-reference
and shard validators must enforce them. No replay access token is minted and no
sealed replay cursor drives a harness.

## Documented BENCH projection

The independent adapter joins `scene_state.samples` to canonical
`ResolvedScenario.entities` by exact `entity_id`, never list order. It checks the
declared set, run/scenario identity, tick/time agreement, motion sample stage,
static/dynamic provider identity, digest format and redundant ENU/NED evidence.
The first observed scene tick is 1. `initial_pose` is validated configuration and
never generates observed tick-0 history.

| Neutral value | Documented source or derivation |
| --- | --- |
| ENU position | `sample.pose.position.enu.{east_m,north_m,up_m}` |
| ENU velocity | `sample.linear_velocity_enu.{east_mps,north_mps,up_mps}` |
| Horizontal speed / `hu.actor.speed_mps` | `hypot(vE, vN)` |
| Positive-up vertical speed / `hu.actor.vertical_speed_mps` | `vU` |
| Pair radial closing speed | `-dot(r,v)/norm(r)`; coincident centres yield null |
| Pair CPA time | `-dot(r,v)/dot(v,v)`; zero relative velocity yields null; negative time is preserved |
| Pair surface clearance | Supported only for caller-declared physical spheres in the synthetic/reference contract |

The source `ResolvedEntity` has no mobile body dimensions. `model_asset_id` is
preserved as a reference and supplies no extent. Actual `pair_close` and CPA body
clearance remain unsupported without an authorized geometry source. No bounding
sphere, mesh radius, collision size or building prism is guessed. Optional
mode/armed/battery/health/angular velocity stay absent/null when unavailable.

Kind classification, entity lifetimes and freshness expiry are explicit caller
policies. Native `EntityKind`/`AuthorityKind` members, StageBarrier internals and
optional battery/health/attribute substructures are not fully validated here.
The caller supplies a barrier validator; native digest recomputation is also an
injected boundary. Without a digest verifier, provenance explicitly says
`format_and_identity_only`, not cryptographic source verification. A complete
native schema validator remains required before using real inputs.

The independently authored BENCH-shaped fixture exercises the documented paths.
Its kind/authority enums and StageBarrier internals are labelled placeholders;
its declared digests are synthetic. It is not a certified native BENCH scenario
or real trace. Identifiers and hash-shaped references follow the documented
identifier and SHA256 formats. Generic neutral aliases are not claimed to be
valid BENCH identifiers.

## Network and stream separation

`ns3.state.v4` receipt payloads contain named digests and provider aggregates.
`PublicNetworkFrame` contains node/link geometry. `public.network.link.v3`
documents contain directed named radio/queue properties with per-property
provenance. Each representation is normalized independently.

RSSI retains dBm; SNR/loss retains dB; distance retains m; queues retain packets
and bytes. Obstruction IDs are parsed with JSON and must be an identifier array.
Duplicate property names, malformed quantities and count/ID disagreements fail
validation. Missing quantities and null digests remain unavailable. Receipt
throughput is an aggregate and is never assigned to each link. No message loss
ratio is computed without count/window/reset semantics. Above-sensitivity is
only a radio observation, not connectivity or delivery proof.

Geometry/property joins require matching attachment/run/epoch/manifest/scenario,
tick, time, link/entity aliases and caller-verified same-scene evidence. The
outer public event alone does not expose a verified scene linkage in the supplied
boundary. `PublicEventIdentity.verified_scene_digest` is explicit caller evidence,
not an invented source field. Missing or disagreeing proof returns `pending`.
No network-specific Atlas predicate binding is inferred.

`BenchStreamCursors` models the actual three query cursors:

| Query cursor | Initial value | Source |
| --- | --- | --- |
| `after_transition` | -1 | `transition.sequence` |
| `after_scene_tick` | 0 | `scene_state.at.tick` |
| `after_event_sequence` | -1 | `event.sequence` |

The pure reducer checks envelope family/identity, processes the item through an
injected complete-family validator and only then advances that family. Duplicate
last items cannot change content; sequence gaps require explicit resynchronization.
No single shared source sequence or `Last-Event-ID` replaces these cursors. An
adapter-generated canonical sequence retains the source tuple in provenance.
Authenticated fetch SSE, its 2000 ms reconnect policy, server URL configuration
and durable resume storage belong to the subsequent private host integration.

## Optional Atlas boundary

No real Atlas runtime or registry is installed by this slice. Approved source
materialization remains a prerequisite. There is no dynamic arbitrary-module
loader, bundled private code, threshold Boolean substitute or fixture evaluator
in production modules.

The host injects a private bridge implementing
`NativeEvaluator.evaluate_batch(EvaluationRequest)`. For each compatible binding/
history/parameter envelope, the bridge calls the real native `evaluate` or
`evaluate_many` once. It must adapt the actual source API, preserve lexical
reference parameters and transitive temporal dependencies, and create a fresh
native mutable mapping/cache for each independent binding. `thaw_json()` makes
independent copies of immutable state maps. Native truth is only `True`, `False`
or `None`; richer reasons and censoring are wrapper metadata, never extra truth
values or replacements for native strong-Kleene logic.

Bindings declare ordered entity tuples, exact native field IDs, opaque target
IDs, registry revision and lexical parameters. Two instances of the same target
require separate binding IDs. A compound owner label is never split into entities.
Changing actors/field maps requires a binding epoch; changing parameters/registry/
targets requires an evaluation revision. History is retained across parameter
reevaluation within a physical binding epoch and is not a 3/5-second ring buffer.
Its full coverage and explicit null gaps are available to native temporal logic.
Starting late is marked censored. Backward evaluation requires a fresh session
warmed with the full relevant prior history; the reference session is dispatched
serially by its owner.

Requested motion targets include actor_moving, vertical_ascent, vertical_descent
and actor_stationary, with pair_close limited to declared physical geometry.
Target IDs are registry input; those requested names do not define the complete
catalog. Until the bridge is available, every target returns null with
`evaluator_unavailable`. Synthetic spy tests check Boolean/null pass-through,
context isolation and history delivery; they do not establish native predicate,
temporal latch or Python/JavaScript semantic parity. This slice emits no event
occurrences; requests explicitly carry `no_occurrences_emitted` pending the
verified native event occurrence policy.

## Renderer and selection handoff

`SharedViewStore` is a small reference reducer with an atomic snapshot; its owner
activates an intentional cursor/revision change. Selection and asynchronous
evidence commits must match the current complete `ViewKey`, otherwise they are
rejected. Activation clears obsolete selection/evidence. `contract_payload()`
exports fresh JSON-compatible objects without losing nanosecond strings, null
truths, binding tuples or nested identity keys.

The private viewer should implement the same reducer shape in its shared store,
then pass one snapshot to its existing scene renderer and Atlas component.
Selections carry kind, ordered entity IDs and optional binding/state/target/graph
node IDs. The host's validated graph-binding registry maps nodes to these exact
references. A click updates selection only; it never calls Atlas `choose()` to
load authored examples, replaces the live observation, seeks another production
engine, or sends simulator commands. Live observation and sealed replay modes
are explicitly distinct; this reducer contains no control channel.

## Verification and next private slice

From the repository root, using the setup virtual environment:

```bash
.venv/bin/python -m pytest tests/test_integration -q
.venv/bin/python -m black --check --line-length 127 src/aeroagentsim/integration tests/test_integration
.venv/bin/python -m flake8 --max-line-length 127 src/aeroagentsim/integration tests/test_integration
```

The tests exercise authored neutral/partial BENCH boundaries, integrity and source
immutability, exact time/transform roundtrips, body-aware versus unavailable
clearance, lifetime and validity gaps, sparse stationary history, full latch
history delivery, registry/parameter revisions, independent same-target contexts,
view races, three source cursors, separate network representations and sealed
index/shard guards. Tests do not run native simulations or server processes.

Next requirements are approved Atlas source and actual API/registry parity
checks; complete native source validators and sealed-reader hooks; authoritative
body geometry and validity policies; authenticated live transport with explicit
server configuration; and the existing private viewer's same-store component
mount. The console fixture now exports neutral observations to this reader and
has a separate bilingual browser verification runner; see
`frontend/console-prototype/README.md`. Native temporal/event parity, live SSE,
SUMO/ns3 synchronization and production viewer mounting have not been exercised. Controller
intents and scenario compilation remain later milestones.

Native `/api/runs` must not be used for external attachment: its GET list/status
reconciliation can rewrite manifests using local simulator status, and POST
creates/resets native simulation. This foundation leaves those routes unchanged
and calls none of them.
