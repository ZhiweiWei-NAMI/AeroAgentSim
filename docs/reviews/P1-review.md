# Independent P1 review — commit 39eb028

**Verdict: the supplied research slice works, but P1 should not yet be accepted as a general-purpose platform.** There are reproducible failures in child-command workflows, per-entity engine ownership, temporal viewing, live completion and interrupted-run recovery. The kernel's authority checks prevent the illegal fleet writes; they do not make the scenario usable. No Critical finding was established. High findings below block dependable use beyond the shipped example.

Reviewed HEAD `39eb0280ecd1729083231e91bde15502df0a5ab9` against `docs/platform/PLAN.md:210` and the independent kernel's DESIGN §§2–9. Only this review is delivered in the repository; scripts, scenarios, runs and profiles are under `/tmp/aas-p1r/`. No source changes, builds, commits, branches or resets were made. The required CLI and source-compiling fixtures internally invoke the compiler’s Git provenance reads; after identifying that behavior, subsequent variants used pinned scratch snapshots. No direct Git command was issued in the protected source checkouts. The live kernel is a concurrent dependency, so results describe the checkout actually exercised, not a promised future kernel. Its source hashes are retained in `/tmp/aas-p1r/kernel-source-hashes.json`.

## Evidence and P1 gate

- The requested CLI slice completed: **22 simulated seconds / 18.030 wall seconds = 1.220× RTF**, peak RSS **112,396 KiB**. Artifacts: `/tmp/aas-p1r/runs/p1-slice-6b5f0c4ff409/` and `slice-cli.txt`.
- Platform suite: **40 passed, one dependency deprecation warning, 190.70 seconds**. Used the required kernel Python 3.11 interpreter, `PYTHONPATH=src:../aerokernel:<platform .venv site-packages>`, `MYPYPATH=../aerokernel`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and `--basetemp=/tmp/aas-p1r/pytest`. The extra read-only site-packages path supplies PyYAML/FastAPI absent from the kernel environment; nothing was installed. Log: `pytest.txt`.
- Existing tests provide meaningful calculated arrival/braking, independent acceptance, delayed record creation, real edges, sampled false→true/unknown behavior, byte determinism and engine-free prefix replay checks. The 1,000-entity test computes actual fields at two seconds; it is not a command-burst test.
- Authored and executed a genuine **position-free scenario** using native `oo:RadioSignalObservation`, `oo:Regulation`, and an explicitly authored concrete subtype of `oo:MetricObservation`. Scanner and restriction DES partitions exchange a typed event; the record engine captures scalar `he.radio.branch_a_power_dbm`, then creates one observation-subject edge. Final power −20 dBm, restriction active, live/replay cuts both 69. Files: `nonspatial.yaml`, `nonspatial.py`, `nonspatial-result.json`.
- Node probes execute transpiled copies of the actual HTTP feed and FeedStore sources, with all generated JS in scratch. Additional Python probes exercise the real kernel and FastAPI service. These are counterexamples, not repository test additions. No frontend build or browser screenshot test was run by this review.

The slice-specific P1 gate is substantially demonstrated. Its coverage does not establish generic workflow completion, scoped heterogeneous fleets, temporal inspector accuracy, real reconnect recovery or crash-prefix viewing. Those are required by the owner's platform boundary and by `PLAN.md:221`.

## High findings

### H1. A normal two-state child-command workflow loses its terminal receipt

**Location:** `src/aeroagentsim/engines/workflow.py:233`, `:260`, `:284`.

Command correlation retains `(machine_id, revision_at_submission)`. Every state transition increments that revision, and receipt matching requires equality with the current revision. A machine that submits a move in `submitted`, transitions to `executing` on `accepted`, and waits there for `succeeded` remains in `executing` forever. The real movement reached its target and the canonical command is `succeeded`.

