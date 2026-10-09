# AeroGraph predicates and events

The `predicate` engine executes canonical AeroGraph definitions over settled kernel state. It is independent of any application domain. `threshold` is unchanged. This revision deliberately implements a finite dialect; an unsupported expression fails scenario load with its target ID and construct name.

## Census and source pin

The checkout inspected on 2026-10-08 contains **5,530 predicates, 3,927 events and 6,506 rules** in the persisted `semantic-directory/index.html` JSON payload. The task's **1,686 predicates / 965 events** are the preserved original-graph subset, which remains present. Additional expanded, capability and pilot definitions are counted separately below. No AeroGraph build, import of its builder, or Git operation was used. The census reads the existing artifact, not generated demonstration values.

| Native dialect | Predicates | Events |
|---|---:|---:|
| Original compact AST | 1,686 | 965 |
| Expanded typed AST | 3,824 | 2,962 |
| Capability typed AST | 13 | 0 |
| Early semantic typed AST | 7 | 0 |
| Total | 5,530 | 3,927 |

Canonical format is `aerograph.predicate-definition/v1`. Leaves contain `field/role/path`, `literal`, scoped `parameter`, directed `relation/sourceRole/targetRole`, `var/path` or `time`. Operators carry `op` and `args`; quantifiers carry `array`, `var`, `predicate` and an optional applicability expression. Canonical rule references execute the referenced definition; inline rule references execute their own embedded expression and retain the label as provenance. Normalization preserves `le → lte`, `ge → gte`, `holds → hold`, temporal `asScope`, parameter occurrence IDs and source defaults. Defaults are provenance and are **not supplied by this engine**.

The table counts **definitions containing each construct anywhere in their executable rule closure or applicability AST**. Rows overlap; they are not a partition or AST-node totals. Expanded transition events include the condition of their referenced contract in the closure. `P/E` are current total predicate/event incidences; `original P/E` restrict the same calculation to preserved original targets. “Yes” enumerates vocabulary, subject to the binding and admission restrictions below.

