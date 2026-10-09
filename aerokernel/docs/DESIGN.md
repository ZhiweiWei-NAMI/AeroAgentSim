# aerokernel design specification, v0.3 (provenance levels)

This document is normative. **MUST** defines a conformance requirement; **SHOULD** permits a documented alternative. The target is an independent Python >=3.10 package, pure standard library at runtime. AeroAgentSim is the platform consuming it; AeroBench is an adapter migration source. The v0.1 scope in section 13 remains; section 14 takes precedence for local ingress, section 15 for full audit encoding, and section 16 for provenance levels.

## 1. Boundary and source authority

The kernel MUST own identity/lifecycle, normalized registry contracts, per-instance-field single-writer authority, atomic commits, bitemporal history, temporal relation integrity, conservative coordination, typed delivery, action status, deterministic ordering, journaling and replay. Entities can be any registered type, including passive records and nonspatial objects. There is no universal pose, vehicle kind, directory taxonomy or fixed domain stage.

Physics, ontology content, predicate languages, unit/frame conversion, UI, evidence/sealing and process/container deployment remain outside the kernel. Spatial indexes and viewers consume committed state. Capability profiles do not imply engine implementations or command support. Adapters MUST publish actual results; missing data, exceptions or transport completion MUST NOT synthesize zero, false or success.

