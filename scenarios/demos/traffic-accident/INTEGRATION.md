# Job B integration contract

B owns this scenario, `packs/traffic_accident`, its importer and four domain test
modules. No loader, plugin catalogue, packaging, service or frontend is changed.
The full scenario is a D1/A integration input: today's loader deliberately rejects
its additive `behaviours` key. Domain tests explicitly remove that key and select
only physical/input/computation owners. This is not a successful accident chain.

Add exactly these lines under the existing pyproject table (F owns that file):

```toml
[project.entry-points."aeroagentsim.engines"]
traffic_road_motion = "aeroagentsim.packs.traffic_accident.road_motion:build"
traffic_route_inputs = "aeroagentsim.packs.traffic_accident.route_inputs:build"
traffic_assessment = "aeroagentsim.packs.traffic_accident.traffic_assessment:build"
```

No B optional extra or new dependency is required: it uses the existing kernel,
PyYAML and standard library. Reuse `kinematic` and `environment`; do not register a
second air owner. C supplies its LangGraph entry point/extra separately. SUMO and
PX4 already have their own entry points/services; selecting their profile does
not install a simulator or certify native readiness. Test-only catalogue patching
in `tests/demos/conftest.py` is not production registration.

## A: compile the authored package

`scenario.yaml` embeds exactly the descriptor lists in `registry.overlay.yaml`;
its registry snapshot is independently frozen and preserves native source review
dispositions. All `aas:Traffic*` and `traffic.*` vocabulary is explicitly proposed
local vocabulary. Active kinematic fields are position/velocity/J; quaternion,
path length, native battery and legacy percentage descriptors are not silently
initialized on UAVs. Edge has no pose or fabricated winner.

Implement RUNTIME §5's additive hashed package hook before accepting this scenario.
The behaviour engine `config.packages` and scenario `behaviours` identify the same
content hash, not two executions. Compile once and retain the resolved IR. B's
small schema gate checks the published §5 surface and Q6 ASTs, not A's future
compiler or every authoring proposal below.

The following concrete selector/IR surfaces need agreement with A, whose code was
not started when B was assigned. They are explicit proposals, not assertions of
already-supported syntax:

- Relation tuple match with source/target roles and task-kind field dependencies;
  `predicate_roles` aliases map current task vs capture resource for eligibility.
- `bootstrap_relations.assertions` publishes the authored 146 initial edges after
  endpoint identity commits. This is input data; chain instances remain automatic.
- Stable-ID expressions for assignment assertions and capture request IDs;
  relation-assertion-ID variables refer to the actual old assignment version.
- Award selector `minimum_known_feasible_eta` joins configured candidate results or
  explicit failures/deadline, sorts known eligible records by ETA then EntityRef,
  and revalidates before closing the old patrol/asserting the unique new edge.
  A must not interpret a missing bid as refusal, an empty ETA as zero, or unknown
  eligibility as a permissible candidate. Selection is a read-only domain rule;
  the actual reservation/edge transaction belongs to behaviour.
- Typed stable entity-ref/choice expressions and event-subject bindings for the
  report/bid record templates; creation commits identity before the next reactive
  microstep publishes fields. `traffic.assessment.link_bid` validates the actual
  bid subject, creates/closes its owned assessment→bid edge, and returns an actual
  execution receipt before `bid.joined` is emitted. No Python plugin selects a winner.
- Road logical debounce uses 200 ms blocked / 300 ms clear state-entry timers.
  Only an actual `borrow corridor occupied` rejection permits up to ten new
  bypass attempts after explicit 300 ms timers; disconnected/missing routes or
  failed commands terminate with their real diagnostics. Compile the bounded
  `retry_policy` and receipt `result_matches` filters; do not auto-retry failed RPCs.
- Typed `$trigger.result.*` substitutions on actual execution receipts; event
  subject/correlation filters; retained goto/hold/storage/edge receipt requirements.
  Capture binds task/UAV/incident before D creates an actual image record.
- `feedback` and finite `sampled_contexts` compile to a real lagged partition and
  dependency/message graph. The explicit 66,666,667 ns return lag must reach kernel
  binding; a delay hidden in an action is insufficient. Pose and velocity aliases
  require the same generation; PX4 keeps their distinct acquisition clocks.

Initial relation cardinality has minimum zero and package guards/selection enforce
required participants and unique awards after legal creation waves. Do not activate
injected incidents until exactly two participant assertions are committed. The
injection point declaration names the operator stream; A/Q9 must declare that real
stream and preserve occurrence/admission/publication stamps, rather than an HTTP
handler setting `active`. `wind` injection must command the actual weather owner;
`pause-test` is a typed diagnostic event, not a replacement run-control API.