| Construct | Family | P | E | Original P | Original E | Dialect support |
|---|---|---:|---:|---:|---:|---|
| `abs` | Arithmetic | 248 | 232 | 223 | 223 | Both |
| `add` | Arithmetic | 259 | 251 | 237 | 233 | Both |
| `all` | Quantification / structure | 20 | 19 | 0 | 0 | Expanded |
| `all_window` | Temporal | 0 | 34 | 0 | 34 | Original |
| `and` | Boolean | 1808 | 1753 | 728 | 765 | Both |
| `any` | Quantification / structure | 698 | 533 | 0 | 0 | Expanded |
| `any_window` | Temporal | 0 | 72 | 0 | 72 | Original |
| `between` | Comparison / membership | 7 | 1 | 7 | 1 | Original |
| `boundary_crossing_time` | Geometry / domain function | 1 | 1 | 1 | 1 | Rejected |
| `changed` | Temporal | 0 | 1 | 0 | 1 | Original |
| `contains` | Comparison / membership | 17 | 14 | 0 | 0 | Expanded |
| `count` | Aggregation | 13 | 10 | 0 | 0 | Expanded |
| `count_window` | Temporal | 1 | 1 | 1 | 1 | Original |
| `cross` | Vector arithmetic | 1 | 0 | 0 | 0 | Expanded |
| `dag_longest_path` | Aggregation | 3 | 3 | 3 | 3 | Rejected |
| `date_time` | Clock / interval | 12 | 12 | 0 | 0 | Expanded |
| `delta` | Temporal | 84 | 39 | 84 | 39 | Original |
| `disjoint` | Comparison / membership | 3 | 4 | 3 | 4 | Both |
| `distance` | Vector arithmetic | 13 | 7 | 13 | 7 | Original |
| `div` | Arithmetic | 111 | 73 | 104 | 69 | Both |
| `dot` | Vector arithmetic | 7 | 3 | 2 | 1 | Both |
| `duration_fraction` | Temporal | 0 | 5 | 0 | 5 | Rejected |
| `episode_active` | Temporal | 0 | 300 | 0 | 300 | Rejected |
| `eq` | Comparison / membership | 3729 | 2683 | 745 | 509 | Both |
| `fall` | Temporal | 0 | 77 | 0 | 77 | Original |
| `field` | Typed leaf | 5526 | 3927 | 1685 | 965 | Both |
| `geometry_collection_clear` | Geometry / domain function | 1 | 1 | 1 | 1 | Rejected |
| `geometry_coverage_complete` | Geometry / domain function | 1 | 0 | 1 | 0 | Rejected |
| `geometry_envelope_intersects` | Geometry / domain function | 1 | 0 | 1 | 0 | Rejected |
| `geometry_observation_intersects` | Geometry / domain function | 1 | 0 | 1 | 0 | Rejected |
| `geometry_relation` | Geometry / domain function | 3 | 0 | 3 | 0 | Rejected |
| `gt` | Comparison / membership | 746 | 481 | 713 | 456 | Both |
| `gte` | Comparison / membership | 1275 | 1214 | 364 | 355 | Both |
| `hold` | Temporal | 7 | 102 | 7 | 102 | Original |
| `if` | Boolean | 365 | 438 | 365 | 438 | Original |
| `implies` | Boolean | 1 | 1 | 0 | 0 | Expanded |
| `in` | Comparison / membership | 122 | 298 | 122 | 298 | Original |
| `interval_contains` | Clock / interval | 45 | 45 | 0 | 0 | Expanded |
| `is_unknown` | Presence / unknown | 2 | 115 | 2 | 115 | Original |
| `len` | Aggregation | 27 | 125 | 27 | 125 | Original |
| `literal` | Typed leaf | 3260 | 3058 | 1014 | 922 | Both |
| `lt` | Comparison / membership | 335 | 251 | 303 | 227 | Both |
| `lte` | Comparison / membership | 1303 | 1213 | 372 | 353 | Both |
| `mahalanobis` | Aggregation | 1 | 1 | 1 | 1 | Rejected |
| `max` | Arithmetic | 14 | 5 | 5 | 3 | Both |
| `max_consecutive_interval` | Geometry / domain function | 1 | 1 | 1 | 1 | Rejected |
| `min` | Arithmetic | 3 | 1 | 2 | 1 | Both |
| `mul` | Arithmetic | 68 | 47 | 44 | 36 | Both |
| `ne` | Comparison / membership | 49 | 147 | 44 | 142 | Both |
| `norm` | Vector arithmetic | 11 | 21 | 11 | 21 | Original |
| `not` | Boolean | 196 | 386 | 192 | 384 | Both |
| `or` | Boolean | 179 | 168 | 82 | 78 | Both |
| `ordered_sequence` | Temporal | 0 | 11 | 0 | 11 | Rejected |
| `parameter` | Typed leaf | 2492 | 1293 | 781 | 387 | Both |
| `polygon_signed_distance` | Geometry / domain function | 3 | 7 | 3 | 7 | Rejected |
| `pow` | Arithmetic | 9 | 4 | 2 | 2 | Both |
| `rate` | Temporal | 19 | 11 | 19 | 11 | Original |
| `record_count` | Aggregation | 1 | 1 | 1 | 1 | Rejected |
| `relation` | Directed relation existence | 67 | 56 | 0 | 0 | Expanded |
| `required_envelope_compliant` | Geometry / domain function | 2 | 3 | 2 | 3 | Rejected |
| `rise` | Temporal | 0 | 351 | 0 | 351 | Original |
| `robust_slope` | Aggregation | 2 | 2 | 2 | 2 | Rejected |
| `same_identity` | Full reference identity | 78 | 74 | 0 | 0 | Expanded |
| `sequence` | Quantification / structure | 0 | 331 | 0 | 331 | Temporal scope only |
| `set_equal` | Comparison / membership | 17 | 39 | 17 | 39 | Original |
| `sqrt` | Arithmetic | 9 | 7 | 8 | 6 | Both |
| `stable_window` | Temporal | 0 | 231 | 0 | 231 | Original |
| `sub` | Arithmetic | 362 | 316 | 322 | 289 | Both |
| `subset` | Comparison / membership | 33 | 151 | 21 | 139 | Both |
| `sum` | Aggregation | 1 | 1 | 1 | 1 | Original |
| `swept_envelopes_overlap` | Geometry / domain function | 2 | 4 | 2 | 4 | Rejected |
| `swept_polygon_intersection` | Geometry / domain function | 1 | 2 | 1 | 2 | Rejected |
| `time` | Typed leaf | 55 | 55 | 0 | 0 | Expanded |
| `transition_event` | Temporal | 0 | 2962 | 0 | 0 | Expanded entered profile |
| `unique_count` | Aggregation | 14 | 113 | 14 | 113 | Original |
| `var` | Typed leaf | 716 | 550 | 0 | 0 | Expanded |
| `vector_norm` | Vector arithmetic | 16 | 12 | 0 | 0 | Expanded |
| `volumes_overlap_4d` | Geometry / domain function | 1 | 4 | 1 | 4 | Rejected |