Reproduced in `receipt-revision.json` / `probes.py`; `probes-result.json.receipt_revision` records position `[1,0,10]`, a succeeded command, and the stranded order state. This is ordinary command tracking across states, not a stale-timer problem. Multiple child commands also have no authored correlation slot or all/any completion policy: matching is “any child with this status in this revision.” Track command lifetimes independently of state-entry timer revisions, with explicit action identities and cancellation/cleanup policy.

### H2. Viewer projection changes the temporal meaning of facts and relations

**Location:** `src/aeroagentsim/services/projector.py:65`, `:78`, `:88`; consuming code `frontend/src/viewport/feed-store.ts:77` and `:81`.

Projection keeps a fact's `validFrom` but drops its validity end, acquisition stamp, actual availability and version/cause identity. FeedStore applies it as soon as its publication commit is reached, without checking `validFrom`. Validity notifications contain no projected state changes. Edge assertion/close projection similarly omits intervals and collapses cancellation into close; interval-scoped retractions become immediate deletions.

A real regulatory fact published at time zero with validity **[10 ms,15 ms)** is Absent in the kernel at 1 ms, active at 10 ms, then Absent at 15/20 ms. Feeding the actual projected WAL through the actual FeedStore displays **active at all four times**. Reproduction: `temporal_probe.py`, `temporal-feed.json`, `temporal-result.json`, `temporal-viewer-result.json`. This directly affects temporary restrictions, spectrum observations and late publications. Kernel replay remains correct; HTTP/viewer replay is a lossy projection. Preserve both knowledge and validity coordinates, and apply expiration/retraction/edge semantics at the selected physical time and journal cut.

### H3. Kinematic fleet construction ignores instance writer scopes

**Location:** `src/aeroagentsim/engines/kinematic.py:114` and `:272`; scenario authority is resolved in `src/aeroagentsim/platform/simulation.py:49`.

Each kinematic instance includes **every entity that is-a its configured type**, even when its writer/lifecycle bindings select only particular IDs. Two valid partitions, `motion-a` owning `uav-1` and `motion-b` owning `uav-2`, both construct both entities. Bootstrap succeeds; the first physical step faults with `FIELD_OWNER: not the resolved per-instance-field writer`.

Reproduced in `split-fleet.py` / `split-fleet-result.json`. Splitting one hierarchy across engines, fidelity models or regions is a basic heterogeneous-engine requirement. Filter by resolved instance-field ownership, validate consistent ownership of the model's slots, and support explicit selectors. Do not weaken the kernel check. Dynamic lifecycle is also incomplete: the fleet is initialized once and has no path to incorporate later-created entities or retire removed generations.

### H4. SSE can terminate before sending the final committed tail

**Location:** `src/aeroagentsim/services/app.py:163`–`:172`.

The tail reads an index snapshot first and terminal status second. The execution owner can publish its final index and `completed` status between those reads. The reader sees no records in the old snapshot, then emits `end` without rechecking the now-final index. The client accepts the end and never verifies a final cursor, so its gap checker cannot detect this missing suffix.

A controlled interleaving using the real endpoint returned only `event: end / completed` from cursor 1 while **69 complete non-header commits** were available immediately afterward (`sse_race.py`, `sse-race-result.json`). Pin a terminal journal index in the completion metadata, and drain through it before ending; an atomic completion snapshot or a final index recheck must close this race.

### H5. “Completed” is published before cleanup can fail

**Location:** `src/aeroagentsim/services/worker.py:44` and `:49`; `src/aeroagentsim/platform/simulation.py:140` and `:149`.

The worker marks a run completed, then closes engines in `finally`. A slow cleanup can fail afterward. A real test plugin that delays close by 0.5 seconds and raises produced **completed → SSE end(completed) → faulted(CLOSE_FAILED)**. The already-ended browser retains the earlier success. Reproduction: `cleanup_probe.py` / `cleanup-result.json`.

Close the execution owner and finalize its index before publishing the terminal outcome. Apply the same lifecycle discipline to RunSession's direct path; fault handling should retain the original failure context when cleanup also fails.

### H6. Service restart serves a stale index instead of the acknowledged WAL prefix

