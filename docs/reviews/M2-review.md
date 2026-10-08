# K2-M2R: independent milestone 2 review

Reviewed on 2026-10-08 against normative `docs/DESIGN.md` and REPORT Round 2. **M2 needs changes before acceptance.** The common-boundary model is suitable for the three documented native backends, but the current implementation has reproducible watermark, output-bound, RPC projection and terminal-taint defects. Host integration also needs explicit paused-connection and backend translation policies. No critical defect was established in this review; passing tests do not certify the complete contract.

The only deliverable changed by this job is this file. Probes, isolated mutant packages, logs and a source snapshot are under `.kernel-agents/review-m2/`. Production code, repository tests and upstream repositories were not edited; transaction/storage performance was excluded. No upstream builds, Git operations, commits, branches or resets were run.

## Evidence and scope

Read the M2 implementation and its tests, DESIGN §§2–6, 8–10, 13, the M1 review, REPORT's Round 2, and the three read-only platform backend documents. This covers exact stop/certified hold, native/logical frontiers, input latching, early return, real-time ingress, temporal relations/obligations/removal, sampled frames/entered, discrete Jacobi reaction waves, and RPC transport/proxy/server. It does not run PX4/Gazebo, SUMO or ns-3 containers or certify their native physics.

All Python execution used `/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python` (3.11). Commands below run from the workspace root:

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD"
export TMPDIR="$PWD/.kernel-agents/review-m2/tmp"
export HYPOTHESIS_STORAGE_DIRECTORY="$PWD/.kernel-agents/review-m2/hypothesis"
.venv/bin/python -m pytest tests/kernel -q -p no:cacheprovider \
  --basetemp=.kernel-agents/review-m2/pytest-final-tmp
.venv/bin/python -m pytest tests/kernel/test_m2_*.py -q -p no:cacheprovider \
  --basetemp=.kernel-agents/review-m2/m2-final-tmp