The inspected ASTs contain **no arbitrary embedded JavaScript/external function calls, unit-conversion nodes, frame-conversion nodes, graph traversal/cardinality nodes, or `within`/`since` nodes**. Their count is zero; those constructs are rejected, not inferred from a name. Native runtimes implement geometry, statistical functions and episode/sequence operators, but this plugin rejects those listed as rejected above. Relation existence uses a complete, directed committed edge set. Array counts/set operations are not graph traversal or cardinality approximations.

**Applicability admission is explicitly unsupported**: 224 current predicates and 204 current events declare a top-level applicability AST; the referenced contracts of expanded transition events also carry it. Original applicability reports scope separately, while expanded guards gate execution. Treating both as `and` would change their semantics. This revision rejects either with `construct applicability`; quantifier item applicability remains supported with expanded native rules. After this restriction and unsupported operators, the enumerated syntax admits **5,288 / 5,530 predicates (95.62%)** and **3,402 / 3,927 events (86.63%)**. These are syntax/admission counts, not claims that all instances, parameters or physical inputs have been bound or executed. There are 27 event definitions with computed duration operands; five were otherwise admissible and are excluded here (the other 22 already fail another restriction). Thirty real targets have golden execution tests.

Reproduce the census from the worktree (large JSON output stays in scratch):

```bash
PYTHONPATH=src /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
  tools/predicate_census.py /mnt/data2/weizhiwei/AeroGraph \
  --out /tmp/aas-q/q6/current-census.json
```

| Input | SHA-256 |
|---|---|
| `semantic-directory/index.html` | `d8beba119f20d59f9a84745a1d72c35434c459ccdb64e88332e1120da2da7f16` |
| `semantic-directory/src/original_runtime.js` | `6e1f60e885208b333fb20d69d24b7c49e9a14aec7c8578917b0b1d30dc2a10e6` |
| `semantic-directory/src/expanded_runtime.js` | `3780dc2f3d6fc7d7e01850595d10f60d98f378f9036e8722e4c7d820bd75f869` |
| `semantic-directory/src/predicate_format.py` | `5227f29d5ac900f54c56de60154e26373db903ad4ea793b82016406d8b506fbf` |
| `research/original-graph/decoded.json` | `de89b3b29f0a718f6a45b995caca2bbe78302ea37d8a9a705d521623ffcea3c1` |

## Plugin binding and execution

The `aeroagentsim.engines` entry point is `predicate = aeroagentsim.engines.predicate:build`; source development also resolves it through the built-in catalog. Configuration declares `version: aerograph-predicate/1`, a canonical definition closure in `definitions`, the selected `target` key, `context`, explicit `parameters`, `temporal_unit: s` or `ns`, a typed event schema/topic, and `transition: entered`, `exited` or `level`. The supported AST dialect is validated at load. Definition and native source hashes are no longer required or checked.

