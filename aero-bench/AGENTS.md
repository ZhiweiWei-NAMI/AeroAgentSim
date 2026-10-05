# AERO-BENCH Repository Rules

## Working method

Complete the assigned task using the current code and executable contracts.
Check the evidence first, make the smallest necessary change, and validate the
affected modules before delivery. Add focused module tests when behavior changes.

Continue through authorized implementation, repair, cleanup, and verification
without asking for confirmation at each stage. Resolve routine reversible
choices using the current task and measured evidence. Ask one specific question
only when required information is unavailable, access is insufficient, or the
next action exceeds the assigned scope. Continue independent work while that
question remains open.

## Assigned tasks and confidential information

Use the project files and installed dependencies needed for the assignment.
Files outside the workspace may be accessed when the task requires them.
Do not expose credentials or tokens, or modify unrelated user data. These
rules also apply to scripts, external tools, and delegated agents.

DSH development commands may use the installed Node runtime, DSH profiles,
settings and session storage, and the WorkBuddy connector outside the workspace.
Normal connector use of its existing login state is authorized. Do not print
or export its credentials.

The user's project configuration disables the Claude Code command sandbox
and uses `bypassPermissions`. Do not reintroduce workspace filesystem or
network allowlists unless the user requests them.

Invoke DSH in this workspace with `bash .claude/dsh.sh <arguments>` so it uses
the canonical runtime and storage paths. For web delegation,
select the `standard` agent preset for the DSH session; the user's current
default, `minimal`, exposes only a persistent shell. The installed connector is
`dsh-llm-workbuddy`, and the configured route is `workbuddy/glm-5.3-flash`.
Check the session's effective tools and route rather than inferring them from
the web profile's top-level composition. Forks share filesystem access; assign
each worker a separate file scope or worktree.

Run `bash .claude/dsh.sh --check` in the current Claude session to check the
configured WorkBuddy endpoint. With the project sandbox disabled, it connects
directly to the host endpoint.
It reports status, route, model availability, and the default preset without
printing credentials. This check does not generate a model turn or a fork.

For command-line WorkBuddy tasks, the installed `glm_batch` profile includes
the connector; the plain `headless` profile does not. The installed `glm_sdk`
profile also includes the connector for SDK-driven session and fork control.

Use existing project tools and the declared validation commands. If a check
depends on unavailable external configuration, report the specific dependency
and continue available checks. Login settings, permissions, sandbox controls,
integrations, and instruction files require an explicit configuration assignment.

## Code and cleanup

Do not add legacy compatibility paths or controls unrelated to the assignment.
Expose missing inputs, contract drift, and programming errors explicitly.
Remove task-related obsolete logic, error-hiding fallbacks, and redundant
branches. Preserve required business branches, explicit missing measurements,
and error handling.

Check affected directories and callers after changes, and decouple components
when the task requires it. Do not expand the task into a repository-wide
refactor. Remove only temporary files created for this task that are no longer
needed. Preserve logs, evidence, deliverables, and existing user changes. Never
delete or restore user work merely to produce a clean Git status.

## Data and research

Measurements and experimental conclusions must come from actual execution.
Distinguish measured results, theoretical calculations, assumptions, and items
awaiting validation. When results are poor, examine simulation construction,
data, evaluation criteria, modules, parameters, and research assumptions. An
assumption may be disproved; do not presume an implementation defect.

Before changing parameters that affect simulation or model performance, model
the configured environment, state the assumptions, calculate the theoretical
performance limit, and then verify it experimentally.

## Long-running work

Use a representative initial wait for a running process, then the execution
tool's supported long wait rather than frequent polling. Advance independent
work while a process runs. Handle completion or failure and continue validation;
starting a background process does not complete the assignment. Report material
progress at the cadence requested in the current session.

## Writing and project facts

Write development instructions, plans, handoffs, process documents, code
comments, and task reports in English. Preserve technical precision and the
user's intent. Use direct, specific language; remove filler, repetition, and
unsupported claims. Preserve application interface language unless the assigned
task explicitly changes it.

When adding or updating `.claude/skills/`, write skill descriptions,
instructions, supporting references, authored examples, and script comments in
English. Preserve executable behavior, technical identifiers, paths, and
original source data.

Use current code, executable contracts, and measurements from the current task
as the sources of project facts. Do not create, maintain, or read persistent
Markdown memory systems. Preserve original source data and experimental records
in their original form. Save deliverable documents where the user requests.

## Delegation

Delegate only when the assignment authorizes it. Share the established task
context instead of repeating exploration, and identify each agent's file
ownership, access scope, validation obligations, and deliverables. Delegated
agents inherit these rules and must not create further agents.

## Formal execution paths

The formal data path is:

```text
strict specs → immutable ResolvedRun → capability/feasibility preflight
→ explicit executor materialization → provider-barrier runtime
→ sealed artifacts → independent verifier → read-only viewer
```

Two executor profiles are explicit and never silently substitute for one another:

- `docker_reference`: the formal single-host Reference Executor. It runs only
  digest-pinned OCI workloads through the user-accessible Docker daemon.
- `kubernetes_cluster`: the deferred cluster executor. It requires an actual
  kubeconfig, existing Namespace, verified namespaced RBAC, and NetworkPolicy
  enforcement. Missing cluster prerequisites block this profile; they do not
  select Docker automatically.

Docker is an execution substrate, not a replacement for a declared Provider.
PX4/Gazebo, ns-3, SUMO, Airspace, Business, Observation, and Verifier claims
require the real declared backend, image digest, version identity, and evidence.

## Formal execution requirements

- Formal workloads use digest-pinned OCI images only.
- Do not substitute mocks or analytical fallbacks for declared Providers, or
  fabricate telemetry, evidence, or viewer data. Test mocks and fixtures are
  not formal execution evidence.
- Domain Specs contain no Docker or Kubernetes fields.
- The Docker executor may use the host Docker client as infrastructure, but no
  workload receives a Docker socket, host network, privileged mode,
  `CAP_NET_ADMIN`, or a host path.
- Workloads run as non-root with read-only root filesystems, dropped Linux
  capabilities, `no-new-privileges`, and RuntimeDefault-equivalent controls.
- A command receipt is not task success. Only sealed authoritative evidence can
  be evaluated by an independent verifier.
- Harness time advances only when every required Provider has returned a valid
  StepReceipt for the target tick.
- Agent workloads cannot access private truth, Provider-private state, or
  Verifier inputs. Viewer workloads consume public trace only.
- Every matrix value must be applied to the resolved configuration. Every
  relevant spec, image, asset, rule, seed, and override contributes to Run ID.