.venv/bin/python .kernel-agents/review-m2/reproduce.py
.venv/bin/python .kernel-agents/review-m2/rpc_probes.py
.venv/bin/python .kernel-agents/review-m2/relation_oracle.py
```

- Final complete kernel suite: **405 passed, 2 skipped in 49.96 s**, `pytest-final.txt`. The skips are opt-in performance benchmarks. An earlier run had 397 passes; concurrent implementation work added tests during review. The final M2-only suite has **104 passed in 5.35 s**, `pytest-m2.txt`.
- `reproduce.jsonl` and `rpc_probes.jsonl` contain the reproduced findings below. The earlier probe draft incorrectly tried a string-valued `FieldDescriptor.schema`, which that descriptor does not support; it was removed. The retained named-schema probe uses the supported `{"schema_ref":"N"}` form.
- A separately authored interval-list oracle passed **7,290 live/replay queries over 81 prefixes and five traces**: replacement, future edge/obligation cancellation, late historical assertion, finite expiry and removal with authorized edge/obligation closure. It checks physical and microstep validity against publication-index visibility. Expected intervals are authored independently, not obtained from `relations()` or the sweep; only their actual publication coordinates come from the journal. `relation_oracle.json` records the result.
- Five isolated mutations each pass all 104 M2 tests; independent behavioral witnesses distinguish every mutation from the original. Details appear below. The first copy attempt overlapped concurrent source updates and produced common replay failures; those results are retained under `mutants/` and excluded from the survival claim. Successful runs are under `mutants-stable/`.
- `input-sha256.json` fingerprints inspected inputs; `source-snapshot/aerokernel/` preserves reviewed source. This is a live working-tree review, not a commit-identified review. All 32 package Python files parse with Python 3.10 grammar; execution used Python 3.11, so no Python 3.10 runtime claim is made.
- Three concurrent WorkBuddy GLM review sessions used `workbuddy/glm-5.3-flash`, output budget 131072, with no effort parameter, and explicit separate scratch ownership. All three hit their 540 s limits without final artifacts. Narrow follow-up mechanical checks were run concurrently; their artifacts were inspected rather than treating scheduling as completed review. The wire check completed and confirms the RPC observations, with the idle-duration caveat. The case checker reproduced the oracle and inspected mutant import/pass logs, then hit its 210 s limit without a final note; its malformed entered fixtures were excluded. Findings and expected behavior were independently verified by the primary reviewer. DSH's initial boot attempted a read-only profile rewrite and failed; subsequent launches used a workspace-local profile/settings tree. No successful external write resulted.

## Ranked findings

Critical threatens publication/causality/replay; high breaks a required execution or interoperability behavior; medium is a bounded defect or contract gap. There are no additional low-severity findings.

| ID | Severity | Finding | Executed probe |
|---|---|---|---|
| F1 | High | A newly safe earlier ingress boundary cannot interrupt the old watermark wait | `incremental_watermark` |
| F2 | High | Relation output bypasses the horizon guard during a certified hold | `hold_relation_output` |
| F3 | High | RPC projections omit supported named schemas and referenced target types | `projection_named`, `projection_ref_type` |
| F4 | High | Completing the original call can erase a concurrent-call taint | `concurrent_call_taint` |
| F5 | Medium | Server requires consecutive request IDs instead of increasing IDs | `increasing_ids` |
| G1 | Medium, adapter gap | Paused engines are destroyed after an operation-derived idle timeout | `paused_idle` |

### F1 — an incremental watermark does not release the earlier safe boundary

**Locations:** `aerokernel/coordinator.py:670`, `:678`, `:926`. DESIGN §2 requires selection from pending ingress, monotonic closed-prefix watermarks and finite waits without losing available progress.

The probe starts `run_until(20)` with watermark zero and a 150 ms wait budget. While it waits for 20, another thread reserves a real stamped command at 3 and advances the watermark only to 3. This is sufficient to advance and dispatch at 3, then wait for a later watermark. Instead, `_wait_watermark(20)` wakes and continues checking its original target. The outer cut-change/recomputation check is reached only after that old wait completes.

**Observed:** watermark **3**, seal **0**, no physical calls, no delivery, action still pending, then **`WATERMARK_TIMEOUT`** at approximately 0.15 s. Replay reproduces the faulted records. The defect is failure to process 3; the eventual inability to finish 20 without more closure is expected. A source waiting for the response at 3 before producing later inputs can therefore deadlock.

**Repair:** wake boundary selection when recorded ingress/cut changes invalidate the selected target; recompute the minimum safe boundary before continuing the wait. Preserve the finite no-progress timeout instead of resetting it indefinitely on notifications. Add a test that advances the watermark to the new command boundary, observes that command's dispatch, and only then advances to the final limit. The current wake-up test advances directly to 20 and misses this case.

### F2 — relation publication is omitted from output-bound enforcement

**Locations:** `aerokernel/coordinator.py:510`, `:514`; relation publication at `aerokernel/relations.py:302`. DESIGN §2.1 applies the uninvalidated output bound to every output, and explicitly prohibits holds from manufacturing measurements/native progress.

The probe declares a lockstep partition with native grid 20, certified holds, and both wakeup/output lower bound 20. Its test adapter acknowledges the legitimate logical hold to 3, with native frontier still zero, but returns an `AssertEdge` at that boundary. This intentionally violates its promise; the kernel should fault before publishing it.

**Observed:** an edge is published and readable at **3**, logical frontier **3**, native frontier **0**, despite output bound **20**. Replay accepts the trace. The guard checks only `FactWrite`, `RetractFact`, `Emit`, `Receipt` and `Feedback` by class name; relation/obligation publications are outside it.

**Repair:** classify observable proposal effects centrally and apply the guard to all relevant publication kinds, including edge/obligation versions. Keep non-output scheduling/frontier acknowledgments legal. Test assertions, closure/cancellation and obligations under both finite future bounds and infinity, with a hold that actually retains the native frontier. This is a kernel validation hole demonstrated with a faulty adapter, not evidence that a conforming adapter spontaneously fabricates edges.

### F3 — legal registry contracts fail RPC projection reconstruction

**Locations:** `aerokernel/rpc.py:197`, `:220`, `:230`, `:249`; compare normalized schema rules at `aerokernel/registry.py:220`, `:226`, `:287`. DESIGN §§6–7 requires declared portable schemas and scoped views to remain usable through RPC.

Two supported local registries reproduce distinct failures:

| Local contract | Local result | Projection/reconstruction result |
|---|---|---|
| Integer field uses `{"schema_ref":"N"}` with `N={"type":"integer"}` | Bootstrap and read return **7** | **`SCHEMA_UNKNOWN`** |
| Active unset field uses `{"type":"ref","target_type":"Other"}`; `Other` is registered but has no instances | Bootstrap and read return tagged **ABSENT** | **`TYPE_UNKNOWN`** |

The schema walker looks for `$ref`, while this normalized registry uses `schema_ref`. The type closure includes field declaring types, visible identities and inheritance, but omits schema `target_type` references. The latter must remain valid even when no value/target instance exists. These probes exercise valid local views, not malformed incoming wire data. `_projection` is also the mandatory path used by `RemoteEngine.reset/advance/react`, so a valid model can fail before its remote callback is sent.

**Repair:** compute the transitive closure using the registry's actual normalized schema grammar, across field/message/result/feedback schemas and their named dependencies, and include referenced types plus ancestors. Preserve scoped state/history access; exporting the whole registry/state is unnecessary. Add local-versus-remote cases for nested named schemas, unions/arrays, distinct reference target types and unset fields. Existing RPC fixtures mostly use inline schemas and same-type references.

### F4 — terminal taint can be overwritten by the original in-flight call

**Locations:** `aerokernel/rpc_transport.py:184`, `:186`, `:244`. DESIGN §6 makes TAINTED a cleanup-only terminal state.

The socket probe suspends the first hello after the server receives it. A second call correctly raises **`RPC_IN_FLIGHT`** and sets taint. The server then returns a valid response to the original hello.

**Observed:** the first call returns `{}`, and the connection's final state is **`hello`**, allowing the reset lifecycle again. The success-state assignment overwrites the taint established by the rejected concurrent caller. A blocked reset has the analogous overwrite to READY.

**Repair:** make taint monotonic across concurrent access and prevent a successful completion from restoring a usable lifecycle after taint. Cover a concurrent rejected call during hello and reset with explicit synchronization, plus attempted subsequent use. Kernel-issued engine calls are serial; this finding concerns the public transport's promised behavior when its concurrency guard detects misuse, rather than claiming ordinary serial kernel execution overlaps calls.

### F5 — increasing IDs are narrowed to an undocumented consecutive sequence

**Locations:** `aerokernel/rpc_transport.py:264`, `:272`, `:308`. DESIGN §6 specifies connection-local **increasing integer request IDs**, not a mandatory contiguous sequence. All three backend documents also describe increasing IDs.

A valid hello with ID 1 followed by reset ID 7 fails **`RPC_IDENTITY`** before reset is called; only the hello response is produced. The server compares to an incremented expected ID rather than validating a positive ID greater than the previous one. `RPCConnection` itself always generates consecutive IDs, so its happy-path tests hide this interoperability restriction. The existing test rejecting an initial ID 2 also encodes a stricter starting-ID assumption than DESIGN states.

**Repair:** validate lossless positive integers against the previous accepted ID, then echo the actual ID. Test skipped IDs and a large jump alongside duplicate, decreasing, bool and float IDs. Alternatively, explicitly adopt a consecutive-ID protocol requirement in DESIGN and its external-facing contract; it should not remain an implementation-only restriction.

### G1 — an idle decision pause inherits operation execution deadlines

**Locations:** `aerokernel/rpc_transport.py:267`; `aerokernel/rpc.py:686`. Each operation defaults to 5 s, and the server uses `max(policy.values())` as the deadline for waiting for the next request. Timeout leaves `serve_requests`; `serve_engine` then closes the engine.

The probe shortens every operation budget to 150 ms, completes hello/reset/settlement, and sends no operation during the pause. **The server reports `RPC_TIMEOUT` and closes the engine; the next grant gets `RPC_EOF` and taints the client.** The same path uses 5 s under the defaults. There was no executing native operation to time out.

This is a **hostability gap**, not a demonstrated violation of an explicit v0.1 idle-duration requirement: DESIGN does not pin such a duration. It is incompatible with the native backend documents' 3600 s idle allowance and paused agent decisions unless the host knowingly changes the policy. Raising reset/advance/close budgets to 3600 merely to permit inactivity also weakens real operation bounds.

**Repair:** declare a separate finite idle/request-arrival budget and keep execution/drain/cleanup deadlines independent. Pin the host policy, and test a silent pause longer than an operation deadline followed by a valid horizon/advance. Do not use periodic stateful calls as a keepalive substitute or infer simulated progress during the pause.

## Test strength and five uncaught mutants

The existing M2 tests have useful independent checks. The directional sweep property uses an authored brute-force oracle with both directional maxima, activated minima, future overlaps, pair identity and finite/open intervals. The lockstep simulator fixture independently records native calls and actual application: 3 ms input is applied after 0→20 ms integration using the pinned native-start cut; unexpected early return publishes nothing; explicit buffering retains 3 ms occurrence with 20 ms availability. Exact splitting and nonzero grid origins are exercised.

Sample tests verify whole-cone coalescing, parent/child publication at later microsteps, a forbidden transitive sampled cycle and a positive-lag break. Entered tests reject first-true emission, skipping newer unresolved frames, context/source/clock mismatch, future/duplicate history, bool coercion and current inapplicability, and check prior/current frame causes. The SCC fixture verifies eight Jacobi reactions share each wave's immutable base, with no physical reintegration; an equal-payload event cycle still exhausts the microstep budget. This is discrete queue iteration as DESIGN requires, not a numerical fixpoint certification.

RPC tests use real socketpairs and binary subprocess stdio; cover fragmentation, exact large integers, duplicate keys, malformed result/error envelopes, versions/IDs, EOF, timeout and oversize; and show native work can occur while all returned facts remain unpublished after a wire fault. Local/remote toy state and actions are compared. These are substantial tests, but many replay checks reuse the live validators and identical inline registry shapes. They do not independently establish every temporal/projection invariant.

The following **executed**, deliberately faulty variants each passed **104/104 M2 tests**. `mutations-stable.jsonl`, each mutant's `pytest.txt`, and `mutant-witnesses.jsonl` retain package paths, outcomes and counterexamples. They are survivors of this M2 suite, not a claim about every possible external test.

| Mutant | Isolated edit | Original versus mutant witness | Targeted missing test |
|---|---|---|---|
| M1 `no_pacing` | Return immediately from `_pace_to` | A 50 ms paced grant waits about **51.5 ms** versus **1.0 ms** | Verify waiting with a controlled monotonic clock/condition; the current pacing test compares only record order |
| M2 `prior_inapplicable_false` | Remove the prior frame applicability check | Prior known-false/inapplicable → current known-true/applicable: **unresolved** versus **known true** | Assert no entered event from an inapplicable baseline; retain a valid applicable false→true control |
| M3 `no_relation_acquisition_check` | Omit `candidate.stamp(op.acquired)` in edge assertion | Acquisition at 1 published at 0: **`ACQUISITION_FUTURE`, no edges** versus **one committed edge** | Relation acquisition mapping/future-time tests, including bootstrap |
| M4 `no_server_contract_check` | Disable server contract equality check after reset | Changed contract on advance: **`RPC_REMOTE`, no native call** versus **successful native advance to 1** | Fault a changed binding contract on an actual request, not only a replay header |
| M5 `cleanup_overlaps_call` | Remove the engine lock from server cleanup | Reset times out while still active: cleanup **waits** versus **calls close during reset** | A blocked native call plus timeout/disconnect, proving cleanup cannot overlap it |

Witnesses are executable individually; for example:

```sh
.venv/bin/python .kernel-agents/review-m2/mutant_witnesses.py prior_inapplicable_false
PYTHONPATH="$PWD/.kernel-agents/review-m2/mutants-stable/prior_inapplicable_false:$PWD" \
  .venv/bin/python .kernel-agents/review-m2/mutant_witnesses.py prior_inapplicable_false