A matching `bindings.samples` entry pins role → full entity reference, role → source partition, role → native clock/mapping pair, parameters, triggers and the complete upstream cone. Canonical owner aliases describe producers; they never select an instance implicitly. `field_roles` and `relation_roles` explicitly bind source leaves whose original aliases are absent or require a selected instance profile. Relation inputs additionally require `relation_profiles: {relation-id: {source: writer-partition, clock: [clock-id, mapping-id]}}`, independent of the producer of the endpoint's fields. Directed endpoint types are checked against registry descriptors. Declared lifecycle reads verify the selected generations are alive; removal yields required input rather than a false relation. Original compact AST admits no field path, time, variable or relation leaf; those leaves are accepted only by the expanded dialect. `sequence` is admitted only inside temporal scope.

Each invocation reads `StateView.field(ref, ctx.now, ctx.view.cut)` through declared dependencies and checks the actual fact producer and acquisition clock/mapping. It retains the exact fact version as a cause. Relations come from declared kernel relation queries: acquisition provenance is checked on selected edges; assertions/closures and empty graph evidence retain the dispatched relation/lifecycle causes. The complete authoritative query is allowed to return an empty set. An unprovided observational edge set is never substituted with an empty set.

One `SampleFrame` records the input values, target status/value, diagnostics, permitted knowledge cut, exact physical ns, pinned roles/sources/clocks and causes. Temporal evaluation reads those committed frames **at their original knowledge cuts**; later corrections do not rewrite earlier observations. Successful current fact reads, actual dirty/timer causes, and consulted historical frame versions become event causes. A missing/expired/retracted field is not zero or false; a source/clock mismatch is recorded as `invalid_input`, and unavailable history as `required_input`. Neither produces an event. A missing frame breaks the entered/exited baseline. Arbitrary casts, truthiness and unit/frame conversions are absent. Event payloads are validated against the configured kernel event schema.

Original Boolean operators retain their native Kleene value rules; expanded operators propagate required inputs, with the native short-circuit rule for implication. The engine additionally requires its selected inputs to be available and source-valid before publishing a known target; definitive primitive Boolean values do not hide missing dependencies. JSON number equality treats 1 and 1.0 alike, separates Booleans from numbers, and compares records independent of key order. Vector hypot uses V8's scaled compensated algorithm rather than Python's differently rounded `hypot`.

## Temporal contract

Durations and maximum gaps must be explicit literal/scoped-parameter operands. `s` values convert with decimal exactness to integer ns; fractional ns are rejected. There are no computed-duration expressions. Source clocks are checked and mapped by the kernel; the evaluator does not invent a clock conversion. Scalar field timestamps retain their declared representation; the native time leaf and rate output preserve the explicitly selected source time unit.

For `hold`/`all_window`, locate the **latest committed frame at or before `now - duration`**, and evaluate the condition from that anchor through now. A complete window containing only true Boolean values is true; false disproves it; missing coverage/input remains undetermined. This matches AeroGraph's sampled, piecewise-held window semantics. It is not an interpolated or continuous sensor trajectory. Kernel fact validity and expiry notifications govern the availability of each sampled input. A declared maximum observation gap or a change/unknown in `asScope` invalidates the window. `any_window`, `count_window`, `stable_window`, `delta` and `rate` use the corresponding native anchor/count rules. Rate and delta are evaluated at actual declared frames; the plugin does not claim to solve continuous crossings between frames.

`rise`/`entered` and `fall`/`exited` compare the preceding real frame with the current Boolean condition, requiring the same scoped identity and permitted gap. The first frame cannot manufacture a false baseline. Canonical expanded `transition_event` is admitted only for its exact entered profile, same-role/same-clock/previous-frame flags and one matching contract rule. Its condition is evaluated in both frames; configure `transition: level` to emit the already computed event pulse once per sampled frame. Predicates may instead use the explicitly selected entered/exited/level envelope. Level emission means each real known-true frame, not a deduplicated transition.

Window operators schedule exact one-shot deadlines after initial input or input changes; timer-only frames do not restart a polling chain. The demo's real `hu.predicate.actor_stationary` uses a three-second native hold. Its authored observation becomes stationary at 1 s: the event fires at **4,000,000,000 ns**, with no event at **3,999,999,999 ns**. Observation values here are explicitly authored simulation data, not camera measurements. The exact integer-time extension is also tested above JavaScript Number's safe integer range; that boundary test uses the documented anchor rule as the reference because the native Number clock cannot represent those ns.