**Location:** `src/aeroagentsim/services/app.py:42`; `src/aeroagentsim/services/storage.py:20`, `:84`; `src/aeroagentsim/services/projector.py:203`.

The worker indexes only after host advances. A crash can leave complete WAL records beyond `index.json`. Startup changes status to interrupted but never rebuilds the index. The viewer permanently misses those real records, although kernel replay can read them. Header construction also excludes interrupted runs from its end-time condition.

Reproduced by truncating only a scratch copy's index to its first ten entries while leaving its 69 non-header WAL records intact and marking it running. Restart served **9 commits**, marked interrupted, and omitted header end (`restart_index_probe.py`, `restart-index-result.json`). Rebuild the complete-record index once during recovery, treating the WAL prefix as authority. Expose the retained endpoint without pretending execution resumes.

### H7. Transport exceptions bypass the advertised SSE reconnect loop

**Location:** `frontend/src/feeds/http.ts:113`, `:119`, `:134`–`:141`.

Only a clean premature EOF reaches the retry counter. A rejected `fetch()` occurs outside the reader block; a rejected `reader.read()` runs `finally`, then escapes the method. Neither retries. The real source in Node probes made two fetch calls and failed on either exception; a clean EOF made three calls and delivered the next commit (`frontend_probes.cjs`, `frontend-result.json`).

Handle bounded read-transport failures around both opening and consuming the stream, preserving the acknowledged cursor. Keep abort, malformed JSON, contract errors and journal gaps distinct from reconnectable transport failures. Existing duplicate suppression and explicit gap rejection are useful and should remain.

### H8. Record capture resolves subjects only from bootstrap entities

**Location:** `src/aeroagentsim/engines/records.py:65` and `:149`; analogous workflow reference handling at `src/aeroagentsim/engines/workflow.py:62` and `:188`.

Records caches `build.entities` and looks up the event's string ID there forever. A workflow created `new-receiver`, published real scalar power in its following writer wave, then emitted a capture trigger. The record engine faults with **KeyError: 'new-receiver'** (`record_dynamic_probe.py`, `record-dynamic-result.json`). A recreated existing ID would retain its old generation in this cache. Workflow likewise cannot generally refer to subjects created by another partition.

Resolve current committed typed identities at dispatch, or consume explicit typed references and lifecycle notifications. A valid new AeroGraph entity should not require rebuilding an engine's bootstrap dictionary.

### H9. A scenario ID escapes the service's configured output root

**Location:** `src/aeroagentsim/scenario/loader.py:164`; `src/aeroagentsim/services/app.py:110`–`:113`.

An ID is only checked as a nonempty string, then joined directly into a filesystem path. Posting the position-free scenario with ID `../escaped` returned 201 and created `/tmp/aas-p1r/escaped-<suffix>` outside the configured `/tmp/aas-p1r/api-id-runs/`. It disappeared from run listing, and the advertised ID cannot be served under the configured root (`probes-result.json.api_id_escape`). Absolute IDs can similarly choose an unrelated output location. This review tested only inside scratch.

Use an opaque service-generated directory ID independent of the scenario's semantic ID, and validate resolved containment before preparation. The existing `scenario_path` check does not cover output names or paths embedded in scenario registry configuration.

### H10. Arbitrary integer fact/payload values silently lose precision in the feed

**Location:** `src/aeroagentsim/services/projector.py:83`, `:102`; `frontend/src/feeds/http.ts:69`, `:130`, `:47`–`:60`.

Only Instant nanoseconds are converted to decimal strings. General kernel integers in values and messages pass through ordinary JSON parsing. The valid integer **9223372036854775815** becomes **9223372036854776000** in JavaScript and passes `validateCommit` (`frontend-result.json.integer_payload`). Threshold `from_ns`/`to_ns` are themselves integer payload fields (`engines/threshold.py:137`), so long-run event interval bounds also lose precision despite lossless outer timestamps.

