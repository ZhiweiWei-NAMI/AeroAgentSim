# Task A verification

Workspace: `/mnt/data2/weizhiwei/aeroagentsim/wt-a`, base `ee86d31`.
No commit, branch, reset or checkout was performed. No frontend files changed.
Large logs and run artifacts are under `/tmp/aas-q/a/`.

## Commands and results

These commands use this worktree's sources. The native-source bundle at
`/tmp/aas-q/b/ontology` was read only; scenario/Q6 admission verifies its pinned
source digests. The upstream AeroGraph worktree was not built or modified.

```bash
task_python=/mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python
task_changed=(
  src/aeroagentsim/behaviours
  src/aeroagentsim/engines/behaviour.py
  src/aeroagentsim/engines/workflow.py
  src/aeroagentsim/engines/threshold.py
  src/aeroagentsim/platform/plugins.py
  src/aeroagentsim/platform/simulation.py
  src/aeroagentsim/scenario/loader.py
  src/aeroagentsim/services/worker.py
  src/aeroagentsim/services/app.py
  src/aeroagentsim/services/projector.py
  tests/behaviours
)
PYTHONPATH=src "$task_python" -m ruff check "${task_changed[@]}"
PYTHONPATH=src MYPYPATH=../aerokernel "$task_python" -m mypy --strict "${task_changed[@]}"
```

Both checks passed; strict mypy checked **22 source files**. `git diff --check`
also passed.

Final new-runtime test command:

```bash
PYTHONPATH=src MYPYPATH=../aerokernel \
AEROAGENTSIM_AEROGRAPH_ROOT=/tmp/aas-q/b/ontology \
HYPOTHESIS_STORAGE_DIRECTORY=/tmp/aas-q/a/hypothesis \
"$task_python" -m pytest -q -p no:cacheprovider -m 'not docker' \
  --basetemp=/tmp/aas-q/a/pytest-final-release tests/behaviours
```

Result: **53 passed in 27.66 seconds**, no skips or deselections. Log: `/tmp/aas-q/a/final-behaviour-tests.txt`.

The full affected regression command was executed twice:

```bash
PYTHONPATH=src AEROAGENTSIM_AEROGRAPH_ROOT=/tmp/aas-q/b/ontology \
"$task_python" -m pytest -q -p no:cacheprovider -m 'not docker' \
  tests/platform tests/adapters tests/agents tests/packs tests/authoring
```

The first run passed **445 tests**, with **4 Docker tests deselected**. The later
run passed **444**, with **1 failure and 4 deselected**: the legacy feed test
required the exact original commit key set. The projector initially put empty
extension arrays on every commit. It now emits each optional array only when
that commit contains corresponding recorded events, preserving legacy shapes.
The failing test was rerun after repair:

```bash
PYTHONPATH=src AEROAGENTSIM_AEROGRAPH_ROOT=/tmp/aas-q/b/ontology \
"$task_python" -m pytest -q -p no:cacheprovider \
  tests/platform/test_platform.py::test_projector_contract_and_lossless_times
```

Result: **1 passed**. The complete broad suite was not rerun a third time; its
other 444 selected tests passed, and the repaired regression plus the required
compatibility suites passed in the subsequent combined gate:

```bash
PYTHONPATH=src MYPYPATH=../aerokernel \
AEROAGENTSIM_AEROGRAPH_ROOT=/tmp/aas-q/b/ontology \
HYPOTHESIS_STORAGE_DIRECTORY=/tmp/aas-q/a/hypothesis \
"$task_python" -m pytest -q -p no:cacheprovider -m 'not docker' \
  --basetemp=/tmp/aas-q/a/pytest-gates \
  tests/behaviours \
  tests/platform/test_platform.py::test_projector_contract_and_lossless_times \
  tests/platform/test_workflow_review.py \
  tests/platform/test_native_threshold.py \
  tests/platform/test_threshold_general_review.py tests/packs
```

Result: **87 passed, 1 Docker test deselected** (this gate contained 48 new tests
before the final dependency-index/producer-warmup tests were added). The final
new-runtime suite above covers those later changes. Logs:
`verified-gates.txt`, `final-regressions.txt`, `projector-contract-regression.txt`.