## Evidence and replay

`tests/platform/test_predicate_differential.py` copies the pinned `original_runtime.js` and `expanded_runtime.js` into pytest scratch and invokes them with Node. No upstream script executes in the upstream tree. The bridge translates representations; native code computes scalar, Boolean, quantified, relation and temporal results. Hypothesis generates histories and compares every supported operator family, including scope changes and maximum gaps. Canonical expanded entered events are separately compared against native event evaluation. This is a native differential oracle, not an invented Python reference for ordinary constructs.

Thirty real AeroGraph predicates have golden tests, including the 25 distinct predicates with preserved `model.X` samples, a real temporal predicate, a real early typed relation predicate with explicit directed instance bindings, and real physical/environment/EM predicates. Their canonical domains span seven labels (including the cross-domain information view); the source fixture JSON carries IDs, pointers and input provenance. Newly authored test histories are explicitly distinguished from preserved source examples. Golden tests compare native output and the stated expected value.

`tests/platform/test_predicate.py` verifies scenario-load rejection, definition/runtime pins, source mismatches, committed directed assertion/closure evidence, exact bitemporal input versions/causes, event deadlines, identical journal bytes and replayed frames. The replay reads the WAL and does not rerun a predicate evaluator.

Run the example:

```bash
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph
PYTHONPATH=src /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/aeroagentsim \
  run scenarios/predicates-demo.yaml --out /tmp/aas-q/q6/demo-runs
```

The AST closure is additive engine configuration recorded with the scenario. Snapshot round trips and selection-order stability remain tested; snapshot byte hashes are no longer pinned.

## Validation record (2026-10-08)

Commands ran from the Q6 worktree. No frontend files changed; frontend checks and Docker tests were not run. Four Docker-marked cases were deselected by the requested marker. Large run artifacts and copied native sources remain under `/tmp/aas-q/q6/`.

```bash
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph
export PYTHONPATH=src
export MYPYPATH=../aerokernel
PY=/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python

$PY -m ruff check src/aeroagentsim/engines/predicate.py src/aeroagentsim/engines/predicate_ast.py src/aeroagentsim/platform/plugins.py src/aeroagentsim/scenario/loader.py tools/predicate_census.py tests/platform/test_predicate.py tests/platform/test_predicate_differential.py
$PY -m mypy --strict src/aeroagentsim/engines/predicate.py src/aeroagentsim/engines/predicate_ast.py src/aeroagentsim/platform/plugins.py src/aeroagentsim/scenario/loader.py tools/predicate_census.py tests/platform/test_predicate.py tests/platform/test_predicate_differential.py

$PY -m pytest -q -p no:cacheprovider -m 'not docker' tests/platform tests/adapters tests/agents tests/packs tests/authoring tests/integrations --basetemp=/tmp/aas-q/q6/pytest-regression-final
$PY -m pytest -q -p no:cacheprovider -m 'not docker' tests/platform/test_predicate.py tests/platform/test_predicate_differential.py tests/platform/test_threshold_general_review.py tests/platform/test_native_threshold.py --basetemp=/tmp/aas-q/q6/pytest-predicate-release
$PY -m pytest -q -p no:cacheprovider -m 'not docker' tests/platform/test_predicate.py --basetemp=/tmp/aas-q/q6/pytest-lifecycle-final
```

Ruff passed. Strict mypy passed on all seven touched Python files; Ruff format checking passed on the same files. The full suite passed **468 tests, with 4 deselected**, in 740.33 s, including the live LLM adapter test. After the final boundary cases and normalization changes, the focused suite passed **26 tests** in 60.50 s. The broad suite preceded the final dialect-leaf/lifecycle tightening; the focused suite checks those implementation changes. The final predicate test file, including explicit non-native leaf rejection and entity removal, passed **15 tests** in 63.97 s. Its first removal test run exposed a test-writer issuing removal again on the returned lifecycle notification; reading the committed life prevents the repeated operation. An earlier broad run failed two new tests (event cause projection and V8 hypot rounding); both were corrected before the passing runs above.