Define a lossless schema-aware integer wire representation/parser for values, including refs' counters where necessary. Do not coerce integer schemas into strings inside the kernel or merely reject valid source data.

## Generality inventory and remaining Medium/Low findings

The platform execution wrapper and catalog contain **no hard-coded AeroGraph type IDs, UAV enumeration, Order state enum or prescribed motion→business engine sequence**. Registry loading accepts inherited fields; Kinematic uses `is_a`; projected type ancestors come from actual parent descriptors. The seven browsing directories are not incorrectly made inheritance. The successful nonspatial scenario proves that pose is not mandatory for every run.

The following assumptions still matter. Domain content in the two supplied YAML examples is distinguished from executable assumptions:

| Area and location | Fixed assumption and effect | Rank |
| --- | --- | --- |
| `platform/plugins.py:28`; `simulation.py:28` | Four named built-ins, with lazy entry-point extension; factories built in sorted engine-ID order. No mandatory timing mode. `EngineBuild` exposes all initial entities and a shared, initially incomplete partition dictionary, rather than resolved owned selectors. The latter contributes to H3 and makes plugin authors reconstruct authority themselves. | Medium API |
| `scenario/loader.py:333` | Every document must include `presentation`, but `[]` works. Spatial bindings require a 3-vector and frames enu/ned/wgs84; type/field applicability, orientation quaternion schema, origin, numeric visual parameters and transform compatibility are not fully checked here. Registry schemas determine facts, but most nested config maps admit unknown keys. | Medium validation; Low empty-list friction |
| `engines/common.py:13` | Local model policies stamp canonical acquisition and unbounded validity. Appropriate to explicitly modeled DES/kinematics; cannot be reused as an external simulator's native stamp/validity policy without adaptation. Core itself does not force these policies on installed plugins. | Low, document boundary |
| `engines/kinematic.py:18`, `:66`, `:92`, `:136`, `:294`, `:302` | ENU metres; exactly 3 position/velocity/target components; speed/acceleration/energy required together; move_to/hold/stop vocabulary; payload keys entity/target/optional machine; result position key. Arrival includes a workflow-routing machine field, falling back to entity ID. These are specialized algorithm contracts, not type IDs, but belong behind explicit model/slot/schema contracts rather than being the general engine interface. | Medium generality/API |
| `engines/kinematic.py:79`, `:109`, `:312` | `float()` accepts numeric strings and bool; `bool('false')` enables lifecycle. Probe with speed `'10'`, acceleration `true`, lifecycle `'false'` validated. Cancels always receive a negative decision directing users to hold/stop; interruption fails the old command, rather than expressing bounded cancellation. Cancel rejection is legal, but the PLAN's cancel capability is not implemented. | Medium validation/capability |
| `engines/workflow.py:249` | Payload member **machine** implicitly filters recipients. A spectrum event whose machine is a hardware identifier suppresses a restriction transition; removing that member broadcasts to all matching machines. `magic_machine_payload` reproduced this. There is no configured correlation expression, payload binding or event guard beyond the field predicate. No fixed logistics states occur in this file. | Medium generality |
| `engines/workflow.py:273`, `:287` | Predicate-only transitions evaluate only on a dirty notification for that field, not upon entering the state. A field already −20 dBm with a ≥−30 guard stranded the scanner in busy after its entry timer; the restriction remained inactive (`extra-probes-result.json.true_predicate_at_entry`). Reevaluate enabled guards at entry under explicit bounded transition semantics. | Medium correctness |
| `engines/workflow.py:89`, `:249`–`:288` | Multiple trigger categories are accepted but event→receipt→timer→predicate `elif` priority shadows later categories. An event+timer transition scheduled its timer, ignored it, and stayed idle (`multi_trigger_shadow`). Simultaneous competing transitions choose authored list order, independently of inbox order, and consumed unmatched events are not retained for the next state. State revisions correctly prevent stale timers firing new states, but timers are not withdrawn when leaving. Document priority/consumption semantics and reject ambiguous combinations. | Medium correctness |
| `engines/workflow.py:116`, `:165`–`:185` | Only command/emit actions receive meaningful constructor validation. Unsupported cancel, invalid set/create/remove references, fact schemas, targets or conflicting state-field writes can survive “validation” and fail later. A cancel action validated, then faulted at its timer (`probes-result.json`). There is no cancel action/child handle, relation action, dynamic machine template, or configurable all-child completion mechanism. | Medium validation/API |
| `engines/records.py:42`, `:56`, `:82`, `:119`, `:157` | Four mandatory header/result fields, a misleading position_field source name, clockRef/value seconds shape, $ref subject, obs/<subject>/<sequence> ID convention, mandatory relation and targets_per_source obligation direction. **The actual captured sample may be scalar**, as the nonspatial run proves. Source acquisition is retained; release is trigger delivery+delay, not necessarily acquisition+delay. Source/sample schema compatibility is not prevalidated. | Medium API; Low naming |
| `engines/threshold.py:25`–`:44`, `:83`, `:137` | Only gte of a subject **vector component**, mandatory one-element path, numeric parameter and fixed entity/from_ns/to_ns event payload. Scalar dBm cannot use the settled sampled evaluator without code changes: the nonspatial variant failed construction with “field and integer vector index.” String, enum, scalar, relation and boolean contracts need another plugin. Diagnostics still say position. | Medium generality |
| `engines/threshold.py:25`–`:53` | The configured native_reference path/hash and redundant index/value are not checked/used. Node AST tags are not fully validated. A nonexistent reference, bad hash and unsupported left-node op validated and ran (`unverified_native_ast_reference`). Reexecution can use the same claimed reference with different effective provenance. Pin/enforce the supported semantic profile; do not claim a general interpreter. | Medium provenance/validation |
| `services/projector.py:144`–`:153` | **Any** engine config with velocity_field or sample_field gets ENU metadata, regardless of plugin or descriptor. The successful scalar dBm record was exported with frame enu. This is spatial semantics leaking into a generic service. Derive frame from pinned field metadata and explicit bindings; absence of a frame is correct for a scalar. | Medium semantic accuracy |
| `services/projector.py:179`, `storage.py:45` | Viewer registryDigest is the compiled source digest, excluding local types/fields/messages and explicit runtime relation completion. Different runtime registries can advertise the same digest. Header reduces descriptors to valueType/unit/frame and omits field schemas, message/command schemas, relation descriptors and source review metadata. | Medium API |
| `frontend/src/pages/RunsPage.tsx:22`, `:55`, `:98`, `:104` | Default path is the P1 sample; auto-selection prefers spatial bindings; all runs display “ENU m,” including position-free runs; raw nanoseconds dominate the UI. The entity dropdown still permits nonspatial inspection. A seek's explicit commit cut is also discarded by the next ingest's `store.seek(clock.ns)` (`:49`) while the UI retains the older cut label. | Medium cut accuracy; Low presentation |
| `frontend/src/viewport/EntityInspector.tsx:12`–`:14` | Messages/receipts are associated by partition source/target equality to entity ID. The shipped move from operations to motion has subject uav-1 in its payload, so selecting that entity shows no related command/receipt. Commands need explicit typed subject association, not heuristics over arbitrary payload keys. Acquisition/availability/expiry/sample-frame provenance cannot be inspected through the current feed. | Medium UX |