## C and D: use the same decisions and actual image contracts

`prompts/manifest.json` points to C's canonical `scenarios.agents.accident_graph`
symbols, with frozen text hashes and old runtime line citations. `.txt` files are
reviewable generated snapshots; the live plugin must import/reuse C's symbols.
Do not maintain a second graph in B. `fixtures/decisions.json` is an explicit
scripted provider input with all four stable C node IDs; it is an authored stub,
not an archival claim that those replies came from the historical model.

C's current compatibility graph accepts the old renderer DTO (X/Y/Z and
`battery_pct`) and emits the final `{winner_id,reason,action}` event. Bind that
surface only in a deliberately selected compatibility profile. Default B energy
is independently authored joules: do not invent a legacy percentage from J.
A/C need a scoped DTO/proposal bridge for the default rule-assisted profile,
including intermediate validated report/bid records and generation-aware refs.
`traffic.proposal.{report,bid,award}` declare the typed target surface. Resolve a
compatibility winner ID against its retained authorized observation, then recheck
current eligibility; do not infer subjects from arbitrary ID strings.

D must supply its capture engine/service configuration, lifecycle/field/relation
bindings and real storage + edge-acceptance events. No image record or PNG is
precreated in B. `capture_execute.completion_policy` requires actual goto and hold
success, complete sampled dwell and request/actor/source-cut-matched storage and
edge acceptance. The event schemas provide `source_cut`, digest and capture ref
slots. A/D must preserve the exact predicate/read cut in requests and validate
byte content/actor/generation/camera/asset digests before those terminal events.
A timer or transport acknowledgement cannot complete capture. B does not include
a metadata-only "successful PNG" fixture.

E uses the same scenario/package, actual native field IDs and recorded cuts. The
71 spatial actors plus the nonspatial edge camera imply 72 views for this default;
219 initial graph entities also include model inputs/tasks/assessment records.
F runs the full composed no-LLM, replay, image, live, browser and native gates after
these hooks arrive. B's domain traces do not substitute for those gates.

## Assets and profile replacement

The importer stores 60 route demands (20 distinct geometries), both incident
lanes, the smoothstep bypass and its connected `route.vehicle.005` continuation,
chosen packing offsets, actual actor parameters and source digests. ENU is local
`[X,-Z,Y]`, metres, yaw east-zero about up. No recovered Shanghai geodetic anchor
or vertical datum is asserted. Road zero altitude is the explicit flat model.

`inputs/city-manifest.json` retains placements/heights and per-file asset hashes.
No GLB, texture, road-render scene, vendor bundle, video or dependency cache is
copied. OSM-derived road database attribution is retained under ODbL; the original
OSM XML/native demand recipe is still unavailable. This is not certification that
the historical city pack meets all redistribution obligations. Its complete
reproduction and mesh/texture/model licences remain undecided, as in root ASSETS.

For independent licensed visuals use the existing `tools/sync_assets.py` pinned
CC0 car and BSD PX4 model pipeline and `tools/build_map.py` ODbL pipeline, retaining
inventory/licences/digests. A newly built geodetic map cannot be aligned to the old
local route frame without a supplied transform. Default presentation uses markers
and makes no city-mesh visual-parity claim. An owner-supplied historical mesh set
must be licensed, installed in the asset store and matched to this manifest before
use; a source pathname alone does not establish permission.

`profiles/sumo.yaml` and `profiles/px4.yaml` validate configuration/ownership and
existing adapter command names only. Their required real inputs and unverified
capabilities are explicit. SUMO publishes separate native XY fields until an
actual transform/occupancy converter exists; no kinematic stand-in is selected.
PX4 uses measured battery fields, not J, and separate pose/boundary-observation
clocks. Its public `goto` command maps to the service action `goto_location`.
Do not claim preemption, borrowed native lane, energy-estimator readiness or a real
Gazebo image bridge from these files. Replacement removes the old owner and creates
a new run/epoch; no hot field-writer transfer is proposed.

## Text for the shared demo document §11 (integrator-owned insertion)

B did not edit `docs/platform/demo-traffic-accident.md`: the task grants B exactly
its scenario/pack/importer/test paths. Append the following findings to §11 during
integration:

> Job B retains the local renderer→ENU transform `[X,-Z,Y]` and all exported route
> vertices, including declared wrap discontinuities. Default publication uses an explicitly rounded 66,666,667 ns interval (5 ns
> drift per 15 steps relative to exact 15 Hz), not a recovered wall tick clock.
> Default air motion reuses the
> existing rest-to-rest accelerated kinematic engine and 64-leg polygonal patrols,
> rather than the old direct constant-speed waypoint/circle mutation. The shared
> fleet speed limit is 8 m/s, acceleration 4 m/s², and initial velocity is explicitly
> at rest; Alpha's old 7 m/s routine parameter remains in frozen source inputs but
> this profile changes leg timing. The default 100,000 J capacity and 40 W/6 J·m⁻¹
> consumption are authored model assumptions, not calibrated from the old 91/87%
> battery display. Physical wind changes modeled energy only. The road owner adds
> swept occupancy checks and stable identity arbitration; staged participants obey
> the same physical clearance and may stop short instead of creating an impact.
> These are declared model revisions, not reproductions of historical completion
> times. Exact city/native demand reproduction remains unverified; 60 demand IDs
> reuse 20 geometries and do not export native edge mappings. The frozen registry
> preserves proposed/candidate source dispositions. Behaviour tuple/ID/bootstrap/
> award/correlation syntax is an explicit proposal for A's compiler; the complete
> scenario cannot execute until A/C/D integration. B's short domain trace tests,
> source comparison, lint and types do not certify full chain/capture/native/UI
> gates. The capture→incident relation stays scenario-local because the native
> observation-subject obligations were not admitted/verified in this snapshot.

## B validation record (2026-10-08)

All commands ran from `wt-b`, using its sources and the prescribed Python 3.11.
The final lint/type commands were:

```bash
PYTHONPATH=src /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  -m ruff check src/aeroagentsim/packs/traffic_accident \
  tools/demos/import_traffic_accident.py tests/demos
PYTHONPATH=src MYPYPATH=../aerokernel \
  /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  -m mypy --strict src/aeroagentsim/packs/traffic_accident \
  tools/demos/import_traffic_accident.py tests/demos
```

Results: ruff clean; mypy clean on 13 Python files.

```bash
PYTHONPATH=src \
AEROAGENTSIM_Q6_AST=/mnt/data2/weizhiwei/aeroagentsim/wt-q6/src/aeroagentsim/engines/predicate_ast.py \
  /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  -m pytest -q -p no:cacheprovider -m 'not docker' \
  --basetemp=/tmp/aas-q/b/domain-tests-final tests/demos
```

Result: **19 passed**. Subsequent road-bootstrap/initial-occupancy changes were
rechecked with the same interpreter/prefix using
`-m pytest -q -p no:cacheprovider -m 'not docker' tests/demos/traffic_accident/test_road.py`
(**3 passed**). Final declaration/debounce/retry/importer changes were rechecked
using the Q6 prefix above with
`-m pytest -q -p no:cacheprovider -m 'not docker' tests/demos/traffic_accident/test_scenario.py tests/demos/traffic_accident/test_policy.py`
(**12 passed**). No subsequent physical/assessment changes were made.

```bash
PYTHONPATH=src MYPYPATH=../aerokernel \
AEROAGENTSIM_AEROGRAPH_ROOT=/tmp/aas-q/b/ontology \
  /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  -m pytest -q -p no:cacheprovider -m 'not docker' \
  tests/platform tests/adapters tests/agents tests/packs tests/authoring
```

Result: **400 passed, 4 deselected**, including the existing live dispatch test
(which is not an accident graph/live demo gate). The scratch ontology contains
read-only copies of the actual `entity-directory/data`, `semantic-directory/data`
and `semantic-directory/src` inputs. Initial attempts without the environment,
then without the hash-pinned `expanded_runtime.js`, failed; supplying the actual
source bytes fixed those input errors without changing shared code or hashes.
No scripts/builds or mutations ran in AeroGraph. Scratch logs and measured
assessment deltas are under `/tmp/aas-q/b/`, outside git.

The two requested concurrent GLM one-shot sessions used
`workbuddy/glm-5.3-flash`, 131072 output budget and no effort setting, separate
scratch ownership and explicit project/source context. The default read-only dsh
profile could not write its launcher config, so the profile was copied into
`/tmp/aas-q/b/dsh`. The asset session completed (exit 0); its inventory/source hashes,
20 unique route geometries and licence findings were reviewed against actual files.
The predicate/schema session produced no deliverable before it was canceled
(exit 130); it is not counted as a completed result. B completed those declarations
through direct Q6-library validation instead of waiting for that session.

Not executed: A's compiler/full accident chain, C's accident live graph,
D's actual PNG/storage/recording gate, E's browser/UI gate, SUMO/PX4 native smoke,
Docker, or frontend checks (frontend was not touched). No videos/meshes/archives,
provider fallback, fake image success or native replacement telemetry is delivered.