The CLI run command above completed seven simulated seconds. The actual replay command was:

```bash
PYTHONPATH=src /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/aeroagentsim \
  replay /tmp/aas-q/q6/demo-runs/predicates-demo-c468bb44a219
```

Replay reported `cut: 51`, `ns: 7000000000`, `incomplete: false`.

Independent headless DSH sessions used the requested `workbuddy/glm-5.3-flash` profile with a 131072 output budget and no effort setting. Each session owned a separate scratch directory. Evaluator and second-review sessions exited with `model stopped: error`; the census session did not finish a deliverable, and the native bridge session still had one failing self-test. The two remaining sessions were stopped after inspecting their artifacts. No unverified GLM code was integrated; the delivered evaluator, census and native differential bridge were independently implemented and checked by the primary agent.

## Incremental sampled evaluation (Q6b, 2026-10-09)

`SampleHistory` caches inputs when its owning context's committed prefix advances;
steady callbacks never thaw preceding frames again. A cold start or an invocation
that did not commit reconstructs from real committed frames. Level expressions
retain only the current input; the previous frame result remains separately
available for the existing event envelope and change-triggered timers. Frames,
truth/unknown/invalid-input diagnostics, sample profiles and publication cadence
are unchanged. `predicate_ast.py` and its authored source pin are unchanged; no
behaviour adapter change is required for A's concurrent dispatch work.

The reach calculation retains the last frame at/before a window boundary and
recurses from that **actual anchor** through nested windows/scopes. Pairwise
operators retain their predecessor. It preserves incomplete startup coverage,
boundary inclusivity, maximum gaps, scope changes and `count_window`'s distinct
count range. Sparse histories can require old anchors: the bound is semantic,
not an arbitrary frame-count cap. Within each retained window the original
native-compatible evaluator remains the oracle; no continuous interpolation or
new temporal operators are introduced.

Console integration must opt in with `Journal(..., codec="positional-deflate")`;
Q6b does not change RunSession/default journal construction. History *data* is
trimmed, but legacy evidence remains complete. Journal 2.0
uses `sample_frame_prefix(context_id)` in the original cause position. Raw frame
causes preserve duplicates; SDK event/timer causes preserve the original first
occurrence order even if an explicit input overlaps the prefix. Journal 1.x
retains its explicit cause list. K5 has no public StateView codec capability or
indexed frame-membership query; the local plugin reads its Store's authoritative
`allow_frame_prefix` flag and indexed frame coordinates after the public prefix
access check. It neither changes kernel state nor discovers support by attempting
an invalid publication. This in-process compatibility access is specific to K5.

The Hypothesis differential compares every sampled prefix against full-history
truth, diagnostics and level/entered/exited emissions, including nested windows,
missing values, irregular spacing, scopes and maximum gaps. Existing native-JS
and real-predicate oracle cases also execute the bounded path. A real dual-codec
scenario compares every record after cause expansion; another test covers SDK
prefix/explicit overlap and raw duplicate preservation.

Measured results (same full 63-road-vehicle/8-UAV scenario; output under
`/tmp/aas-q/q6b/`; benchmark wrappers change only the codec and reference callback):

| Workload | Before | Q6b |
|---|---:|---:|
| Level, 1,350 samples: input thaw + AST evaluation | 14.726 ms/evaluation | 10.693 µs/evaluation; 1 input retained |
| 3 s hold, 1,350 samples at 15 Hz | 16.546 ms/evaluation | 205.712 µs/evaluation; 46 inputs retained |
| Full scene, 3 s at 15 Hz, sampled callbacks (182) | 0.286 s | 0.170 s |
| Full scene, 90 s at §12's 1 Hz, sampled callbacks (364) | 1.482 s | 0.245 s |
| Full scene, 30 s at 15 Hz, sampled callbacks (2,608) | 37.831 s, previously recorded by K5 | 6.692 s, measured here |

Microbenchmarks exclude kernel/SDK overhead. The 30 s historical baseline was not
rerun and predates K5's final authority-sharing change; the 3 s and 90 s callback
comparisons use a saved pre-change `Predicate` implementation with the same
current compiler inputs, adapter and kernel. An initial benchmark failed to
replace the adapter's inherited implementation and was discarded as a baseline.
The measured runs overlapped other verification work; they are observations,
not isolated hardware limits.