The sample YAML's oo:UAV/oo:Order IDs, uav-* names, aircraft position field, order-state enum, arrival/business-acceptance/threshold-events topics, five subjects and two delivery waves are explicitly authored examples, not evidence that core infers those IDs. However, the reusable workflow's magic machine member, Kinematic's arrival routing member and the service's ENU inference make the sample's conventions spill across plugin boundaries. Generic topic routing is configurable; kernel partition-ID direct addressing is documented in `p1.md:240`, so topics colliding with partition IDs need an authoring diagnostic.

For the position-free use case, **basic authoring and execution work without changes**. Settled scalar entered evaluation does not; dynamic subject capture does not; generic time-limited regulatory state is misrepresented by the viewer; sample metadata is wrong. The scanner in the executable fixture follows an explicitly authored sequence and emits its event at 2 ms; it does not pretend the vector threshold plugin evaluated scalar power. The record correctly carries source acquisition 1 ms and availability 22 ms, giving 20 ms delay from the trigger and 21 ms from source acquisition.

## Correctness assessment beyond the failures

Kinematic integration uses an analytic triangular/trapezoidal trajectory and actual native elapsed time. Boundary reaction finishes integrated outcomes before applying new controls (`kinematic.py:278`). The existing off-grid hold test observes bounded braking and real terminal success; the earlier move fails when interrupted. Busy/from-motion moves and insufficient route energy are rejected. The coasting test checks real distance/energy, not just receipt counts. No successful receipt fabricated from viewer progress was found. Energy is a chosen point-mass model, not a native flight observation; idle consumption saturates at zero and moving uncommanded depletion faults explicitly.

