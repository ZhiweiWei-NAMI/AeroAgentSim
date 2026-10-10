# Operations monitor data adapter

`frontend/src/operations-data.ts` is the read-only boundary between formal
frontend contracts and `OperationsMonitor`. It does not change Provider,
replay, scene, or authoring contracts.

## Current inputs

`StateSample` supplies AGL altitude, ENU velocity, battery fraction, health,
mode, optional attributes, entity identity, Provider identity, and simulation
time. The adapter uses the three ENU velocity components to calculate speed.
It converts `battery.remaining_fraction` to percent only when the field exists.
Missing battery, mode, connectivity, or health data stays absent or explicitly
unknown.

Activity, connectivity, and health remain separate values. Activity comes from
`mode`. Connectivity comes only from a declared `connectivity`,
`connectivity_state`, or `network_state` sample attribute. Health comes only
from `health.healthy`. Sample age compares the sample simulation timestamp with
the supplied source clock. Replay callers pass the playhead; live callers pass
their observed source clock. Wall time and an assumed telemetry cadence do not
affect freshness, so pausing a replay cannot make its telemetry older.

`PublicRunEvent` supplies stable run and event identities, simulation time,
entity or vehicle identifiers, event type, digests, and authorized public
payload attributes. Every event passes `assertPublicEventSafe` before use.
The adapter filters events after the playhead and removes repeated deliveries
with the same event ID.

Severity, condition, acknowledgement, resolution, location, mission, order,
facility, dependency, and sensor-frame values are copied only from named public
attributes. An event without a declared severity has `unknown` priority. A
command receipt or accepted status remains that status; the adapter does not
turn it into task completion or delivery. Event evidence always links the run,
event digest, and payload digest. Sensor-frame links appear only when a frame
identifier is declared.

## Spatial and logistics boundaries

The event-position callback receives an entity ID and the event's exact tick.
Integration may return a position from that tick's scene state or trajectory.
It returns `null` when exact historical position is unavailable. The adapter
does not substitute the object's current pose, which prevents a replay event
from moving as the playhead advances.

`latestOperationTask` selects the newest visible event for an entity that
declares `task_id`, `mission_id`, or `order_id`. It copies only declared phase,
label, facility, and dependency fields. Event names, command acceptance,
waypoint arrival, and vehicle motion do not imply a task phase. If public run
events carry no logistics identifiers, the result is `undefined` and the
monitor displays unknown context.

Current formal public traces project several Provider event payloads into
specialized trace collections and may leave the matching `PublicRunEvent`
payload empty. The adapter therefore cannot recover order, mission, facility,
or sensor-frame links that the public event does not carry. Adding those links
requires a separately reviewed public projection or a narrow adapter from an
existing specialized public collection; this UI change does not alter the
backend contract.

## API and checks

```ts
telemetryFromSample(
  sample: StateSample,
  timeSeconds: number,
  source: "replay" | "live",
): OperationsTelemetry

publicOperationEvents(
  events: readonly PublicRunEvent[],
  timeSeconds: number,
  positionAtEvent: (entityId: string, tick: number) => { x: number; z: number } | null,
): OperationsEvent[]

latestOperationTask(
  events: readonly PublicRunEvent[],
  entityId: string,
  timeSeconds: number,
): OperationsTask | undefined
```

`frontend/src/operations-data.test.ts` checks declared telemetry projection,
unknown values, simulation-clock freshness, duplicate stream delivery,
playhead filtering, public-event rejection, exact-tick location lookup,
undeclared event state, evidence links, and declared-only task selection.

## Map integration review

The current `map.ts` integration correctly sends the complete event stream to
the adapter, which filters events and task context at the selected simulation
time. Event locations come from the scene state at the event tick or an exact
trajectory sample at that tick. The callback returns `null` when neither source
has that tick.

Four integration issues remain open:

1. A formal monitor snapshot uses the scenario digest as `sourceKey`. Two runs
   of the same scenario therefore share a monitor identity and can retain
   selection or view state across the run boundary. `MapScene.operationContext`
   should carry the replay trace run ID or live session run ID as a separate,
   stable source key.
2. Telemetry source semantics are selected by checking whether the Chinese
   display label contains `回放`. A copy or localization change can classify a
   replay as live. The operation context should carry a declared
   `"replay" | "live" | "authored"` source kind.
3. `observationTime()` falls back to the authored-preview clock when a formal
   scene has no current `SceneState`. At replay tick zero, while sealed state is
   unavailable, or before the first live state, this can filter events and
   calculate freshness against an unrelated time. The operation context should
   carry its source time explicitly. A paused replay or live inspection should
   retain its selected simulation time.
4. Live operation context reports `playing` whenever any scene state exists,
   even when the local buffer is paused in `INSPECT`. This mislabels the
   visualization clock. The monitor clock should follow the local playback
   state while the source label continues to state that pausing observation
   does not stop the Provider run.

The current event header counts every event without `resolved: true` as
ongoing. An absent resolution value means unknown, so it cannot support that
count. The header should count only `resolved === false` as known ongoing and
report undeclared resolution separately.