Q6b's 30 s run took 508.748 s (RTF 0.05897), with 117.723 s total callbacks,
including 98.621 s behaviour reactions, a 48.990 MB journal and 1428.082 MiB peak
RSS. **The joint live targets are not met by Q6b alone.** A's dispatch changes
are not present here. K5 still expands resolved historical cause lists; input
caching and raw prefixes do not remove that kernel storage cost. A full 30 s
replay performance measurement is not claimed.

Streaming expansion proves all **507 records** of the 3 s/15 Hz prefix and all
**3,980 records** of the complete 90 s/1 Hz chain are equal to the saved reference
callback run, including ordered causes, frame results, diagnostics, physical
facts, events and capture results. The same current compiler/provenance inputs
are used on both sides; this isolates the callback change. The complete chain
also matches §12's timer timeline (activation 8 s, detection 13 s, award
15.066666667 s, capture 49.066666667 s, acceptance 49.266666667 s).


Two independent DSH GLM audits completed with exit 0 and separate scratch-only
ownership. Their native-anchor and SDK overlap findings were checked against
source and tests; set-only cause comparison and a blanket extra-sentinel rule
were rejected. Model/profile: `workbuddy/glm-5.3-flash`, 131072 output budget,
no effort parameter. Reports are in `native-audit/` and `evidence-audit/` under
`/tmp/aas-q/q6b/`.

Q6b final gates ran once from `wt-q6b`:

```bash
export AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph
export PYTHONPATH=src
export MYPYPATH=../aerokernel
PY=/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python
$PY -m ruff check src/aeroagentsim/engines/predicate.py tests/platform/test_predicate_differential.py tests/platform/test_predicate_incremental.py
$PY -m mypy --strict src/aeroagentsim/engines/predicate.py tests/platform/test_predicate_differential.py tests/platform/test_predicate_incremental.py
$PY -m pytest -q -p no:cacheprovider -m 'not docker' tests/platform/test_predicate*.py tests/behaviours tests/demos --basetemp=/tmp/aas-q/q6b/pytest-gates
```

Ruff and strict mypy passed on the three touched Python files. The agreed test
suite passed **101 tests in 684.42 s**, including both full demo end-to-end cases
and zero-call replay. No cases were skipped or deselected. No Docker/frontend
checks ran because neither was touched. The prior targeted runs passed three
native/window/deadline tests, two evidence tests and one randomized
truth/diagnostics/emission test. The first evidence run failed because its test
forgot to pass the selected codec to Journal; the corrected target and the final
gate passed. No Git commit was made.

Measurement/equivalence commands used the saved reference implementation and
scratch-only wrappers (no wrapper or copied source is a product dependency):

```bash
$PY /tmp/aas-q/q6b/perf_run.py --baseline --hz 15 --seconds 3 --out /tmp/aas-q/q6b/before-3s
$PY /tmp/aas-q/q6b/perf_run.py --hz 15 --seconds 3 --out /tmp/aas-q/q6b/after-3s
$PY /tmp/aas-q/q6b/perf_run.py --hz 15 --seconds 30 --out /tmp/aas-q/q6b/after-30s
$PY /tmp/aas-q/q6b/perf_run.py --baseline --hz 1 --seconds 90 --out /tmp/aas-q/q6b/before-90s
$PY /tmp/aas-q/q6b/perf_run.py --hz 1 --seconds 90 --out /tmp/aas-q/q6b/after-90s
$PY /tmp/aas-q/q6b/microbench.py
$PY /tmp/aas-q/q6b/compare_records.py /tmp/aas-q/q6b/before-3s/journal.jsonl /tmp/aas-q/q6b/after-3s/journal.jsonl
$PY /tmp/aas-q/q6b/compare_records.py /tmp/aas-q/q6b/before-90s/journal.jsonl /tmp/aas-q/q6b/after-90s/journal.jsonl
$PY /tmp/aas-q/q6b/timeline_check.py
```