```

The original correctly rejects/handles these witness cases; the table describes test weaknesses, not five additional production defects. Root-level witnesses use proper `Evaluation.to_data()` frame results. A follow-up assistant's malformed entered fixture is not evidence of profile behavior and is excluded.

## PX4, SUMO and ns-3 host-adapter assessment

**The semantic foundation is sufficient for a correctly declared host wrapper; the shipped implementation and backend-specific transports are not a complete adapter contract out of the box.** DESIGN already supplies exact common grants, certified logical holds, native-start/read cuts, reactive latching, delayed availability, typed actions and uncertain-call faulting. None of the three documented services implements `aerokernel.rpc` directly. Repair F1–F5 and make G1 explicit before claiming host conformance. No native backend defect is inferred from these kernel probes.

Sources read: [PX4 backend](/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/docs/platform/px4-backend.md:23), [SUMO backend](/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/docs/platform/sumo-backend.md:50), [ns-3 backend](/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/docs/platform/ns3-backend.md:134). Native measurements below are those documents' reports, not executions performed by this reviewer.

| Backend | Usable kernel timing model | Required host translation and unresolved integration gaps |
|---|---|---|
| `aeroagentsim.px4/v1` | Native-grid lockstep with certified off-grid holds; documented physics quantum is 4 ms. Choose/pin communication barriers and latch inputs after positive integration. Equal-time backend advances hold and leave commands queued. | Map elapsed kernel time to the warmup/source Gazebo clock with a pinned mapping. Validate actual paused WorldStatistics and exact source-time poses before acknowledging a grant. Translate queued commands and subsequent native action updates into kernel receipts; ACK alone is not success. Cached MAVSDK fields lack exact source simulation timestamps: retain their ages/unmapped source-time status and distinguish them from synchronous pose truth. Contacts may arrive late; an empty list is not closed-prefix absence. No host adapter or general cancellation/lifecycle integration is verified. |
| `aeroagentsim.sumo/v1` | Native-grid lockstep with configured step/origin, certified holds and next-positive-boundary command application. Larger grants gather events at every internal tick and expose final-boundary state. | Buffer discovered departure/insertion/arrival/removal facts until kernel lifecycle controller waves create/remove the corresponding generations; physical-phase Create/Remove is forbidden. Distinguish pending insertion, active entities, teleport/collision and explicit removal. Preserve per-tick occurrence and batch availability. Wire actual removal/readback into `LifecycleReady` before kernel Remove. Preserve SUMO network frame and optional georeferencing; do not invent ENU coordinates. Scheduled restrictions need declared safe input/application boundaries and bound command IDs. |
| `aeroagentsim.ns3/v1` | Exact stop with 1 ns quantum, no early return, and a paused native world; a host can acknowledge logical holds without calling same-target advance, which the backend does not support. | Stage sends at the current native boundary and apply committed timestamped mobility under an explicit horizon/lag policy. Preserve received/sent/drop occurrence separately from `available_sim_ns`. An event tied with the stop may be reported in the next batch; its original occurrence cannot retroactively influence another engine. Translate accepted sends and actual deliveries/expiry drops into suitable typed outcomes; a command ACK is never delivery. Explicitly bind node inventory/model limits, opaque payload references and optional measured RSSI/SNR; omitted measurements must not become zero or persistent fabricated observations. |

Shared gaps requiring host-owned choices and tests:

1. **Two transport layers and identities.** Translate backend `hello/reset/command/advance/close` envelopes into the kernel Engine interface; add `horizon/react`, partition/invocation tokens, all cuts/frontiers and manifest/registry pins. Backend major/minor/profile, image/runtime/configuration/seed and capability provenance should be pinned alongside the wrapper version. Maintain a deterministic checked mapping between kernel command IDs/entity generations and backend IDs; backend ID length/count limits are narrower than arbitrary kernel identifiers. A host wrapper can be in-process; if remote, its outer kernel RPC connection is separate from the backend connection.
2. **Boundary capability and availability.** The timing declaration must describe the actual legal quantum, origin, controllable stop and hold behavior. PX4/SUMO alignment does not justify promising arbitrary exact stops. Decide communication cadence explicitly; `run_until` boundaries can change coupling/subdivision. Native event times alone cannot establish an output lower bound or watermark. In particular PX4 contacts/cached telemetry need a defensible collection/availability policy, and ns-3 tied callbacks need delayed availability. Conformance fixtures should combine at least two irreversible peers and a third off-grid command, not only an isolated simulator.
3. **Timeout nesting, paused decisions and cleanup.** The kernel RPC defaults are 5 s per operation; backend budgets are hello 10, reset 180, advance/command 30, close 20 s, plus drain/native/idle budgets. The reported PX4 reset alone takes about 13 s. Configure a defensible outer budget for the complete wrapper operation, including serialization and inner exchanges, and a separate 3600 s decision-pause policy where required. A generic timed-out daemon callback cannot be killed; `serve_engine` serializes cleanup behind it but has no native abort hook. Hosts must supply cancellable/bounded native work and process ownership/termination, and surface cleanup failure without a retry or success fabrication.
4. **Actions and lifecycle.** Map actual native accepted/executing/terminal updates into the legal kernel status table, with original dispatch and current receipt-head causes. Advertise `cancel_support=False` until an actual backend cleanup/cancel policy exists; a PX4 hold action is not automatically cancellation. Native disappearance does not grant edge cleanup authority. Bind controllers, native cleanup participants, relation writers and any atomic cohorts before removal, and keep pending commands explicitly failed/rejected where appropriate.
5. **Schemas, source clocks and missing observations.** Declare field/result/feedback schemas, units/frames, actual acquisition and validity, and type/source scopes. Apply F3's complete normalized schema closure through RPC. A host collection stamp can describe actual acquisition by the adapter; it cannot manufacture a missing native timestamp. Missing optional measurements require appropriate absence/retraction/explicit tagged schema, never false/zero/default substitution. Kernel replay preserves published history, while native reexecution repeatability remains backend-specific: the PX4 document explicitly does not claim bitwise trajectory equality.

The most useful next acceptance fixture is a small host-shaped two-backend run: native-grid holds and exact stops, a 3 ms input between native ticks, one queued command with observed terminal status, one native entity lifecycle event, one late batch event, and a timeout after real native mutation. Check recorded publication/application/native-start cuts and every replay prefix, and include a long idle decision pause. This fixture can use instrumented backend-protocol stand-ins first; real native conformance still requires the separately owned host adapters and containers.
