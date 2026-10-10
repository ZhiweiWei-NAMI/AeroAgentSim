# Independent Agent inspection runtime

This runtime lets a fresh `gpt-6-astra/xhigh` session perform the declared
three-order inspection through granted functions. Its flight destinations,
image classifications, report content, and recovery decisions come from the
session. The generic bridge executes those explicit requests and records their
effects. The historical Inspection v1 reference result in the repository README
does not establish acceptance of this independent Agent task.

The acceptance requirement is recorded in [the approved plan](AGENT_INSPECTION_E2E_PLAN.md).
Engineering progress and failed attempts are retained in
[`validation/agent-inspection-development/progress.md`](../validation/agent-inspection-development/progress.md).
Acceptance remains pending until one frozen candidate passes two consecutive
fresh sessions, independent sealed verification, offline re-verification, and
public replay checks.

## Task and configuration identity

`tools/build_agent_inspection_v1.py` creates a bundle containing `suite.yaml`,
`task/task.yaml`, `task/inspection-package.json`, `instruction.md`,
`agent/participant.yaml`, Provider configurations, public navigation and output
schemas, private truth, and `resolved-run.json`. The task contains three separated
work orders with visible upper/lower defects and a clean surface. Private target
labels and simulation assets are excluded from the Agent's input projection.

The builder requires a fresh image lock and the matching content-derived runtime
source revision from `tools/build_agent_inspection_images.py`. That tool builds
and digest-pins eight declared components: Harness, PX4/Gazebo, ns-3, SUMO,
Inspection Business, Verifier, participant bridge, and Astra Driver. The native
Codex executable is pinned by version and SHA-256 in the Driver configuration.
Changing a source, schema, task asset, image, seed, policy, or relevant override
changes the resolved identity. Stale source/image bindings fail explicitly.

The immutable `run_id` identifies configuration. Each execution gets a distinct
`attempt_id`; repeating the same task never overwrites an earlier attempt.

## Granted functions

The exact function catalog is compiled from `AgentSpec` grants and their hashed
JSON schemas by [`compile_tools`](../aero_bench/agent/bridge.py). These names and
descriptions summarize the inspection bundle; the compiled catalog remains the
executable contract.

| Function | Authority and result |
| --- | --- |
| `query_business_work_orders` | Read-only Business discovery of the declared orders and current states. |
| `query_business_work_order` | Read-only detail for a discovered ID: navigation references, capture tolerances, deadlines, delivery requirements, and authoritative current state. |
| `read_flight_telemetry_uav_inspector` | Actual MAVSDK flight estimate, health, attitude, velocity, armed/landed state, source and freshness metadata. |
| `read_flight_gnss_uav_inspector` | Actual MAVSDK raw GPS and GPS quality streams, including fix and satellite count; explicit missing-value and timestamp status. |
| `read_observation_01`, `read_observation_06`, `read_observation_08` | Granted inspection observations with real Gazebo PNG data when capture conditions hold, plus frame ID, byte digest, and provenance. |
| `action_business_claim`, `action_business_start`, `action_business_submit` | Explicit work-order transitions through Business. Actor identity is injected from the authenticated Agent contract. |
| `action_flight_arm`, `action_flight_takeoff`, `action_flight_goto`, `action_flight_hold`, `action_flight_land`, `action_flight_disarm` | Stage the model's requested flight operation; acceptance and ACKs do not prove physical completion. |
| `action_network_send` | Transmit the exact bytes of a previously written declared artifact through ns-3. The bridge derives its digest and wire payload. |
| `get_run_status`, `get_command_status` | Read the current clock or a command issued by this session. |
| `wait_for_command`, `wait_duration` | Advance complete Provider barriers under declared simulation and wall-time bounds. They do not select routes, targets, observations, or further actions. |
| `read_public_asset` | Read one enumerated public asset, including maps and artifact schemas. |
| `write_json_artifact` | Write one declared JSON artifact once, using the model's content; return its exact bytes' SHA-256 and size. |
| `record_decision_summary` | Record a concise explicit mission note. Every action also requires its own model-authored `decision_summary`, bound to that command. |
| `finish` | End the session and submit its final Agent turn. It does not declare the benchmark successful. |

Query, command, observation, and turn requests use strict versioned contracts.
Missing inputs, undeclared operations, mismatched hashes, and invalid Provider
responses are errors. The Bridge and Gateway preserve their actual outcomes in
the interaction audit and authoritative ledger.