The project direction is [REPORT Round 2](/tmp/aas-survey/REPORT.md). Hostability is checked against AeroGraph's [state schema](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/state.schema.json), [relation schema](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relation.schema.json), [handoff](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/HANDOFF.md) and [expanded interpreter](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/src/expanded_runtime.js:508). AeroBench's [provider contract](/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim/aero-bench/aero_bench/providers/contracts.py) motivates adapter lifecycle, without importing domain stages or evidence requirements. [FMI 3.0.2 co-simulation](https://fmi-standard.org/docs/3.0.2/#early-return) permits early returns; the common-boundary restriction below is this kernel's v0.1 design choice. Attribute-level authority and separate data/behavior follow HLA/ECS principles; the package implements neither an RTI nor an FMI/DEVS runtime.

## 2. Time and conservative coordination

`Instant(ns, microstep)` has nonnegative, unbounded integer components and lexicographic order; bool is not an integer. Nanoseconds measure canonical elapsed time; microsteps order reactive work without advancing physical time. A new physical time starts at microstep zero. All validity intervals are half-open `[start,end)` over Instants, with `None` meaning an explicitly unbounded end. Missing boundaries are invalid; an empty interval is invalid.

`Cut(index, instant)` identifies an immutable complete journal prefix in this run. Only the exact index/Instant pairs issued by the kernel or reconstructed from valid records are legal; forged/mismatched pairs fail. The header is index zero with initial Instant `(0,0)`; subsequent record Instants are nondecreasing. Reads are bounded by index and instant, so equal-time records remain distinct. A wave's `transaction_base_cut` is the global immutable prefix before invocation intents. Its per-invocation `read_cut` can be older (native latching/lag); `StateView.cut` caps knowledge at that read cut. Later intent/dispatch references are authorized through the private token/inbox, not through future state reads. Historical cuts resolve lifecycle/relations as known then, not eventual tombstones.

`Stamp(clock_id, numerator, denominator, mapping_id)` retains rational source time. A pinned mapping computes nanoseconds as `offset_ns + (p/q)*(numerator/denominator)`, with positive denominators/scale, using integer/Fraction arithmetic. The mapping declares `exact`, `floor`, `ceil`, or `nearest_ties_even`; `exact` rejects nonintegral results. The original stamp, mapping and mapped result are recorded. Acquisition may predate the run origin, but must not map later than publication's physical time. Equal mapped times do not make native clocks identical. Mapping changes during an epoch are **v0.2+**.

### 2.1 Common boundary contract

v0.1 uses a central barrier, not distributed conditional per-stream grants. At each settled physical time `C`, every adapter has a logical frontier through `C`. An adapter's native simulator frontier may be earlier, but that distinction MUST be declared and recorded. A wrapper can acknowledge a hold without contacting or advancing its external simulator only when its manifest promises no newly available output before its next native boundary. Holding changes neither measurements nor native time.

Each partition reports a `Horizon` tied to the current input cut:

- `reached`: the last acknowledged logical Instant;
- `native_reached_ns`: the confirmed native integration frontier, or the logical physical time for a DES/reactive partition;
- `next_wakeup_ns`: earliest internal/native activation, or `None` for infinity;
- `output_lb_ns`: no as-yet-unreturned output can become available strictly before this bound, or `None` for infinity;
- `grant_limit_ns`: an exact advance or certified hold is possible for every requested time between the logical frontier and this limit; `None` means unbounded;
- optional typed external wait, with readiness condition and timeout policy.

Equality at an output bound is allowed: a bound of `T` means output may be published at `T`, so its partition MUST be serviced at `T`. Zero lookahead is legal. When `C` is sealed, finite wakeups/output bounds must be strictly greater than `C`; equal-time work must have been settled first. A finite limit equal to `C`, unchanged bound at a serviced boundary, or immediate activation with no work/progress is a deadlock unless an explicit external wait applies. `None` is infinity only in these named horizon fields, never a missing value converted to zero.

The next global boundary `T>C` is the minimum of all finite wakeups, output bounds, grant limits, pending ingress, delivery eligibility, validity/evaluation deadlines and the requested run limit. Every returned output must obey all uninvalidated bounds; a hold/run limit cannot manufacture a sample before its promised output bound. An idle DES with no event advertises infinity and acknowledges a hold; it does not prevent progress to someone else's event or to the run limit. A downstream infinity promise cannot override an earlier activation anywhere else in the run. Horizons are refreshed after each input/commit; same-time input invalidates future conditional bounds, never the already closed interval before that input.

Timing policies are explicit:

| Mode | v0.1 adapter behavior |
|---|---|
| `des` | Exact logical advance, internal event output at the boundary, then external-input reaction. No next event is infinity. |
| `fixed_step` | Positive step and origin; native integration only on its grid. Intermediate boundaries are certified holds. Inputs/dirty dependencies off-grid are either supported by exact splitting or latched to the next native boundary. |
| `lockstep` | Confirm actual simulator time. Expose exact controllable stop points or a native-grid wrapper with certified holds/latching. Unsolicited early return is not a valid common-boundary result. |
| `real_time` | An ingress adapter supplies a closed-prefix watermark: no more inputs at or before that physical time will arrive. The coordinator waits before closing beyond it; timestamps alone are not a watermark. |

Arbitrary early-return negotiation across irreversible peers is **v0.2+**. Serial calls alone do not solve it: after P reaches 20 ms, Q can return at 5 ms and generate input for P. An adapter must disable early return, determine a safe exact boundary before the grant, or explicitly buffer occurrence at 5 ms for availability at its agreed return boundary. That last policy models delay; it cannot claim immediate 5 ms influence. Unexpected earlier/later native return faults the run; there is no rollback.

### 2.2 Advance, reaction and settlement

The coordinator executes these phases in order:

1. **Physical phase `(T,0)`:** authorize and advance each partition logically to exactly `T`, in stable ID order, with no inbox containing inputs at `T`. Internal DES output and integration ending at `T` happen before external inputs at `T`. Results share one transaction base but use each partition's restricted native/lag read cut; they are validated/published as one wave. A physical call cannot create/remove entities; it can schedule its lifecycle controller for reaction.
2. **Reactive waves `(T,m)`, `m>=1`:** choose ready reactors from due eligibility, dirty/timer work and explicit activations. If any lifecycle cohort is ready, invoke only those controller cohorts, commit that separate wave, then recompute readiness before ordinary writers. Otherwise invoke the ordinary ready set. All participants use the same transaction base before their intent record, with per-invocation read restrictions. A reactor is called at most once per wave with its sorted inbox and cannot integrate physical time again. Outputs/dirty notifications activate recipients no earlier than `m+1`. Cohorts are disjoint (overlaps must be merged at bind); activating any member invokes the entire cohort, with one combined commit. Cohort members cannot observe one another's uncommitted proposals.
3. **Sample phase:** after ordinary upstream work settles, assign the next unused microstep at `T` to each eligible sampled evaluator level, once per context and physical time (section 8). Frame/batch publication uses that assigned Instant. Its downstream inputs run only in later microsteps; settle them before the next sampled level. Phase priority never places a causal predecessor and consumer in the same microstep.
4. **Seal:** record a seal only when no deliveries, activations, dirty work or unsampled ready contexts remain at `T`, and every refreshed unshifted output bound/wakeup is strictly beyond `T`. `run_until(T)` returns this settled cut. Holding to a run limit does not assert that actions have completed.

For an engine actually integrated at `C`, physical input is the declared latched snapshot after settlement at `C`. A held fixed-step wrapper integrating native `q_prev→q` MUST use the cut pinned after settlement at `q_prev`, not a newer global cut. Record separate transaction base, read cut, native interval/input cut and application boundary. Inputs arriving in `(q_prev,q]` are exposed/applied only after reaching `q`, never over the just-finished interval. Latching does not change `Fact.valid`: at `q`, an expired version may generate a notification but a current-validity read returns `ABSENT`. A private sampled input/held model is explicit engine behavior, not a store extrapolation. If an input must remain valid throughout integration, check that coverage before the native call; otherwise split exactly, use an explicitly declared model policy, or fail binding to a nonsplittable stream. No expired/missing input becomes a default value.

Field, relation, lifecycle and message dependencies are all declared. Zero lag reads preceding committed microsteps at the same physical time. Positive lag `L` restricts both knowledge and validity reads to physical time at most `t-L`; before the origin the result is genuinely absent. For a physical interval, the native interval-start cut additionally restricts reads. Future configuration validity can be inspected by explicit historical/planning APIs outside an engine's evolution view; it does not grant future knowledge to an engine.

Zero-lag reactive cycles are allowed only when every member supports reaction without physical reintegration. They implement discrete transitions over successive committed views, not an algebraic/numerical fixpoint solver (**v0.2+**). Quiescence means no queued messages/dirty work/activations; equal values alone do not prove it. Only explicitly value-only dependencies suppress equal-value updates. Version/time-sensitive dependencies remain dirty even for equal values. `max_microsteps` (default 1024) bounds all same-time work, including event storms and controller cycles; exhaustion raises `MicrostepLimitExceeded` with the work/cycle trace. Unsupported cycles fail binding with a path. No safe progress raises `SynchronizationDeadlock`; declared external waits use finite wall-clock timeouts instead of busy polling.

Lifecycle controllers and their cohorts have reactive priority as separate waves. Accepted removal freezes the generation and delivers lifecycle dirtiness before a later field-owner invocation; owners can react to the tombstone for cleanup but cannot publish removed-generation facts. If a controller requires a newly committed field result, it runs again in a later wave. Native advancement to `T` precedes lifecycle changes at later microsteps of `T`; changes do not delete anything midway through an already completed native interval. There are no overlapping calls in v0.1. Numerical coupling accuracy remains an adapter/model responsibility: held inputs and communication points are explicit approximation choices.

Live ingress is an explicit ordered stream of recorded inputs and watermark advances. A watermark is monotonic and closes the entire physical instant, including microsteps. Late input is rejected, or delayed to a recorded legal unsealed delivery boundary under the pinned ingress policy; its original stamp is retained. Pacing uses monotonic wall time only to wait; it does not choose simulation ordering. Unpaced/offline execution is the deterministic reexecution profile. Real-time transport/pacing infrastructure is host-owned; distributed watermark protocols are **v0.2+**.

## 3. Identity, state and authority

`EntityRef(run_id, epoch, id, generation, type_id)` is immutable. Generation allocation is keyed by `(run_id,epoch,id)` across types: zero for first creation, exactly previous generation plus one for reuse. Duplicate live IDs, duplicate creates and remove/recreate of an ID in one transaction fail. The new generation can use a different concrete type; all bindings resolve anew. Abstract types cannot instantiate. Foreign namespaces and stale generations fail live operations; history preserves their identity. IDs/epoch/run identity are caller-pinned, never wall-clock UUIDs.

Lifecycle authority is distinct from field/edge authority. The manifest binds create domains (type plus ID selectors) and one lifecycle controller per created generation; ties/uncovered domains fail. Only that controller can remove it. Creation grants no authority to initialize another producer's fields or relations. Ordinary dynamic creation commits first; the newly bound producers receive lifecycle dirtiness at the next microstep and can then write. Bootstrap may combine predeclared creates and each owner's initial writes in one coordinated transaction. Cross-owner provisional creation during execution is **v0.2+**.

The selected active field set is fixed for each generation in v0.1. The manifest selects fields by exact bindings/capability rules and resolves exactly one partition per active key. Rules use explicit priority, inheritance membership and selectors; exact overrides must be explicitly represented. Highest-priority ties and uncovered active keys fail. Resolution must also match the partition's `produces` declaration; declaring production alone grants no authority. Inactive/inapplicable fields are errors; active but unset fields return tagged `ABSENT`. Dynamic component activation and runtime writer transfer are **v0.2+**. Bindings are immutable during a generation; neither queued work nor textual producer aliases transfer them.

An engine returns `FactWrite(key,value,acquired,valid)` or `RetractFact(key,valid,reason)`. The kernel derives producer and stamps availability at the actual publication Instant; it never trusts adapter-supplied authority/publication stamps. A committed `Fact` includes those stamps and its `(record_index,item_index)` version. Future-available samples stay in the adapter until their release boundary, which it must advertise. Validity may describe an earlier observation or future configuration; late publication never changes what was known before publication.

`field(key, valid_at, known_at)` selects versions with publication index/Instant within the knowledge cut and validity containing `valid_at`, choosing the greatest `(record_index,item_index)`. Lifecycle creation/death is resolved at that same cut before clipping validity. Later removal therefore cannot alter a historical view. Acquisition does not determine overwrite order. A winning retraction returns `ABSENT`, preventing older open-ended facts from resurfacing. JSON null is a concrete value only for nullable schemas. Unknown data requires an explicit tagged field schema; an evaluator's unresolved result is not automatically a field value. Units, frames, roles and truth/estimate distinctions are descriptor metadata; applications select distinct descriptors/producers when their semantics differ.

The store MUST deep-copy/freeze nested values at ingress and expose immutable values. There is no implicit interpolation, extrapolation or persistence beyond validity. Typed field references are identity references in v0.1: the target's creation must be visible in the base prefix or in the same coordinated bootstrap candidate. Current liveness is irrelevant; known tombstones remain legal, while never-created/future generations fail. Live-reference field cleanup policies are **v0.2+**; live links use relations now.

`Remove(ref,cleanup_refs)` ends lifecycle at its commit. The manifest selects native lifecycle participants for that generation, including native writers that require entity cleanup. Each emits a typed `LifecycleReady(ref)` operation only after actual cleanup at a supported boundary; the kernel stamps its producer/native frontier and freezes that participant's future writes for the generation. Remove must cite one valid readiness item per participant visible in its read cut (or its own earlier local operation). Cohort members cannot invent same-wave acknowledgments for each other. This is final cleanup acknowledgment, not a reversible prepare/2PC promise. A lifecycle notification cannot be latched past removal and then silently discard stale samples. A nonsplittable native owner therefore receives a preparation command at its latch boundary, reports actual cleanup, and only then can the controller remove the generation in a later reactive wave. Passive/local owners can acknowledge without physical integration. New writes/retractions to the generation in the removal wave are rejected; earlier versions remain and are clipped according to the reader's knowledge cut. The controller must coordinate incident edge closure/cancellation and obligation termination before removal or in its declared atomic cohort. Owners must consume lifecycle notifications and cease future writes; pending commands remain recorded and require an explicit target rejection/failure where appropriate, never silent disappearance. External deletion/cleanup is the adapter's job and must be acknowledged by actual results. Full in-memory history is required in v0.1; bounded dual-time pruning and resumable checkpoints are **v0.2+**.

## 4. Temporal relations and atomic commits

`AssertEdge(edge_id,relation_id,source,target,valid,acquired)` creates an edge once. The kernel stamps producer/publication. Its ID is unique for the run/epoch and never reused. Endpoints/definition are immutable. `CloseEdge(edge_id)` ends an edge whose start is strictly before publication and whose end is later, at exactly publication. `CancelEdge(edge_id)` cancels an edge whose start is at or after publication, including equality; it creates a version with no effective interval, not an invalid empty assertion. Neither operation reopens an edge or rewrites an older knowledge cut. Replacement uses a new edge ID. Reclosing ended/canceled edges, duplicate edge operations and backdated closure fail. Historical correction/reopen protocols are **v0.2+**. A first assertion of a finite historical interval is allowed when both generations already existed and their historical lifetimes/cardinalities cover it; late publication has no retroactive engine effect.

An explicit `edge_id` is the transport identity. Normalized identity policy can additionally require endpoint-pair uniqueness. v0.1 always forbids overlapping active duplicate pairs for one relation and counts distinct counterpart entities; edge multiplicity policies are **v0.2+**. Write authority resolves per `(relation_id,source_ref)`, independently of source lifecycle/field ownership. Assertions and close/cancel require that bound writer.

Relation descriptors define directed endpoint classes and separate `targets_per_source` and `sources_per_target` min/max bounds; integer max or explicit infinity, `0<=min<=max`. Ambiguous source `{min,max}` requires explicit direction during compilation. A minimum applies only to `ActivateObligation(obligation_id,relation_id,direction,endpoint_ref,valid)`. IDs are unique for the epoch; overlapping obligations for one directional scope fail. The manifest binds its authorized controller. `EndObligation(id)` ends an obligation with start strictly before publication at exactly publication; `CancelObligation(id)` cancels one with start at or after publication. Versions resolve by knowledge cut; no backdating, reopen or ID reuse. Zero bounds are allowed only as explicit scenario declarations, never normalization of missing bounds.

Validation runs once over the whole merged wave candidate, independent of operation/partition collection order. It checks endpoint membership, existence at the knowledge cut, lifetime coverage, interval shape, IDs/pairs and both cardinality directions. Sweep the affected effective edge, obligation and lifecycle boundaries, including future overlaps and open tails; apply version closures/cancellations before counting. Maxima hold whenever edges exist, minima throughout their obligation intervals. A finite edge cannot be committed under a longer minimum obligation unless committed replacements already cover the gap or the obligation ends; a hoped-for future update is not coverage. Dirty expiry timers notify consumers without synthesizing a replacement.

Two sources concurrently connecting to one max-one target cannot both commit: the entire conflicting wave fails, with no arbitrary winner. An atomic one-to-one swap passes only when all its owners participate in the same declared cohort and the final candidate graph is valid. Co-locate tightly coupled mutations where practical; distributed reservation/2PC is **v0.2+**. Kernel invocation completion order never chooses a winner.

Removal must close incident edges starting before removal and extending past it, and cancel those starting at or after removal. It likewise ends/cancels extending obligations; already ended intervals need no operation. Each closure/cancellation/obligation operation needs its own authorized owner, in the controller cohort or in earlier commits. There is no implicit cascade or borrowed lifecycle authority. Missing authorized cleanup rejects removal. Historical edges/endpoints/obligations remain queryable at older knowledge cuts.

A wave has one publication Instant and one `transaction_base_cut`, with a recorded per-invocation `read_cut`/native input cut. Merge by `(engine_id,partition_id)` and preserve each batch's ordered operations. Validate schemas, reads/causes, authority, facts, lifecycle, final graph, actions and routing before publication. Duplicate fact-key mutations fail. Local causes refer only to earlier operations in the same originating batch, never another cohort member's proposals. Candidate creates/closures never become visible during calls. The kernel appends operations, returned-invocation/frontier items, dirty/timer changes, routed enqueues and status transitions as one atomic transaction record before exposing the immutable view. Failure after any call terminates the run: private/native state may have advanced and no rollback/success is fabricated.

## 5. Typed messages and actions

Engines return `Emit(kind,schema_id,target_or_topic,at,payload,causes,source_stamp?)`. The kernel assigns source, source-local sequence, message ID and availability equal to publication. Sources have distinct `partition`, `ingress`, or `kernel` namespaces; IDs within each are unique. Sequence starts at zero and increases in canonical operation order; reserved ingress sequences are not reused. Message ID is the canonical JSON encoding without LF of `['aerokernel.message/v1',run_id,epoch,source_kind,source_id,sequence]`. Events require `at<=available`; commands may request future activation. Future `available` is invalid; delayed-release output stays in the adapter until its advertised boundary. Routing starts from `max(at,available)`, applies exactly one route lag, then the recipient latch. Same-time zero lag is eligible no earlier than publication microstep `m+1`. Positive lag or future activation yields eligibility `(future_ns,0)`; grid latching can move it later. Eligibility is not dispatch: actual `Delivery.instant` is the assigned reactive microstep, at least `(future_ns,1)` after physical work. Dirty/field notifications use the same rule. Record both eligibility and actual recipient dispatch.

Commands address one registered accepting partition. Events can address one partition or a topic with pinned sorted subscriptions. Undeliverable commands fail before enqueue; empty event fan-out is recorded. The kernel inbox contains `Delivery(message,recipient,instant,enqueue_ref,dispatch_ref)`, not a bare Message. Inbox order is `(delivery_instant,source,sequence,id)`; fan-out adds recipient ID. Recipient-specific delivery time is essential: an effect cannot cite a 10 ms enqueue as proof that a latched recipient saw it before its 20 ms dispatch.

Causes are typed `(record_index,item_index)` references or `LocalCause(i)` to an earlier operation of the same returned batch. The private token scopes local references; require `i<current_operation_index`, then rewrite them to final coordinates on merge. Cross-batch/cohort local references and forward references fail. A preexisting state cause must be visible in the invocation's read cut; an inbox/dirty/timer cause must be authorized by that invocation. Local earlier outputs are the explicit exception to prior-view visibility. Kernel-generated items may cite earlier generated items, including enqueue→submitted receipt. Every invocation records its cuts/authorized inputs automatically. Naming an undelivered command ID is correlation, not a legal reaction cause. Output cannot precede publication/authorized causes; actual recipient dispatch must be beyond the recipient logical frontier and the global physical seal.

Before every stateful call, including reset, physical advance/hold, dirty-only reaction and sampling, append an `InvocationIntent` record. It assigns each call an invocation reference and phase/partition/Instant, transaction base, read/native input cuts, expected logical/native frontiers and authorized inbox/dirty/timer items. A reactive intent atomically moves its inbox from pending to dispatch-authorized. All intents for a wave share the pre-intent transaction base; the post-intent prefix is a newer replay cut with unchanged facts but changed work queues. Append failure prevents every call in that wave. A successful call, even an empty batch/hold, produces exactly one return item in the wave transaction, citing its intent and updating frontiers. On fault the run records per-intent returned-but-unpublished or not-returned status as actually known; it never guesses external execution. At most one final result/fault resolution per intent, no retry/resume. Exactly-once external execution is **v0.2+**.

Ingress accepts a `CommandRequest(schema_id,target,at,payload,idempotency_key?,ingress_at_ns?)`, not a caller-stamped Message. Offline manifest requests can publish during bootstrap. After a seal at `C`, default publication eligibility is `C+1` ns, or an explicit strictly later ingress boundary; requested activation must not precede that boundary. `submit` atomically records an `IngressRequest` reservation at the current journal cut, reserves its source sequence/ID and returns that ID without invoking engines or publishing future state. The reservation is pending input, included in candidate-boundary selection and replay; `action(id)` reports a tagged pending-submission state until its boundary transaction enqueues the command, creates submitted status and receipt together. Control reservations after a seal do not reopen evolution at that physical time. Identical keyed requests scoped by source return the existing reservation/command ID; differing content fails. No new sequence/delivery/status for a duplicate. Engine outputs are never retried/deduplicated. Live ingress order/publication boundaries are explicit and recorded.

The compact action protocol is inspired by [ROS 2 actions](https://design.ros2.org/articles/actions.html), but uses kernel-owned receipt envelopes rather than assuming ROS transport:

| Current status | Legal next status |
|---|---|
| `submitted` | `accepted`, `rejected` |
| `accepted` | `executing`, `failed`, `canceling` |
| `executing` | `succeeded`, `failed`, `canceling` |
| `canceling` | `canceled`, `failed` |
| terminal (`rejected/succeeded/failed/canceled`) | none |

The enqueue transaction creates submitted status and its kernel receipt, caused by enqueue. `Receipt(command_id,status,result?)` and `Feedback(command_id,payload)` are reserved typed operations; the command descriptor declares result/feedback schemas and cancel support. Only its designated target issues subsequent receipts after original command dispatch, citing that dispatch and the current receipt head. Ordered transitions in a batch follow the table and can cite their own preceding local receipt. Status derives from those same receipt items, never a second later record. Acceptance/terminal decisions are at most one; deadlines are explicit, not an eventual-success promise. Feedback is legal while executing/canceling and preserves status. Receipts/feedback include the originating Emit/reservation ref. Partition origins receive them under ordinary routing/latching, so engines learn assigned child command IDs; ingress origins observe the canonical items through action queries/host observers, without an imaginary simulator recipient. `action(id)` exposes status/head/history, or pending submission for a reserved ingress ID.

`RequestCancel(command_id)` is also an engine proposal. Only the originating partition/ingress or an explicitly named control source in the manifest can request cancellation. The host `cancel(id)` uses its pinned control ingress identity and returns `CancelRequestResult(queued,message_id,rejection_ref)`: queued means a cancel ID was reserved, not target acceptance; rejection has no message ID and cites its recorded reason. For a submitted/pending, already-canceling, unsupported, unauthorized or terminal action, it records a typed request rejection and creates no cancel message/status change; pre-activation withdrawal is **v0.2+**. Otherwise it stages a reserved typed cancel request at the next legal ingress boundary, with its own message ID and no recursive action. `CancelDecision(command_id,cancel_message_id,accepted,reason?)` is a target-issued envelope citing cancel dispatch and the current original receipt head. Validate that the head is still current in ordered candidate state. Acceptance from accepted/executing atomically creates the canceling head; rejection preserves head/status and is routed to the cancel requester. `canceled/failed` cites that accepted decision/head; canceled means actual cleanup completion. Completion first rejects a later cancel, while accepted cancellation excludes success. Internal boundary completion precedes newly dispatched same-time cancel; within a reaction sorted inbox and ordered decisions govern ties. Missing receipts, RPC success or accepted cancellation never imply success/cleanup.

## 6. Engine and RPC contracts

A manifest declares unique nonempty engine IDs and globally unique partition IDs, create/lifecycle domains, produces/consumes selectors with lag/latch policies, schemas/subscriptions, native timing, exact-stop/hold/reentry capabilities, disjoint cohorts, sampled contexts and history/input requirements. Reads use a restricted view; writes/causes validate against the private intent token. `StateView.transaction_base_cut` names the pre-intent wave prefix; `StateView.cut` is the permitted per-invocation read cut, never newer. There is no public `Kernel.commit` accepting a caller producer ID. The binding compiler pins potential dependency edges for all selected writer/create rules and declared message target sets; dynamic creation/routing must stay within those domains. It checks reentry and sampled cycles there rather than guessing a DAG from current live instances. Evolution reads cannot see future availability or undeclared state/lifecycle. Arbitrary Python globals cannot be policed: conforming deterministic engines must honor access/RNG contracts.

`reset` runs once after binding against an explicit bootstrap context. Initial batches use predeclared refs and normal writer validation; no evaluator/engine observes partial bootstrap. `advance` acknowledges exactly the granted logical boundary, records native interval/cut, and accepts no boundary inbox. `react` changes state at the assigned microstep, never native physical time. A reacting partition with no output can return an empty batch; it must not reschedule itself endlessly without real work. All calls on all Engine objects are serial in v0.1, using canonical ready order. Parallel execution/shared serialization-group negotiation is **v0.2+**. `close` is idempotent; cleanup failure is reported.

An out-of-process adapter uses JSON-lines RPC with `protocol="aerokernel.rpc"`, major/minor, connection-local increasing integer request ID, operation and payload. Responses echo identity/version/ID and contain exactly one result or structured error. Lifecycle is `NEW→HELLO→RESET→READY→CLOSED`; only horizon/advance/react run in READY. Fault enters TAINTED and permits cleanup only. One reset/in-flight request per connection. Hello requires equal major and negotiates greatest common minor/features; required unsupported features fail. Record negotiated profile, manifest and registry digest. Every horizon/advance/react carries partition, query/invocation ref, expected logical/native frontier and transaction/read/native input cuts. Responses echo the token and frontiers. Horizon must match recorded prestate; advance returns logical reached exactly equal to grant; react returns assigned Instant with native frontier unchanged. Certified hold advances only logical time. Frontier/cut mismatch taints/faults before publication; native frontier changes only on physical integration, while the coordinator records/adopts the next latched input cut after settlement at a native boundary. This adoption is explicit token metadata, never reinterpretation of an already integrated interval.

Frames are LF-terminated UTF-8 objects; duplicate keys, nonfinite numbers, invalid UTF-8, incomplete/oversize frames fail. The configured byte limit (default 8 MiB) includes LF. Structural/schema integer tokens are parsed losslessly, never through binary64; adapters unable to preserve them cannot bind. Canonical value rules are in section 9. Stdout is protocol-only, stderr diagnostics. Views are declared projections/history slices with Cut and latched native input cut, not the whole ontology. Each operation has a positive timeout. Wrong IDs/versions, EOF and timeouts taint/close the connection, fault the run and publish no partial result. Record operation/request/policy and stable fault code; elapsed wall time belongs only in diagnostics. Socket drain is not a kernel commit; stateful requests are never automatically retried.

Paused request arrival MUST use a declared finite budget independent of operation execution, frame drain and cleanup budgets. The optional `wall_clock_hold/v1` hello feature pins `PausePolicy(idle_timeout_s,max_hold_s,frame_timeout_s)` on both peers; declarations must match. In READY, a negotiated `hold` carries an explicit positive duration no greater than `max_hold_s`, a reason and the ordinary token/contract. Its acknowledgment grants a wall-clock request-arrival lease; it invokes no native engine work and changes no logical/native frontier, state, receipt or measurement. The lease starts after the response write, expires without implicit renewal, and can be replaced only by another explicit journaled request. Hosts journal request intent before sending and acknowledgment only after all remote peers confirm; an unacknowledged prefix is incomplete, and an uncertain result faults without retry. `Kernel.hold_wall_clock` applies this contract only at a settled world boundary. Local in-process engines remain paused because the host invokes no engine work.

A host may supply a bounded abort hook for a native call that exceeds its deadline or loses transport. Abort requests cancellation/termination under the host's process ownership; cleanup MUST still acquire the execution lock before entering `Engine.close`. The kernel cannot kill an arbitrary foreign Python callback. Abort and cleanup failures remain failures, and native mutation after a timeout never authorizes publication or automatic retry.

## 7. Registry and AeroGraph binding

The base registry is an immutable serialized collection of selected normalized type, field, relation and message descriptors, with revision/digest. Type inheritance is a DAG; `is_a` is reflexive/transitive for known types, and unknown IDs raise. Fields require declaring-type inheritance or explicitly selected capability application. Unit/frame/semantic-role/time metadata is retained as opaque metadata; the kernel does not interpret a drone/ontology vocabulary or convert quantities.

Every active schema MUST compile into the v0.1 portable subset: nullability; strict bool/integer/finite binary64/string; enums; typed identity references; length-constrained arrays/vectors/matrices; string-key records with required members/explicit extra-member policy; and tagged unions. Numeric/length bounds and referenced schemas are explicit. Bool is not integer; no scalar coercion. Enum members are canonical typed values, unique and matched by section 9 equality (`[1]` excludes true and 1.0). Unions have a discriminator and one selected branch, never first-match validation. Unsupported active constraints fail binding. Full JSON Schema/custom executable validators are **v0.2+**; replay never needs compiler code.

The optional AeroGraph compiler MUST preserve source descriptors/review disposition/native AST authority and record a normalization digest. It selects definitions, retains field role/unit/frame/time including acquired/available/valid, resolves native inheritance and explicit capability application, and maps scoped producer roles `(role,subject selector,field/relation selector)` to actual partitions per scenario. `ownerSubject`, `writer` strings and `Own/Oi` aliases describe semantic subjects/producer requirements, not runtime authority. Missing/ambiguous selected bindings require real producers/conversions before activation; replacing missing data with UNKNOWN is not a production path.

Seven directories are browsing categories, not inheritance. Suggested parents/unattached profiles cannot become `is_a`. Capability profiles are not engines. Source `configuration`/`config` and cardinality direction require explicit recorded normalization, not a guessed universal default. Unselected imperfect definitions may remain outside the active registry. Original rule references/inline bindings, scoped parameters, applicability and temporal ASTs stay with their native evaluator; the kernel exposes exact typed history and schedules it as an ordinary producer. Pilot/default examples are authored configuration only when explicitly selected, never telemetry, approval or evidence of computation. Selecting content must not promote research proposals, alter review/collection scope or collapse truth into estimates.

## 8. Observers, sampled frames and optional `entered` profile

Each commit contains deterministic dirty versions/retractions, relation/obligation endpoints, lifecycle refs and routed inputs, with originating item refs. `value_changed` compares effective current values before/after the transaction or validity boundary, by canonical typed equality; a future-valid version can be unchanged now and changed at its start. Null/absence and bool/int differ. Only declared value-only consumers suppress equal-current-value notifications. Version/time-sensitive consumers still receive equal-value versions. Validity/window deadlines are kernel timers that record notifications even without writes; their dedup keys derive from originating version/boundary. Self-notification requires an explicit dependency. Read-only observer failure is diagnosed without changing ordering; required evaluator/recording failure faults the run.

`ScheduleTimer(timer_id,due,payload)` and `CancelTimer(timer_id)` are ordered partition-owned operations. IDs are unique within that partition/epoch and never reused; scheduling requires due strictly after publication, including a later same-time microstep. Cancellation requires a still-pending timer. Firing atomically marks it consumed and records its notification/cause; one-shot timers do not silently recur. Recipient timing/latching turns due into actual reactive dispatch exactly as for messages. Automatic validity timers use a reserved kernel namespace. Native internal wakeups are advertised separately by horizons; no kernel timer can bypass a closer native wakeup or the microstep bound.

A sampled evaluator declares its whole upstream dependency cone, context and timers. The compiler rejects every transitive zero-lag cycle containing a sampled evaluator, including evaluator output→other engines→any sampled input. Breaking that return path needs positive physical lag. Ordinary discrete reactive cycles outside sampled cones follow section 2. At a boundary, coalesce upstream dirtiness; sample once only after the entire upstream cone has settled. Sampled contexts are processed in deterministic topological order, settling ordinary downstream work between levels. Later same-time input into an already sampled cone is a causality fault. Sampling is triggered by declared input changes/timers, not by viewer cadence or arbitrary `run_until` calls.

The resulting `SampleFrame(context_id,physical_ns,cut,bindings,clocks,result)` is an atomic recorded output. Its preceding frame is the unique latest recorded SampleFrame for that context at strictly earlier physical time, not an arbitrary raw commit. Each context has at most one frame per physical time; same-nanosecond microsteps do not create multiple prior frames. Context/parameters/native clocks/mapping versions are pinned for an epoch; rebind/baseline migration is **v0.2+**.

The following is an optional AeroGraph adapter conformance profile, not a base-kernel predicate language. Expanded `entered` emits only known false→known true after matching full role identities, declared instance sources and native clocks. Supplied history at/after current time, duplicate latest times or mismatched context is invalid; missing/unknown is not false, and the adapter cannot skip a newer unresolved frame to find an older false. A first true produces an unresolved baseline result and no event. Preserve applicability and scoped parameters. Record unresolved/invalid results and diagnostics as well as known results. An event records current sample time and observed interval `(previous_time,current_time]`, not a continuous crossing time. Kernel-native microstep transition evaluators use a distinct schema and cannot masquerade as this profile.

## 9. Ordering, record and replay

Identifiers/map keys are compared by exact UTF-8 bytes, with no Unicode normalization; reject surrogate code points. Selector expansion, registry enumeration, subscribers, ready partitions and cohorts are sorted. Physical/reactive order is `(ns,microstep,phase_priority,engine_id,partition_id)`; phase priority is the explicit sequence in section 2, lifecycle/cohort controllers before ordinary reactors. Sample levels use a stable topological ordering. Batch operation indices are supplied ordered positions, never dictionary/set iteration order. Completion/registration order does not select indices or IDs.

The canonical runtime value tree is null, bool, arbitrary-precision int, finite Python binary64 float, Unicode string, ordered array or string-key record. A schema-directed identity ref encodes exactly as `{"$ref":{"run_id":str,"epoch":str,"id":str,"generation":int,"type_id":str}}`; a union encodes exactly as `{"$case":tag,"value":branch_value}`. These are validated by their declared schemas, not guessed from record shape. Decimal, bytes, tuple-as-array, custom objects and unordered sets fail as field/payload values. Snapshots may freeze arrays/maps preserving the wire tree. Normalize float signed zero to 0.0; int 0 remains distinct. Equality is type-sensitive structural equality; NaN/infinity/coercion fail. Integers have no fixed machine width but the header pins explicit resource budgets: decimal token digits (default 4096), frame bytes and nesting depth (default 128). In-process values obey the same limits as RPC/replay; exceeding them raises ResourceLimit, never rounding/truncation. The implementation must enforce/parse the pinned digit limit explicitly. Binding rejects a budget larger than an active CPython decimal-conversion limit; it neither silently lowers the budget nor changes process-global limits.

v0.1 byte determinism is pinned to the declared CPython/serializer version, not claimed across implementations or Python releases. Canonical encoding uses `json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))`, UTF-8 plus one LF, no BOM, after canonical tree normalization. For valid Unicode keys the specified byte order agrees with code-point order used by this encoder. Binary64 formatting/escaping is that pinned encoder's behavior; replay preserves typed values rather than requiring another runtime to produce identical bytes. Registry digest is SHA-256 of the canonical selected descriptor object encoding with the trailing LF omitted. Cross-runtime canonical numerical encoding is **v0.2+**.

Named RNG streams belong to exactly one partition and are accessed sequentially. `root_seed` is an integer (not bool). Derivation hashes the canonical encoding, without LF, of `['aerokernel.rng/v1',root_seed,engine_id,partition_id,stream_name]`; the seed is `int.from_bytes(SHA256(bytes).digest(),'big')`. Names are declared/unique; no Python `hash`, global RNG, lazy discovery ordering or shared generator. Supply `random.Random(seed)` and record every derived seed, Python/RNG and engine version. Offline byte reexecution requires identical pinned inputs and run-control/communication-boundary schedule, configuration/run/epoch/serializer, conforming deterministic engines and no wall-clock/native/global randomness. External numerical repeatability is a measured adapter capability, never implied by lockstep. Replay uses no RNG.

The header contains format/version, run/epoch, time origin, complete normalized descriptors/digest, resolved bindings, timing/latch/cohort/sample/ingress/resource policies, mappings, manifests/configuration, serializer/RNG versions and seeds. Atomic indexed record types cover bootstrap/transactions, ingress reservations/rejections, invocation intents, frontier/seal/watermark/timer controls, faults and terminal status. Every transaction includes all ordered operations, generated facts/messages/receipts, enqueues, action transitions, dirty/timer changes and all invocation return/frontier items, including empty returns. No dependent state effect lives in a later line. Horizon queries/caches need not be logged; every stateful grant/native interval/input cut does. Invocation intents, pending ingress and returned/unresolved invocation state are replayed alongside messages.

Replay validates the header subset, monotonic contiguous indices, record schema, typed backward/local refs, exact normalized integrity rules and legal queue/action/frontier transitions, then applies complete records. It never runs engines, evaluator/native AST code, custom validators, RNG or live clocks. A valid prefix is the header plus zero or more complete, structurally and semantically valid atomic LF-terminated records; each such prefix reconstructs lifecycle/history, authority, frames, action status, pending ingress and pending/dispatch-authorized/returned invocation work. A valid prefix may end during a run or after a dispatch with no returned result; it is incomplete, not successful. Trailing incomplete bytes are rejected by default; explicit recovery exposes the last complete valid prefix marked incomplete. Complete but corrupt records are never silently skipped.

Append+flush is required before visibility; configurable `flush` versus `fsync` durability is pinned in the header. On append/flush failure, no new live view is exposed and the process faults. Storage may contain a complete write whose acknowledgment was lost; recovery treats valid complete records as authoritative and reports incomplete execution, without claiming external rollback or successful execution. This is a WAL/replay contract, not a crash-resume contract. Hash chains/sealing, resume and exactly-once external effects are **v0.2+**. Wall timestamps, elapsed time and traceback text belong in diagnostics excluded from byte reexecution claims; stable operation/fault records remain authoritative.

## 10. Errors, invariants and verification

Errors contain stable code, assigned Instant/cut, partition, offending key/message and typed causes. Preflight binding/ingress errors can be rejected before invoking an engine. A rejected output wave, causality/synchronization/adapter/journal fault stops the run at the recorded prefix; if recording itself failed the prefix alone exposes incompleteness. Business rejection/failure is a typed outcome. No silent skipping, fallback data, automatic retry or synthetic success.

Required implementation coverage (`P` denotes Hypothesis properties; this specification revision does not implement these tests):

| ID | Testable invariant | Required coverage |
|---|---|---|
| I1 | Ordered integer time, explicit quantization and prefix cuts. | Bool rejection; P rational mapping/order and equal-Instant cuts. |
| I2 | Namespace/generation/type identity and lifecycle authority. | Duplicate create, reuse/type rebinding, stale/foreign refs, unauthorized remove. |
| I3 | Every selected active field/edge/obligation scope has one declared bound writer. | Role/default ties, dynamic create, unset/inactive distinction, nonowner initial write. |
| I4 | Whole-wave publication is immutable/atomic. | Invalid last op, journal failure; P completion/registration permutations, not semantic op permutations. |
| I5 | Knowledge/validity and tombstones preserve each older prefix. | Delayed facts, null/ABSENT/retraction, later removal/closure; P bitemporal read oracle. |
| I6 | Final graph meets directional cardinalities over all affected intervals. | Cross-owner same-wave collision/swap, future overlap, obligations/expiry, removal/cancel; P sweep oracle. |
| I7 | No unsafe advance or retroactive input application. | Zero bound equality, idle DES, internal/cancel tie, 3→20 ms latch, native-start cut, failed exact stop. |
| I8 | Discrete same-time work settles or faults within its bound. | Convergent transitions, equal-version storm, unsupported reentry, deadlock versus typed wait. |
| I9 | Typed routing/recipient dispatch causes have one legal order. | Delayed fan-out, undelivered/future cause, idempotent ingress, no delivery behind seal/frontier. |
| I10 | Action state derives from authorized receipts with at most one terminal. | Failure before executing, cleanup/cancel races, missing receipt, forged target/source. |
| I11 | RPC faults cannot publish partial result or authorize retry. | Framing, lossless large integer, wrong ID/version, EOF/timeout, taint lifecycle. |
| I12 | Every complete valid journal prefix replays without plugin execution. | Engine/evaluator spies; P traces, crash after dispatch, atomic enqueue/dirty/status, truncation/corruption. |
| I13 | Sample protocol is stable; optional entered profile preserves native semantics. | Whole-cone settlement, feedback path, once/time; profile false→true, unknown/first true, identity/clock mismatch. |
| I14 | Ordering/RNG contracts isolate deterministic conforming engines. | Reordered registration, independent streams, type-sensitive floats/zero, byte reexecution on pinned runtime. |

Use synthetic registries and instrumented engines first. Adapters separately verify native time, actual input application, readiness and repeatability. Optional AeroGraph differential fixtures use actual positive/negative/missing-input cases without running its build scripts or modifying that repository. I14 does not claim detection of undeclared Python global state or determinism under wall-clock timeouts/live arrivals.

## 11. Package and public API

Responsibilities map to `aerokernel/{time,ids,registry,state,relations,messages,engine,coordinator,journal,rpc}.py`; test helpers belong in tests, not production fallback paths. Frozen records, Protocols, heap queues, indexed histories and affected-interval sweeps are sufficient. Do not add a generic distributed transaction service or numeric solver. Ordered proposal records named below have the required fields defined in sections 3–8.

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True, order=True)
class Instant:
    ns: int
    microstep: int = 0

@dataclass(frozen=True)
class Cut:
    index: int
    instant: Instant

@dataclass(frozen=True)
class EntityRef:
    run_id: str
    epoch: str
    id: str
    generation: int
    type_id: str

@dataclass(frozen=True)
class Batch:
    reached: Instant
    transaction_base_cut: Cut
    read_cut: Cut
    native_reached_ns: int
    native_input_cut: Cut
    operations: tuple["Operation", ...]  # ordered proposals; kernel stamps outputs

class StateView(Protocol):
    cut: Cut  # restricted read cut
    transaction_base_cut: Cut
    def field(self, key: tuple[EntityRef, str], valid_at: Instant,
              known_at: Cut) -> "Fact | Absent": ...
    def history(self, key: tuple[EntityRef, str], start: Instant,
                end: Instant, known_at: Cut) -> tuple["Fact | Retraction", ...]: ...
    def relations(self, relation_id: str, valid_at: Instant,
                  known_at: Cut) -> tuple["Edge", ...]: ...
    def action(self, command_id: str) -> "ActionState": ...

class Engine(Protocol):
    partitions: tuple["Partition", ...]
    def reset(self, context: "RunContext", view: StateView) -> tuple[Batch, ...]: ...
    def horizon(self, partition: str, cut: Cut) -> "Horizon": ...
    def advance(self, partition: str, to: Instant, view: StateView) -> Batch: ...
    def react(self, partition: str, to: Instant, view: StateView,
              inbox: tuple["Delivery", ...], dirty: tuple["Dirty", ...]) -> Batch: ...
    def close(self) -> None: ...

class Kernel(Protocol):
    def bind(self, registry: "NormalizedRegistry", manifest: "BindingManifest",
             engines: tuple[Engine, ...]) -> None: ...
    def start(self) -> StateView: ...  # coordinated reset/bootstrap + settle t=0
    def submit(self, command: "CommandRequest") -> str: ...
    def cancel(self, command_id: str) -> "CancelRequestResult": ...
    def run_until(self, ns: int) -> StateView: ...  # settle through ns, never go back
    def view(self) -> StateView: ...
    def close(self) -> None: ...
```

`history(start,end,known_at)` returns all known fact/retraction versions whose effective validity intersects `[start,end)`, in version order, not a resampled series. Unknown keys/action IDs fail. `bind` occurs once; submit/cancel need a started nonfaulted run and reserve a recorded later ingress boundary. Static inputs install before start. Reservations do not inject state/delivery into a sealed instant; `run_until` processes them at their boundary. A backward run call fails; an equal call returns the settled view without resampling. A requested run limit may insert an exact communication boundary and alter a numerical adapter's subdivision, so its schedule is part of reexecution input. Viewers read snapshots and never choose these boundaries. Private commit requires invocation tokens; replay exposes reads only.

## 12. Worked coordinator trace

This is explicitly authored toy simulation, not telemetry. Types `Item` and `Order` have `position_truth`, `order_status`, `in_zone`, and a one-item-per-order `fulfills` relation. Bootstrap refs/active fields/writers are predeclared: `mover` writes position and move receipts; `orders` controls Order lifecycle/status/edge and order receipts; `zone_eval` writes `in_zone` and sampled `zone.entered`. Item lifecycle has its own bound controller. The coordinated bootstrap commits each owner's initial facts/edge together.

Mover integrates `x += 1 unit/ms` at native boundaries 0,5,10,… ms. Its pose-like field is merely scenario content. It supports reaction without integration on those boundaries; off-grid commands latch to the next native boundary, after that step. Orders is DES, separately acknowledging business acceptance. The evaluator samples only movement changes/timers, with zero-lag mover→evaluator→orders and a positive-lag return path to any sampled input. Acquisition uses the pinned canonical clock; the kernel stamps all outputs.

| Physical time / microstep | Committed work and causal consequence |
|---|---|
| 0 / 0 | Bootstrap: `x=0`, order new, relation and resolved authority commit together. |
| 0 / later waves | Offline order submission is delivered; orders accepts/executes and submits move. Mover reacts without integrating, accepts/executes. |
| 0 / sample phase | Upstream settles; evaluator records a real false baseline. No entered event. |
| 2 ms / 0–1 | Orders' internal feedback commits. Mover logically holds at 2, native frontier 0; no new position/interpolation. Evaluator is not resampled by a run-limit hold. |
| 5 ms / 0, then sample | Mover integrates from native 0 using its pinned 0 input cut, publishes `x=5`; evaluator records false. |
| 10 ms / 0, then reaction | Mover publishes `x=10`; its reaction emits move-success from actual completion. Physical time is not integrated twice. |
| 10 ms / sample phase | Evaluator records true and emits `zone.entered`, caused by the settled movement frame, interval `(5,10] ms`. |
| 10 ms / following wave | Orders receives its recorded Delivery and records awaiting acknowledgment. Arrival does not finish the order. |
| 13 ms / 0–1 | Authored DES acceptance timer commits accepted output/succeeded receipt. Mover holds logically at 13, native frontier 10, next integration 15. |

Each row replays without toy engines/predicate recomputation. A command first available at 3 ms to a nonsplittable 20 ms engine is dispatched/applied after its 20 ms integration, never over 0→20. An external event occurring at 3 ms but only returned at 20 ms is first published/usable at 20 ms. Immediate influence requires an adapter exposing a real exact 3 ms communication boundary.

## 13. Scope and deferred work

v0.1 includes serial centralized DES/fixed-step/lockstep coordination, explicit real-time ingress/wait policy, native holds/latching, bounded discrete reactive cycles, immutable per-generation ownership, typed values/messages/receipts, full bitemporal history, temporal cardinality checks, stable sampled frames, JSONL RPC and offline replay. Generality comes from descriptors and adapters, not from implementing every external simulation protocol.

Explicit **v0.2+** work: distributed per-stream conservative grants/null-message/watermark protocols; arbitrary early-return negotiation, optimistic rollback and numerical/algebraic solvers; parallel engine calls; dynamic writer/lifecycle transfer and component/context/clock rebind; cross-owner provisional creation/distributed reservation/2PC; edge multiplicity/reopen/historical correction; live-reference field cleanup; bounded dual-axis retention/checkpoints; full JSON Schema/custom validators; cross-runtime canonical float bytes; pre-activation action withdrawal; crash resume/exactly-once external execution; cryptographic evidence/sealing. None is a hidden v0.1 conformance requirement.

Deployment choices remain concrete scenario inputs: real adapter stop/latch capabilities, selected normalization/producer bindings, and ingress/timeout/durability budgets. No selected missing producer is patched with demonstration data. The next implementation should prove the two-engine toy slice and adversarial traces below before expanding scope.

## Review log

Severity: **Critical** can violate causality, authority, atomicity or replay; **High** prevents interoperable/correct implementation; **Medium** leaves policy/scope or verification ambiguous. This revision is a design audit, not a claim that the kernel/tests already exist. Three native read-only review sessions covered synchronization, ownership/relations and replay/API; their returned findings were checked and integrated. The requested WorkBuddy DSH GLM backend could not be reached (`Operation not permitted` on the configured localhost proxy); no successful GLM session is claimed.

| Issue | Severity | Resolution |
|---|---|---|
| Strictly advancing horizons excluded zero lookahead; waiting for upstream closure through T deadlocked same-boundary chains/SCCs. | Critical | Common barrier; equality is serviced output; separate advance/reaction/seal (§2). |
| An idle DES infinity promise could let a consumer pass a later upstream cause. | Critical | Global minimum includes every partition/route/deadline; infinity never overrides another activation (§2.1). |
| Serial early returns still let a peer advance irreversibly past an earlier newly exposed event. | Critical | Exact-stop/certified-hold contract; generic negotiation/rollback v0.2+ (§2.1). |
| Same-time internal event versus external cancel had no confluent order. | High | Internal/physical output precedes boundary external reaction; inbox/receipt order resolves later ties (§2.2, §5). |
| One frontier conflated native integration and logical holds. | Critical | Separate recorded native/logical frontiers and certified no-output holds (§2.1). |
| Latest global view retroactively fed a held 0→20 ms integrator with 3 ms input. | Critical | Pinned native interval-start cut; latch applied only after native boundary (§2.2). |
| Field/relation/lifecycle reads were not fully synchronized; sample holding could extend expired facts. | Critical | Restricted lag/native cuts, expiry notifications and explicit validity/latch policy (§2.2). |
| Fixpoint equality contradicted time/provenance-sensitive consumers; event storms could evade SCC limits. | High | Discrete queue quiescence; global same-time bound; numeric solver v0.2+ (§2.2). |
| Readiness, stalled bounds and idle/run-limit behavior could spin or deadlock indefinitely. | High | Infinity convention, progress tests, explicit bounded waits and seal conditions (§2). |
| Creator/remover authority, duplicate generations and cross-type ID reuse were undefined. | Critical | Separate lifecycle bindings; exact generation sequence; candidate rebind on reuse (§3). |
| Creation gave implicit authority over another engine's initial fields. | Critical | Validate each bound producer; coordinated predeclared bootstrap; dynamic owners react after creation (§3). |
| Active field set, unset/inactive distinction and scoped producer aliases were ambiguous. | High | Fixed selected set and declaration+binding enforcement; scoped compiler bindings (§3, §7). |
| Removal raced field writes and future incident edges/obligations. | Critical | Lifecycle priority/cohorts, native cleanup acknowledgment, no removal-wave writes, explicit authorized closure (§2–4). |
| Transfer freeze/ack races and failure recovery were underspecified/oversized. | High | Immutable generation bindings; runtime transfer v0.2+ (§3, §13). |
| Later tombstones/closures could change an older knowledge view. | Critical | Prefix Cut governs lifecycle and edge versions as well as facts (§2–4). |
| Dual-time history pruning could discard still-visible open facts/retractions. | High | Full memory history v0.1; pruning/checkpoints v0.2+ (§3). |
| Typed ref tombstone/liveness policy was unspecified. | Medium | Identity refs allow known tombstones; live links use relations; live-field policies v0.2+ (§3). |
| Edge close/replacement/reopen and future-edge cancellation could disagree. | High | Assert-once IDs, current close versus future cancel, new-ID replacement (§4). |
| Concurrent max-one target claims passed per-partition checks. | Critical | One merged candidate interval sweep; reject whole conflicting wave; atomic cohorts for swaps (§4). |
| Cardinality direction, multiplicity and target-side minimum authority were missing. | High | Two explicit directions, distinct counterparts, owned directional obligations (§4). |
| Minima relied on future uncommitted replacement at expiry. | High | Committed interval coverage required; explicit obligation end/replacement (§4). |
| Adapter guessed availability/producer/sequence and could impersonate authority. | Critical | Proposal records; kernel stamps output identity/publication under private invocation tokens (§3, §5–6). |
| Public commit bypassed grants and read/inbox provenance. | Critical | Remove public commit; bootstrap/start plus private coordinator commit (§6, §11). |
| Bare inbox Message lost recipient-specific latch time; enqueue cause enabled premature reaction. | Critical | Delivery with dispatch ref/instant; visible causes and next-wave effects (§5). |
| Future message availability, duplicate retries and boundary-lag application were ambiguous. | High | Availability equals publication; one route lag then latch; ingress-only keyed dedup; no stateful retry (§5). |
| Receipt schemas/status authority were undefined; failure before execution and cancellation cleanup were unrepresentable. | High | Reserved typed envelopes, result/feedback schemas, failed/canceling states, explicit decisions (§5). |
| Exactly one acceptance was impossible for pending/faulted runs. | Medium | At most one decision; pending/incomplete status retained, no eventual-success inference (§5). |
| Transaction/enqueue/dirty/status split across journal lines broke valid-prefix replay. | Critical | Every dependent state effect in one atomic transaction line (§4, §9). |
| Dispatch recording could be mistaken for proof of external execution after a crash. | Critical | Dispatch intent/returned distinction; no resume/retry/exactly-once claim (§5, §9). |
| `Any`, JSON escapes/float formatting/signed zero and dict/set order defeated determinism. | High | Portable typed value tree, normalized signed zero, sorted IDs, pinned stdlib encoder; no cross-runtime byte claim (§7, §9). |
| RNG tuple encoding, digest conversion, stream ownership and wall-clock leaks were unspecified. | High | Domain-separated canonical seed array, big-endian SHA-256 seed, exclusive streams and offline profile (§9). |
| Replay required arbitrary external registry validation/evaluator code. | High | Complete normalized portable descriptors in header; replay executes kernel rules only (§7, §9). |
| Journal flush acknowledgment loss and external advancement contradicted all-or-nothing claims. | High | WAL prefix authoritative; no live publication after failure; recovery remains incomplete (§4, §9). |
| Shared mutable-state/parallel invariants were untestable. | High | Serial v0.1 calls; determinism claims conditional on conforming engines; parallelism v0.2+ (§6, §10). |
| RPC lifecycle, version negotiation, large integers, frame size and taint rules were incomplete. | High | Explicit state machine/profile, lossless integer tokens, LF-inclusive limit and no retry (§6). |
| Early sampled entered could fire true before a later same-time producer made it false. | Critical | Whole-cone settlement, one SampleFrame/context/time, transitive return-path rejection (§8). |
| Raw commit history/unknown frames could fabricate entered baselines. | High | Prior recorded physical SampleFrame; no unknown-as-false/skipping; record unresolved baseline (§8). |
| AeroGraph entered/roles/review semantics leaked into universal kernel obligations. | High | Optional compiler/evaluator conformance profile; core keeps opaque metadata/history/scheduling (§7–8). |
| V1 protocol numbering hid too much runtime-transfer/solver/retention/concurrency scope. | Medium | Consistent v0.1 scope and explicit v0.2+ list (§13). |
| Test invariants confused batch completion permutation with semantic operation permutation. | Medium | Revised I1–I14 with meaningful premises/oracles and optional profile coverage (§10). |
| Trace/run-control sampling could change entered behavior without a data update. | High | Declared sample triggers; equal run call/hold does not resample; trace uses settled frames (§8, §11–12). |
| Route eligibility at (T,0) conflicted with physical-before-inbox order. | Critical | Separate eligibility from actual reactive dispatch, at least T/1 (§2, §5). |
| A global transaction cut let held engines read newer-than-native inputs. | Critical | Separate transaction/read/native cuts in restricted views and intent tokens (§2, §6, §11). |
| Same-transaction causes let one engine guess another engine's unseen output. | Critical | Local references scoped to the originating batch only (§4–5). |
| Empty, dirty-only, sampled and physical calls had no crash-visible invocation identity. | Critical | Intent/return state for every stateful call, including empty holds (§5, §9). |
| Cancellation could cite a stale receipt head, and child-command producers could not learn kernel IDs. | High | Current-head validation and cancel envelopes; origin-routed submitted/result receipts (§5). |
| Removal exactly at a scheduled edge/obligation start had no legal cancel operation. | High | Cancel start >= publication; explicit obligation close/cancel versions (§4). |
| Dynamic submit after a sealed run lacked a legal publication time. | High | Recorded reservation with explicit later eligibility; enqueue/status atomic at that boundary (§5, §11). |
| Tagged ref/union wire shapes and ambient integer digit caps remained ambiguous. | High | Exact schema-directed shapes and pinned explicit resource budgets (§7, §9). |
| Window/validity timers and effective-value equality lacked executable operation rules. | High | Partition-owned one-shot timer proposals; before/after effective equality and reserved validity timers (§8). |
| Run-limit subdivision was omitted from deterministic reexecution inputs. | Medium | Pin/record run-control boundaries; viewers read snapshots (§9, §11). |

Review validation for this document: read-only source/schema/interpreter checks, concrete counterexample walkthroughs, independent returned reviews, and syntax/structure checks of the revised API sketch. No upstream build, runtime adapter experiment or implementation test is asserted by this audit.

## 14. v0.2 additive amendment: local named live ingress (K4)

This amendment is normative for kernel v0.2 and journal 1.3. It overrides the
single shared live-watermark restriction in §§2, 5, 9 and 13. All other v0.1
contracts remain. Coordination is local and single-process; distributed grants,
clock remapping, rollback, native early-return negotiation and crash-resume are
outside this amendment. Offline workloads without live declarations retain
journal 1.2 output, including their existing deterministic byte fixtures.

### 14.1 Stream declarations and influence bindings

`IngressStream(id, policy, mapping_id, engine_ids)` MUST pin a unique nonempty
stream ID, one registered clock mapping and a nonempty tuple of distinct engine
IDs. `IngressPolicy` retains its existing positional fields and appends optional
`allowed_lateness_ns: int | None`. The bound MUST be a nonnegative integer; bool
is invalid. `lateness` selects `reject` or `delay`; `timeout_s` is a finite positive
wall-clock wait budget, and optional `speed_ratio` is positive and finite.
These policies belong to streams, not to one shared run-wide watermark.

`Kernel(ingress_streams=(...))` MUST validate declarations before engine reset.
Every real-time engine MUST have a stream binding. A stream can affect several
engines, and an engine can receive several streams. A submission MUST target a
partition in that stream's declared engine domain and use its pinned mapping.
Clock identity and rational conversion are checked by the existing mapping
contract. Stream declarations/mappings MUST remain immutable during the epoch.

Direct ingress dependencies are the streams bound to each engine. The coordinator
MUST propagate influence through declared field/relation reads, message routes
and returning command receipts, lifecycle notifications to writers/readers,
explicit sampled upstream cones, all partitions sharing an engine and atomic cohorts. The local
implementation conservatively includes positive-lag dependencies and lifecycle
controllers affecting writer/read domains in this union;
it does not infer a weaker watermark bound from a lag. Thus the safe closure for
partition `p` is `min(W[s] for s in influence[p])`, or unbounded when the declared
influence set is empty. Independent engines do not share an ingress dependency
merely because they occupy the same kernel. Hosts MUST declare all causal reads
and routes; undeclared Python global state remains outside the contract.

The existing `Kernel(ingress_policy=policy)` is an implicit `default` stream
covering all partitions, preserving shared-stream behavior and accepting stamps
from existing pinned mappings. Explicit named streams can coexist with this
compatibility stream but MUST NOT also declare its `default` ID. Without an
implicit policy, an explicit stream may itself be named `default`. There are no
implicit aliases between different IDs.

### 14.2 Inclusive closed prefixes and allowed lateness

Each stream owns a monotonic canonical integer closed-prefix watermark `W[s]`,
initially `policy.initial_watermark_ns`. Closure is inclusive: after closing `W`,
inputs mapped at or before `W` are late. An already reserved input remains queued
when a later assertion closes past its boundary; it MUST NOT be discarded.

**Timestamps alone are not a watermark.** Neither submission nor observation
implicitly advances any stream. `advance_watermark(ns, stream_id=...)` is a source
assertion that the canonical prefix through `ns` is closed. It MUST reject
regression, treat equality as a no-op, and subtract no lateness bound.

`advance_source_progress(stamp, stream_id=...)` is a separate, explicit source
assertion. Let `P` be the exact mapped progress and `L` the pinned allowance,
with absent allowance meaning no subtraction. The derived closure is exactly
`W = P - L`. Both `P` and the resulting closure MUST be monotonic. A derived
closure below the initial/current watermark MUST be rejected, without clamping;
an adapter starts asserting progress only once it can respect the bootstrap
closure. Equal repeated progress is a no-op. Explicit closure can move farther
than a prior progress assertion, but subsequent assertions must respect it.

Inputs in the open tail `(P-L, P]` remain admissible after the progress assertion,
subject to the committed-prefix rule below. The lower endpoint `P-L` is closed.
This deliberately models a *progress assertion with a lateness tail*, rather
than reopening a source-asserted closed watermark. A closed prefix cannot safely
allow later changes to earlier irreversible engine results. An adapter that
computes its own explicit `P-L` may call `advance_watermark` instead; the bound
MUST be subtracted once. A bound of zero closes through `P` immediately.

### 14.3 Admission, displacement and typed receipts

`submit_live(request, stamp, stream_id="default")` preserves the old command-ID
return and raises typed `KernelError("LATE_INGRESS", ...)` after an acknowledged
rejection. `admit_live(...)` returns an immutable `IngressReceipt` for each
acknowledged decision, including rejection, without translating it into execution
success. Malformed clocks, schemas or routes fail preflight without a receipt or
journal mutation. The SDK `LiveIngress(kernel, stream_id)` binds these calls to
one stream and explicitly forwards progress/closure assertions.

An input is late when `mapped_ns <= W[stream]` or it falls at/before the common
sealed prefix. `reject` MUST record disposition `rejected`, code `LATE_INGRESS`,
and no reservation ID/action/sequence. `delay` MUST preserve the original request
and source stamp and reserve at the next admissible integer publication boundary:

```
B = max(mapped_ns, current_journal_instant.ns + 1,
        W[stream] + 1 if late, requested_ingress_boundary if supplied)
A = max(requested_activation.ns, B)
```

The activation microstep is preserved only when activation ns is unchanged;
otherwise it is zero. Existing recipient lag, latching and actual dispatch remain
separate recorded decisions. On-time arrivals behind an unsealed partial journal
commit are still admissible with availability after the current journal instant;
their source occurrence is preserved and `delay_ns = B - mapped_ns` is explicit.
This does not claim retroactive influence on already committed engine results.
Disposition `delayed` specifically denotes the late-input policy decision;
`accepted` may have ordinary arrival/latching displacement.

Receipts MUST include `stream_id`, `disposition`, `journal_index`, `mapped_ns`,
`command_id`, `boundary_ns`, `activation_ns`, `delay_ns`, and `code`. Rejection has
no command/boundary/activation/delay coordinates. Every decision refers to its
actual acknowledged WAL index. `ingress_receipt(command_id)` returns the original
accepted/delayed receipt without rescanning the WAL. Keyed live idempotency is
scoped by `(stream_id, key)`: identical original request/stamp returns the original
receipt/index, including rejection; changed original content fails conflict.
The compatibility default preserves its old shared offline/live key-conflict
behavior. Named streams use distinct source sequence domains, retained in reservation and
published message records. A WAL failure exposes neither a receipt nor candidate
state. Replay reconstructs receipts from decisions rather than synthesizing them.

### 14.4 Partial local waves and common sealing

At a selected common physical boundary `T`, the coordinator MUST authorize only
partitions whose entire ingress influence is closed through `T`. A safe independent
engine may advance and process its ordinary/sample work at `T` while another
engine waits on its own streams. All partitions in one engine/cohort remain in
the same influence domain. Same-time dirty work for unsafe recipients remains
queued, and reservations for unsafe targets remain unpublished.

The global append-only journal retains nondecreasing Instants. The first physical
wave starts at `(T,0)`; after safe reactions, a waiting engine's physical catch-up
at the same `T` uses a later microstep, before its own external-input reactions.
No partition advances physically twice at `T`. Runtime and replay MUST validate
that each advance wave is exactly the canonical set of safe partitions still
behind `T`, and that reactions follow the recipient's physical catch-up.

Global `seal(T)` MUST wait until every partition reaches `T`, every relevant
stream closes through `T`, and all due ingress, timers, ordinary work and samples
settle. It MUST never pass any affected engine's minimum watermark. `run_until`
returns only at this common seal; views and acknowledged receipts can expose
partial committed progress while it waits. The scheduler retains common event
boundaries: it does not allow one independent island to skip ahead past a blocked
selected boundary. This is local early service within a central barrier, not a
distributed/asynchronous timeline or a per-engine native rollback facility.

Each blocked stream retains its own absolute `timeout_s` deadline through wakeups
and recomputation until common physical settlement. An already closed stream's
shorter timeout MUST NOT shorten another stream's wait. Expiry records a fault;
partial acknowledged work is retained and replay marks the run incomplete.
Pacing of early safe service uses only its influencing streams; replay never
waits or calls wall clocks/engines.

### 14.5 Journal 1.3 and explicit historical compatibility

Live v0.2 runs write `aerokernel.journal` major 1, minor 3. The exact header adds
`ingress_streams` to the 1.2 header and retains `ingress_policy` for the default
compatibility declaration. Complete policy encodings include the nullable
`allowed_lateness_ns` field. Header reconstruction MUST validate mappings,
engine domains, real-time coverage and propagated influence again.

* `watermark` adds `stream_id`; `watermark_ns` is the explicit inclusive closure.
* New `source_progress` records `stream_id`, original `source_stamp`, mapped
  `progress_ns`, and derived `watermark_ns`. Replay MUST recompute the mapping and
  subtraction and reject wrong mappings, nonmonotonic values or forged metadata.
* `live_ingress` and `live_ingress_rejection` add `stream_id`; the pinned policy,
  original input, mapped result, disposition and reservation/delay are reproduced
  by the same admission calculation on replay. Named reservations retain source.
* Existing intent/return/transaction/boundary/seal/fault records retain their
  shapes. Selected subsets and later-microstep physical catch-up are legal only
  under the 1.3 stream declarations and their computed safety constraints.

Journal 1.2 and 1.1 MUST replay unchanged: retain their exact header shapes,
single-stream watermark records, original policy field set, decision bytes and
whole physical-wave rules. The only accepted old `IngressPolicy` encoding omits
exactly the newly appended field; arbitrary missing fields remain invalid.
1.2 live records retain their original idempotency/decision interpretation.
New stream records under an old header and unsupported journal versions fail.
Historical replay performs no lateness reinterpretation, inferred progress,
wall-clock waits or engine execution.

## 15. v0.2 additive amendment: compact journals and sampled cause prefixes (K5)

This amendment is normative. Execution, evidence, ordering, atomic publication,
input authority and clock semantics remain those of sections 1–14. The new codec
is explicitly selected with `Journal(codec="positional-deflate")`; `"json"`
remains the default and continues to issue the existing 1.2/1.3 bytes. Existing
1.1, 1.2 and 1.3 journals remain admissible without rewriting.

### 15.1 Journal 2.0 admission and lossless encoding

The header is ordinary canonical JSON, with `major=2`, `minor=0`,
`codec="positional-deflate/v1"`, and `semantic_version=2` or `3`. The remaining
header fields are exactly the corresponding 1.2/1.3 declaration. Unknown codec,
semantic version, fields or versions MUST fail admission. A file cannot mix
legacy records and compressed frames. The serializer and resource policies stay
pinned in the header.

Every subsequent LF-terminated line has exactly `type`, `index`, `data`, and
optionally `phase`. `data` is standard padded base64 of a zlib stream, compression
level 1, containing UTF-8 positional JSON. The outer coordinates MUST match the
expanded record. Each frame is independent: no preceding compressor state,
engines, evaluators, RNG or clocks are needed to decode it.

The positional grammar is unambiguous for every portable value: scalars remain
scalars; lists are `[0, ...members]`; dictionaries are
`[1, [key, value], ...]` in sorted key order. Closed kernel record shapes use
`[2, type_name, ...fields]` in the fixed field order in `journal_codec._SHAPES`.
That table is part of this codec version and MUST NOT change when adding future
Python fields. Other dictionary shapes retain the dictionary grammar, including
payloads containing literal `$type`, `fields` or tag-like lists. No portable
payload is interpreted as a causal instruction. Positional records remove
repeated field names; DEFLATE references repeated byte sequences inside the
frame, including repeated notifications, messages and cause sequences. Every
value and every occurrence of a cause remains recoverable. The codec does not
introduce cross-record message or sampled-input delta references.

The encoded line and decompressed positional bytes MUST each fit `frame_bytes`.
Decompression MUST stop before exceeding that bound and reject unfinished or
concatenated streams. Expanded semantic trees MUST satisfy the declared integer,
Unicode, finite-value and nesting constraints. Positional parsing uses the
explicit derived depth ceiling `2 * nesting_depth + 2`; semantic expansion then
rechecks the declared depth. Canonical generation is deterministic for identical
inputs under the pinned serializer/compressor implementation; cross-runtime zlib
bitwise equivalence is not promised. Semantic replay checks type-sensitive
normalized equality and reproduces the same atomic effects as legacy replay.

### 15.2 Exact ordered frame-prefix causes

`FramePrefix(context_id, count, through)` denotes the first `count` committed
frames of that sampled context in publication order, ending at item coordinate
`through`. The empty prefix has `count=0, through=None`. Nonempty prefixes require
a typed boundary. `StateView.sample_frame_prefix(context_id, known_at=None)`
issues the exact prefix visible at the permitted knowledge cut, under the same
context read declarations as `sample_frames`.

Prefixes are admitted as proposal causes only under journal 2.0. A prefix MUST
reference a declared readable context, fit the invocation read cut, and have
exactly the recorded item at its stated boundary. Authority is checked once per
partition/prefix in a candidate. Expansion replaces each prefix in place with
all of its frame version ItemRefs; explicit causes before/after it keep their
positions. Repeated prefixes repeat their entire sequences. Expansion MUST NOT
sort, deduplicate, replace the sequence by a transitive graph, or omit unresolved
frames. Raw proposals retain their prefix; resolved effects retain the complete
expanded cause sequence. LocalCause rules remain unchanged. This implementation
therefore reduces authority checks but still materializes resolved causes;
constant-size resolved provenance requires a further representation amendment.

### 15.3 Indexed authority, streaming and committed file prefixes

Cause authority indexes retain only ownership, kind, declaration keys and
coordinates. They MUST be derived from validated effects, preserve all scope,
lag, dispatch and future-reference checks, and remain independent of the bounded
payload decode cache. Removing payloads from this index does not remove them from
the WAL or diagnostic/evidence access. Divergent candidate suffixes MUST NOT
reuse another candidate's authority. Sample histories use immutable issued
prefixes and monotonically indexed publication coordinates.

`journal.iter_records(bytes_or_path)` decodes one wire record at a time;
`compact.expand_record` expands its fact rows for projections. This iterator
checks framing/value/codec validity; `journal.replay` additionally validates all
transaction semantics. `Kernel.iter_records()` yields detached semantic records
from the committed prefix captured when iteration starts. Path replay MUST stream
rather than first loading or decoding the entire journal. Recovery still discards
only the unterminated final line, never a corrupt complete line.

File-backed RecordLog prefixes reference acknowledged byte ranges and pin their
hashes; subsequent appends cannot expose an unacknowledged suffix. Uncached reads of changed
committed ranges MUST fail; decoded cache entries retain acknowledged values. The backing journal must remain available while
issued views are used. WAL flush/fsync precedes visibility as before, and the
metadata/file indexes are not independently authoritative journal records.

## 16. v0.3 amendment: lean invocation provenance (K7)

`Kernel(provenance="lean"|"full")` MUST default to `lean`. The choice is
pinned when binding and MUST be admitted explicitly from the journal header.
This amendment changes audit detail; it does not change engine-visible state,
time coordination, time seals, writer authority, message ordering, receipt
transitions, ingress watermarks, or WAL-before-publication semantics.

### 16.1 Lean and full contracts

In lean mode each engine operation's resolved cause sequence MUST contain only
the ItemRef of its invocation intent. Resolved emitted messages and sampled
frames MUST use that same cause. The intent retains its exact read cut, native
input cut, grant, delivered messages, dirty notifications and dispatch identities.
External inputs retain their reservation/publication identity and original stamp;
kernel-generated routing/timer records retain their operational references.
Lean provenance identifies which invocation committed an item; it MUST NOT be
presented as a list of the fields actually read by the engine.

The live index MUST retain pending intents and compact operation mappings for
each partition's latest returned invocation per phase (bootstrap, advance, react,
sample), without returned inbox/read trees. `StateView.committed_operation`
and RPC `committed_operations` MUST resolve those own committed local proposals
with ownership and transaction-base-cut checks. Own committed publications
MAY be resolved and cited before a global seal catches up; this MUST NOT widen
native observation cuts or declared state/dispatch cause scope. RPC projections
MUST include the same own-publication mappings independently of their read cut.
Engines MUST resolve a local
proposal during their next callback of that phase and retain its resulting
ItemRef if needed later. Superseded mappings are absent (`CAUSE_UNKNOWN`);
previously issued views retain their immutable mappings. Full mode retains all
returned invocations. Historical invocation inputs MUST remain available through
the immutable WAL. Lean publication and replay MAY
serialize already admitted payloads and kernel-generated metadata directly,
without another normalization copy. Payload nesting/integer/value budgets remain
checked at admission; newly issued coordinates, stamps and intervals are checked
once. Lean wire-record nesting has a framing allowance of `2*nesting_depth+2`,
while decoded payloads MUST still satisfy the original payload budget. Both
expanded and physical byte limits remain enforced. Arbitrary public
`Journal.append` calls retain normal validation unless the kernel supplies its
internal trusted-record flag.

Engine-supplied proposal `causes` MUST be discarded before normalization,
encoding or per-reference validation, including ItemRef, LocalCause and
FramePrefix citations. Recorded proposals carry an empty `causes` sequence.
The SDK MUST avoid constructing read vectors in lean mode; `StateView.provenance`
and RPC view projections expose the pinned choice. Actual field/frame reads MUST
still enforce declared scope, lag and the permitted read cut.

Receipt, feedback and cancel decisions MUST retain target ownership, an actual
recorded command/cancel dispatch, legal current-state transitions, cancellation
correlation and typed payload/result validation. Lean decisions use the kernel's
current action head and dispatch index instead of requiring their repetition in
proposal causes. `Message.origin`, command IDs, dispatch IDs and receipt heads
remain operational identities. `Remove.cleanup_refs` is likewise operational
authorization: actual participant acknowledgments and their visibility/order
MUST still be checked; it is not discarded with audit `causes`.

Full mode MUST retain the previous ordered explicit-vector validation, expansion
and encoding, including duplicates and prefix expansion. Its existing journal
1.x/2.0 headers and bytes MUST remain unchanged on the pinned runtime. The
provenance requirements in earlier sections apply to full mode unless this
section explicitly retains them for lean mode.

### 16.2 Journal admission and replay

Lean JSON journals use version 1.4; lean compressed journals use version 2.1,
`semantic_version:4`, and `codec:"canonical-deflate/v1"`. Both MUST declare
`provenance:"lean"` and integer `policy_version:2|3`, selecting the existing
offline/single-stream or named-stream ingress record rules. A 2.1 frame contains
one independently zlib-compressed canonical JSON record, base64 encoded in the
existing type/index/optional-phase envelope. Compression level is 6. Expanded
and physical frame budgets remain enforced; there is no positional tree or
cross-frame dictionary. Version 2.0 retains `positional-deflate/v1` unchanged.
Unsupported or malformed headers MUST fail; absent provenance in a supported
historical 1.x/2.0 header means full audit semantics, not the new default.

Engine-free replay MUST reproduce committed state history, messages, receipts
and time using the admitted provenance level. Re-execution with identical pinned
engines, inputs and run-control schedule MUST produce identical bytes for that
level and codec. Lean and full runs need not have identical journal bytes or
audit causes. Lean mode requires none of K6's segmented cause sequences,
cross-record payload references or payload-node storage.


### Release clarification: concurrent control requests

In-process public mutations MUST serialize guard checks, candidate construction
and WAL publication under the coordinator's reentrant condition lock, including
`bind`, `start`, `submit` and `cancel`. Watermark waits and pacing MAY release
that lock so ingress/control requests can progress; after waking, coordination
MUST recompute from the resulting committed cut. Control requests that add work
MUST notify waiting coordination.
