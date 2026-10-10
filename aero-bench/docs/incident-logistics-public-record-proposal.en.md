# Proposal: public records for the incident and logistics exception workflow

Status: proposal, not implemented. Prepared 2026-10-01 19:05 PDT by Claude
for handoff gate 3. No sealed run, public trace or contract was changed.

## 1. What the viewer consumes

`frontend/src/operations-data.ts` (`publicOperationEvents`) builds the
exception list only from explicitly named public payload attributes on
`PublicRunEvent.public_payload`:

| Workflow field | Attribute names read | When absent |
| --- | --- | --- |
| Severity | `severity` ∈ `critical`, `warning`, `info` | "等级未知" (severity unknown) |
| Condition | `condition`, `status`, `state` | "未知" (unknown) |
| Message | `message`, `label` | event type only |
| Object and location | `entity_id`, `vehicle_id` (position from the scene state at the event tick); `location_label`, `location` | no map location |
| Links | `mission_id`, `order_id`, `facility_id`, `dependencies` (JSON string list) | "未知" (unknown) |
| Operator state | `acknowledged`, `resolved` (bool) | "未知" (unknown); counted as unresolved-unknown |
| Grouping | `group_key` | not grouped |

Labelled UI fixtures (`frontend/src/testing/trace-v3-fixture.ts`) already
exercise the full workflow in `operations-data.test.ts` and
`operations-monitor.test.ts`:
- projection;
- unresolved-first ordering;
- evidence, order and dependency links;
- selection → map navigation;
- keyed grouping;
- unknown-resolution counts.

These fixtures are test data only. They are not formal evidence and must not
enter a sealed run.

## 2. What the sealed formal runs contain (measured)

| Run | Public events | Exception-capable records |
| --- | --- | --- |
| Inspection v8 `c69f303f…` | 918: 316 network-link, 298 traffic-light, 298 status, 4 sensor-frame, 2 `mission.event.v1` | The two mission events have an empty `public_payload` |
| Traffic restriction R5 `c073afde…` | 1,596, with the same two `mission.event.v1` records | Same |
| Logistics v7 `316379c1…` | 0 | None; `mission_events` and `mission_status_history` are empty |

In the sealed v8 event log
(`runtime-seal/harness/event-log.jsonl`), both mission events carry
`severity: info` and `state: delivered`, plus evidence `artifact.delivery`
selectors 0 and 1. These are ns-3 delivery records. They are not incidents and
not logistics deliveries.

The logistics v7 runtime seal has one Business order record in
`runtime-seal/logistics/state.json`: `kind: created`,
`order_id: scheduled.order.1`, from `facility.origin` to
`facility.destination`. It has no public projection.

## 3. Gaps

1. **Severity and message are validated, then dropped.**
   `aero_bench/trace/projector.py` `_project_mission_event` requires
   `severity` ∈ {info, warning, error, critical}. However, `PublicMissionEvent`
   has no severity or message field. `_SAFE_PUBLIC_INTERACTION_PAYLOADS` has no
   entry for `mission.event.v1`, so the `PublicRunEvent` payload is empty.
2. **Severity vocabularies differ.** The backend accepts `error`; the viewer
   knows only `critical`, `warning` and `info`. A published `error` would show
   as "等级未知" (severity unknown). This drift is latent today because nothing
   is published.
3. **No Provider emits an incident.** No declared scenario produces a
   `warning` or `critical` mission event bound to an `entity_id`.
4. **Logistics order lifecycle is private only.** The Business Provider
   records order lifecycle entries, but the public trace has none.
5. **Operator acknowledgement and resolution have no authority.** They are
   operator actions, not Provider facts. No Control-side record exists.

## 4. Narrow additions (in dependency order)

Owner: Track B (projector, contracts, `tools/generate_contracts.py`), then the
frontend consumer. Each step needs its own tests and a fresh formal run. Old
runs are not re-projected into new claims.

1. **Publish declared mission-event fields.** Add `mission.event.v1` to
   `_SAFE_PUBLIC_INTERACTION_PAYLOADS` with exactly `severity`, `message`,
   `state` and `entity_id`, the fields `_project_mission_event` already
   validates. `evidence` stays in `mission_events` as artifact references.
   The viewer needs no new attribute names.
2. **Align severity.** Pick one vocabulary in the contract and generate it for
   both sides. Either the viewer adds `error`, or Providers stop emitting it.
   Do not map `error` to `critical` silently.
3. **Public order lifecycle.** Project Business order records as public events
   with `order_id`, `state` (the recorded `kind`), `facility_id`, and evidence
   pointing at the sealed Business state. Publish only kinds the Business
   Provider actually records. Do not publish `delivered` or infer delivery from
   arrival; physical delivery remains a separate capability gap.
4. **A formal exception scenario.** Declare one run in which a Provider emits
   a `warning` or `critical` mission event, with an `entity_id` that has scene
   samples at that tick. For example, the inspection Business Provider can
   report a detected defect or a blocked task. The event must come from the
   Provider's real logic, not from injection.
5. **Operator actions (separate assignment).** Acknowledgement and resolution
   need a Control-side operator action record with its own authority and
   audit. Until then, the viewer correctly shows them as unknown.

## 5. Acceptance for closing gate 3 with formal evidence

- A sealed run that passes its independent verifier and whose public trace
  contains at least one `warning`/`critical` event with an `entity_id`
  resolvable to a map position.
- For logistics: at least one public order-lifecycle event linked by
  `order_id`.
- A browser check against that run showing:
  - severity, condition and location;
  - the order link;
  - navigation from event selection to the map;
  - unknown acknowledgement and resolution while no operator records exist.
