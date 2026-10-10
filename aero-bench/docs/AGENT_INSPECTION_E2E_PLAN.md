# Approved agent inspection acceptance plan

Approved by the user on 2026-09-12: execute continuously and deliver only after
the complete acceptance loop is closed. Intermediate API, module, build, or
flight results do not complete this task.

## Objective

An independent `gpt-6-astra` session at reasoning effort `low` controls one
PX4 SITL vehicle in the declared Shanghai Gazebo environment through granted
APIs. It discovers three work orders, checks flight and GNSS observations,
takes off, visits three separated targets, captures and interprets real camera
images, submits and delivers the reports, confirms authoritative business
completion, returns, lands, stops, and disarms. The target set includes both
defective and clean surfaces; target identifiers do not disclose private truth.

## Implementation and evidence

1. Preserve the current working source and establish a versioned task,
   mathematical feasibility model, image/configuration identity, and acceptance
   matrix. Validate the exact Astra session and technical tool restrictions
   before attempting formal model-driven flight.
2. Add a granted read-only Gateway query contract for business summary/detail.
   Publish navigation coordinates, coordinate/height references, tolerances,
   deadlines, and delivery requirements without private truth or history.
3. Expose sourced PX4 flight estimates and actual GNSS measurements/quality as
   authorized observations. Keep Gazebo truth, flight estimates, and GNSS
   sensor observations distinct. Missing required measurements remain explicit.
4. Complete Gazebo RGB capture, authorized image delivery, actual model image
   input, detection/frame/report bindings, and independent byte/provenance
   verification. Command acknowledgement alone is never physical completion.
5. Implement a generic, isolated OCI participant and explicit inference bridge.
   The model receives public instructions and declared function tools only.
   No inherited development context, filesystem, shell, browser, provider
   endpoint, verifier input, or runtime credential is available to it.
6. Implement declarative bounded waits over permitted observations. Every
   advanced tick closes the complete Provider barrier. Simulation remains
   paused during model inference. The bridge contains no target order, route,
   classification policy, or expected answer.
7. Store task definitions, resolved inputs, model/session identity, all explicit
   model-visible interactions, real image references, tool calls/results, and
   failures in versioned contracts. A trusted recorder binds actual model
   requests to responses, proposed calls, Gateway events, and sealed evidence.
   Do not record credentials or hidden model reasoning.
8. Run module tests, relevant regressions, real container integrations, and
   negative checks for unauthorized access, stale/missing required data,
   missing images, incomplete logs, and model disconnection. Build fresh
   digest-pinned production images with truthful source provenance.
9. Obtain the required read-only `gpt-5.6-sol/max` review and resolve findings.
   Freeze a candidate and run two predeclared consecutive fresh Astra sessions
   with identical task, seed, prompts, model settings, code, schemas, and images.

## Storage

The immutable configuration identity is `run_id`; repeated executions have
distinct `attempt_id` values. No attempt overwrites another.

```text
validation/agent-inspection/<run_id>/<attempt_id>/
  task-definition.json
  resolved-run.json
  agent-session-manifest.json
  runtime-seal/
  verification/
  public/
  attempt-summary.json
```

Model interaction JSONL and image payloads are explicitly declared in the
sealed inventory. Records bind run/attempt/session/turn/call IDs, sequence,
wall time, simulation time, payload digests, actual inputs/outputs, errors,
and Gateway event identities. Failed attempts are retained with their actual
terminal status and evidence completeness.

## Completion

Both fresh sessions must finish all three work orders and pass all 15 existing
Formal Inspection v2 goals plus access, API-use, real-observation, and transcript
checks. Independent verification must pass using sealed artifacts; task and
run identities must re-resolve, saved evidence must verify offline, public
replay must load, and executor-owned resources must be cleaned up. No parent
agent or human may supply actions during either acceptance attempt.

Any failed attempt rejects that frozen candidate. A repair creates a new
candidate and restarts the two-attempt count; all prior attempts remain
available. This is acceptance of one bounded task, not a statistical claim
about general agent reliability. A missing external prerequisite is reported
explicitly and never replaced by a different model, fake provider, scripted
participant, analytical telemetry, or weakened success criterion.