## Coordinates, time, and evidence

Navigation details expose WGS84, ENU, AMSL, and AGL references. `flight.goto`
takes WGS84 latitude/longitude and AMSL altitude; its yaw is ENU yaw, with zero
pointing east and positive rotation toward north. `flight.takeoff.altitude_m`
is relative takeoff height. The public instruction and order details state the
geofence, no-fly volume, capture envelope, dwell, minimum takeoff height, and
return/landing requirements. They supply constraints, not route waypoints.

Model inference leaves simulation paused. Each 0.5 s advance requires every
declared Provider stage to return valid evidence. Wall-time timeout and
simulation-time deadline are separate: a bounded wait can time out while a
flight command is still applied. The session must inspect the returned state
and decide whether to wait again. A physical completion receipt requires actual
command acknowledgement and the configured state/settling predicates.

Flight estimates, GNSS measurements, and private Gazebo pose authority remain
distinct. Receipt freshness and unavailable source-to-simulation mappings are
reported explicitly. Each physical camera is captured once per barrier even
when several target observations share it. PNG dimensions, RGB format, exact
engine timestamp, frame/image digests, and private pose bindings are validated.

The model reads the public output schemas before writing the complete detection
array and report array. Detections cite actual image/frame identifiers. Reports
bind the detection artifact digest. Business submission and ns-3 transmission
bind the exact report artifact bytes. A network delivery's arrival time can be
inside a step; Business commits at the validated barrier. The verifier checks
both times against their respective evidence and deadlines.

The current budget arithmetic and geometric assumptions are in
[the performance model](AGENT_INSPECTION_PERFORMANCE_MODEL.md). They are
theoretical bounds and engineering estimates, not measured mission success.

## Running an attempt

Use an already generated digest-pinned suite and an authenticated native model
credential file. The wrapper uses the suite's `runner.local.yaml`; the model
egress proxy must be explicitly provided. For example, from the repository root:

```bash
python tools/run_agent_inspection_attempt.py \
  --suite releases/agent-inspection-v1-candidate08/suite.yaml \
  --output-root validation/agent-inspection \
  --model-auth-file /path/to/model-auth.json \
  --timeout-seconds 22800
```

This command names an engineering candidate, not an accepted release. Use the
frozen release suite when acceptance is published. An optional explicit
`--attempt-id` must be new; collisions are rejected. No workload receives a
Docker socket, host path, host network, privileged mode, or extra capabilities.
The executor privately copies credentials to the Driver input volume. Credentials
are excluded from model context, interaction logs, and sealed artifact inventory.

Only the separate Driver has model egress. Its inspected transport replaces
native tools and inherited host context with the exact granted catalog, public
instruction, initial input, and trusted session history. Filesystem, shell,
browser, arbitrary network, Provider-private state, and Verifier input are not
model tools. Hidden reasoning is not recorded; explicit model-visible inputs,
outputs, function arguments/results, image references, and public summaries are.

## Reading and verifying an attempt

The wrapper stores outputs under
`validation/agent-inspection/<run_id>/<attempt_id>/`. It first records the task,
resolved run, source specification snapshot, and effective runner configuration.
Successful execution adds the runtime seal, independent verification outputs,
public projection, and terminal summaries. The exact paths and artifact digests
are named in the manifests rather than inferred from a command's exit message.

`runtime-seal/model.session-manifest.json` and
`runtime-seal/model.interactions.jsonl` bind run/attempt/session identity, policy,
request/tool counts, usage, and a hashed ordered interaction chain. Gateway
audits bind model proposals to authoritative command/query/observation events.
The independent verifier also checks required discovery before claims, actual
flight/GNSS reads before arm, real image consumption, and complete task evidence.

Failed execution retains `failure-diagnostic.json`, available declared artifacts
under `failure-evidence/`, and terminal attempt/run summaries. Partial failure
evidence is explicitly non-authoritative and cannot pass sealed verification.
Cleanup removes only executor-owned resources; failure evidence remains.

Offline verification calls
[`verify_formal_v2_sealed`](../aero_bench/tasks/inspection/formal_v2_sealed.py)
with the resolved package, bounds, verifier configuration, bundle, and saved
seal. It reconstructs results from sealed authority and validates all 15 goals;
replaying model tool calls or rerunning Providers is unnecessary. The public
viewer consumes only the projected public trace and its declared public assets.
Loading a trace is a replay check, not an independent success verdict.