Workflow stale timer revisions are useful. They should not also scope a command's entire lifetime (H1). Current ordering is deterministic but too implicit for competing business triggers, parallel children, cancellation and state-entry guards. There is no general workflow cancellation or child cancellation cleanup path; a state named canceled in scenario data provides no such behavior by itself.

Records preserve the committed fact's actual source stamp/payload and hold release until a kernel timer. Creation and field/edge publication occur in separate microsteps at release; prefix observers can therefore see an empty newly created record before its next writer wave. The documented P1 contract permits that staged publication, but consumers needing a complete record should subscribe to an explicit readiness/result signal rather than mere creation. Removal of a subject during the delay needs a declared historical/live-link policy; there is no producer repair or lifecycle cleanup policy in this plugin.

Threshold correctly records an unknown baseline, requires prior known false at an earlier physical sample time, and emits no first-true event. Native source/clock matching and settled SampleFrames are retained by kernel replay. Its narrow profile, unsupported scalar input, and unenforced reference metadata must remain explicit limitations.

Kernel replay/deterministic WAL tests passed. A fresh service process also projected an actual relation commit successfully (HTTP 200 at journal index 404), so this review does **not** infer a restart codec failure from the older decoder workaround documented in `p1.md:225`. The observed live dependency differs from that old issue description. Viewer temporal fidelity and stale-index recovery remain independent failures.

## Performance attribution and journal growth

The run has **5 moving entities, 10 initial orders and 10 released records**, not 25 motion integrators. The 1.66× figure in the task is not the measurement reproduced here; the checked-in final table itself reports 1.159× (`p1.md:214`). This run achieved 1.220×. These are local end-to-end measurements with buffered/flush WAL and no native simulators.

`profile_slice.py` profiled the full same slice to completion; wall time was **52.971 s with cProfile enabled**, so its RTF is not a throughput comparison. The profile contains 65.68 million calls and 51.302 seconds of attributed function time. Nested cumulative times below must not be added together:

| Cost | Calls | Cumulative seconds | Attribution |
| --- | ---: | ---: | --- |
| Kernel cause_refs | 3,808 | 36.346 | `../aerokernel/aerokernel/transactions.py:130` |
| item_at → encoded record lookup | 5,345 | 34.798 | `transactions.py:52` → `storage.py:156` |
| parse_json of records | 5,361 | 32.681 | `values.py:193` |
| Scenario compile | 1 | 3.930 | Platform AeroGraph integration, once at startup |
| Platform index maintenance | 24 | 0.490 | `services/storage.py:67` |
| Threshold reaction | 221 | 0.342 | Platform engine, including SDK/kernel reads |
| Kinematic step | 220 | 0.100 | Platform engine, including SDK proposals |

**About 68% of total attributed profile time is under item_at/record decoding.** Platform engine functions themselves consumed only 0.046 s of exclusive time. The measured bottleneck is kernel causal validation repeatedly parsing, normalizing and expanding a full encoded journal line to fetch individual referenced items. Current source calls the storage class RecordLog; `p1.md:235` uses the earlier RecordTable name. Preserve validation and immutable cuts while caching/indexing decoded items per relevant record/invocation; replacing real cause tracking with empty causes would alter research meaning.