## Measured legacy journal compatibility

`workflow` and `threshold` retain selectable historical interpreters through
the new factory module. Their compatibility IR is descriptive, not a claim
that the old comparator language was converted to Q6 or the new chain IR.

The comparison script under `/tmp/aas-q/a/compare_compat.py` loaded historical
classes from `git show HEAD:src/aeroagentsim/engines/{workflow,threshold}.py`,
executed `scenarios/p1-slice.yaml` against historical and compatibility factories,
and compared both the raw journal bytes and sorted-key compact JSON encoding of every record:

```bash
PYTHONPATH=src AEROAGENTSIM_AEROGRAPH_ROOT=/tmp/aas-q/b/ontology \
"$task_python" /tmp/aas-q/a/compare_compat.py
```

Both produced **1,624 records, 14,706,768 bytes**, SHA-256
`715d9f6e9964e1148e54c70ebf84a207cdd7440e06949176f844f88cda6a32db`.
The raw WALs were also identical: **8,885,797 bytes**, SHA-256
`3bf93d250900b81267e7f1dd4123875bbf64d11a6a2220f84e25ed65805602ec`.
The script asserted both equalities. No journal differences occurred in this
measured case. `behaviour.compat.ir.json` and manifest epoch are additive run
artifacts, not kernel journal edits. This evidence does not assert universal
byte equality for every possible legacy configuration.

## New-runtime coverage

Tests exercise real kernel commits for assignment/completion, injected conflict
edges, instance creation after new entity generations, named live ingress and
HTTP admission, state/instance timers, same-ns zero-delay delivery, real typed
child results and receipt references, relation capacity arbitration, relation
predicates and per-assertion binding, unbind timer cancellation, producer-warmup
continuation, transition-budget fault prefixes, and Q6 sampled-frame reuse.
Sampled feedback rejects zero return lag and accepts an explicitly declared
positive lag; no implicit nanosecond is added. A cached successful child cannot
satisfy an `any` receipt trigger on another child's fresh rejection.

Hypothesis tests permute independent binding declarations and vary injection
availability. Replay compares projected WAL/state while the evaluator is
patched to raise if called. Feed tests verify recorded half-open intervals,
role EntityKeys, exact commit/proposal versions and replay equality.

## Job B smoke and remaining integration

The current read-only accident package was actually submitted to the compiler.
Its first authored-path error is:

```text
wt-b/scenarios/demos/traffic-accident/behaviours.yaml: $:
unsupported keys ['feedback', 'requires_compiler_features', 'sampled_contexts']
```

Full accident execution in kinematic/stub-decision mode **was not run**. Its
file exists; it requires the concrete semantic conversions listed in
[behaviours.md §13](behaviours.md#13-changes-required-in-job-bs-current-accident-package).
This is not a successful accident-demo gate. Q6 sampled contexts are finite,
explicit scenario declarations; the compiler does not manufacture contexts,
split feedback routes, implement B's ETA selector or generate business entity
IDs from events. Actual capture/storage/edge acceptance must remain real
producer evidence when B is integrated.

Docker and frontend gates were not run: Docker tests were explicitly deselected,
and this diff does not touch frontend files.

## Concurrent GLM work audit

Independent compiler, compatibility, projection, gateway, tests and documentation
tasks were launched as actual headless dsh sessions with bounded file ownership.
The corrected six request headers were inspected: model `glm-5.3-flash`, output
`maxTokens: 131072`, no effort parameter, provider profile `workbuddy`.
The initial profile inherited an unintended FlashX setting; it was corrected
and those initial sessions were stopped, as disclosed during the work.

The compatibility session completed and produced its four owned files and gate
report; its output was inspected and its interpreter bodies/journals verified.
The documentation session produced a draft that was reviewed and corrected
against the final code. Sessions that kept reading without delivering were
stopped; their unfinished tasks were implemented and verified by the primary
agent. No unverified GLM pass claim is used as evidence above.