Command-burst probes use the pinned source snapshot, N real initial entities, and N simultaneous real move_to commands; they stop after actual bootstrap acceptance, with **no physical step**:

| N | Mode | Result | Wall | WAL prefix bytes |
| ---: | --- | --- | ---: | ---: |
| 25 | Unprofiled | Bootstrap completed, 5 records | 1.447 s | 261,158 |
| 100 | Unprofiled | Bootstrap completed, 5 records | 21.012 s | 974,151 |
| 1,000 | Profiled, 30 s budget | **Interrupted; bootstrap incomplete, 4 records** | 30.016 s | 7,283,591 |

Four times the command count took roughly 14.5 times as long in the small complete probes. This supports the documented scaling problem; it is not an extrapolated 1,000-command throughput claim. In the interrupted profile, only **12 item_at calls already consumed 17.912 s**, because the records they decoded are very large; cause_refs consumed 18.274 s. Configuration YAML dumping consumed another 2.386 s, and Kinematic command reaction 1.440 s. Files: `slice.prof`, `slice-profile.txt`, `burst-1000.prof`, `burst-1000-profile.txt`, and the three burst result JSON files. Profile overhead and concurrent local activity limit absolute timing comparisons.

**High performance finding:** this causal lookup path blocks practical command-driven scaling. The initial-velocity scale workload bypasses it honestly (`p1.md:208`), but cannot establish command capacity. Fix the kernel bottleneck with its own correctness fixtures, then measure a completed burst separately from fleet publication.

For the completed slice: **9,399,623 bytes of WAL**, **1,646 records**, **3,425 fact writes**, **74,445 bytes of index**, **46,447-byte header**, and a largest line of **69,438 bytes**. That is **0.407 MiB per simulated second**, or approximately **1.43 GiB/hour** if the same publication rate persists; this is a rate extrapolation, not a measured one-hour run. Invocation records account for 3.598 MiB and sampled-frame transactions for 1.793 MiB. Full-memory v0.1 retention is intentional in DESIGN §13, but its practical memory/disk budget should be reported.

Index maintenance rewrites the entire offsets array at each advance (`storage.py:82`); every page/tail poll reparses it and scans from its beginning (`:89`–`:94`). That is a **Medium** long-run/platform cost, currently small in the slice. RunsPage also retains every commit and linearly scans them during redraw (`RunsPage.tsx:84`). Optimize these measured read/index paths without sampling away authoritative records or changing viewer-dependent simulation scheduling.

## Test strength and missing cases

The suite is useful, not broadly vacuous: same-byte WAL comparison, real unknown retraction, field/energy arithmetic, real independent acceptance and kernel prefix replay have concrete oracles. Important gaps explain the failures:

1. **Workflow cases absent:** accepted→executing→succeeded across states; two child commands with mixed results; competing same-time triggers; predicate true on state entry; cancel/cleanup; dynamic subject identity; separated ID-scoped fleets. All currently pass generic construction or are expressible enough to fail only during execution.
2. **Replay excludes dynamic record fields:** `test_platform.py:180` iterates only initial manifest entities and their initial fields. It checks real dynamic edges and exact records, but does not independently compare observation acquisition/sample/availability values after replay. The all-prefix compact run at `:191` only exercises bootstrap. Add small evolving traces with dynamic records and finite/future validity instead of repeatedly replaying the large fleet.
3. **The “differential” native test is incomplete:** `tests/platform/test_native_threshold.py:76` runs AeroGraph JS and asserts a few native outputs; it does not run the Python threshold implementation on the same frames and compare results. It also checks the reference hash in the test, while production ignores it. A shared paired fixture should cover known/unknown, source/clock mismatch, exact role identity and unsupported AST nodes.
4. **Real live tail is not exercised by the browser script:** `tests/platform/p1-e2e.mjs:12` waits for completed before opening the viewer, then switches the same completed run into live mode at `:38`. It verifies paged history and console health, not a growing stream, reconnect or terminal race. `test_service.py` also tails after completion. `frontend/src/feeds/http.test.ts:21` tests fragmented frames and duplicate delivery, but no reader/fetch exceptions, final-cursor agreement or actual reconnect loss.
5. **Name-only claims:** `http.test.ts:40` is called “fails malformed header units” but only supplies numeric timestamps; there is no malformed unit case. The nonspatial fixture at `:9` only checks presentation count and timestamp, not a position-free executable scenario or unit/frame accuracy.
6. **Negative construction cases are shallow:** the eight GLM-authored cases in `test_validation_glm.py:24` cover basic run/frame/format/nonfinite checks. Missing cases include bool/string model parameters, invalid action kinds/targets/facts, multiple trigger categories, presentation applicability/orientation/origin, wrong claimed native hash, local relation definitions absent from the compiled source, output-root traversal, stale index recovery and lossless large integer values. The loader only allows runtime relation completion for IDs in compiled relations (`loader.py:210`), unlike its additive local type/field/message facility.

## Independent GLM cross-checks

Two concurrent isolated DSH sessions used `workbuddy/glm-5.3-flash`, max output 131072, with no effort parameter, and separate scratch ownership for generality/services/feed versus engine correctness. Both reached the model and returned streamed candidate analysis; their broad reviews did not converge promptly and were interrupted. Their logs are retained as `glm-generality/reasoning.txt` and `glm-correctness/reasoning.txt`; they are not claimed as completed review deliverables. Two narrower follow-ups completed with exit code 0 and actual `findings.md` deliverables in both directories, independently confirming H7 and H1 respectively; their final output is in `narrow-out.txt`. Both returned proposals were checked against control flow. The suggestion to retire command correlation at the accepted transition was rejected because later receipts would become uncorrelated; JSON/contract failures also remain hard failures, not reconnect candidates. Primary reviewer source inspection and actual counterexamples determine every finding above.

## Prioritized fixes

1. **Correct workflow command correlation and state-entry semantics:** separate timer revision from action lifetime; add explicit child identities, guards, simultaneous-trigger policy and bounded cancellation/cleanup. Prove the small stranded-command counterexample first.
2. **Respect instance ownership and live identity:** expose resolved selectors to plugin authors; filter Kinematic by actual owned slots; resolve record/workflow subjects and generations from committed state. Verify two engines over one type and a dynamically created nonspatial subject.
3. **Make temporal feed viewing faithful:** retain acquisition/availability/valid intervals and identity/version information, including edge/retraction semantics; preserve large integers. Derive frame/unit from descriptors. Verify the real [10,15) ms restriction against kernel views.
4. **Finalize runs and streams coherently:** cleanup and final index precede terminal status; record/drain a final cursor; recover complete WAL prefixes once on restart; reconnect transport exceptions while surfacing semantic errors. Exercise an actually growing browser run.
5. **Close output-root escape and prevalidate authored contracts:** separate semantic IDs from storage names; validate nested action/model/presentation/AST contracts without coercion; reject ambiguity before worker creation. Keep real producer resolution as the repair path.
6. **Generalize the selected semantic adapters:** scalar/enum/relation selectors for settled sampled evaluation, schema-declared event correlation/results, configurable record slots/IDs/clocks, and explicit specialized spatial model contracts. Supply the tested position-free scenario as a regression example.
7. **Repair the measured kernel causal lookup cost, then characterize completed commands and journal budgets.** Keep causality/authority checks and real receipts. Improve platform indexing/read paths only after attribution; do not claim the initial-velocity scale run measures command capacity.
8. **Improve plugin/inspector usability and tests:** publish full runtime registry/command metadata with its true digest, typed entity-command links, readable units/time plus exact stamps, and compact negative/differential fixtures for the above failures.
