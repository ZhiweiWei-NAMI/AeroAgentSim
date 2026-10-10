# City day

The most complete everyday template: several low-altitude businesses run at
the same time in one district, **and** the public traffic-accident chain runs
in the same shared world. This page is written for first-time users.

## Shared world

There is exactly one kernel, one simulated clock and one journal. Air and
road vehicles from the traffic accident demo and the low-altitude aircraft
all commit state into the same run, each field owned by exactly one writer
engine. The external traffic responders keep their own sole motion owners
(`air_motion`, `road_motion`); the low-altitude airspace engine monitors
their published positions as read-only external vehicles and moves the
low-altitude fleet around them — it never writes traffic poses. Weather is
one shared `lowalt.wind` field owned by the environment engine.

## Who owns what

| Owner (engine) | Domain | Fields owned |
| --- | --- | --- |
| `motion` (kinematic) | Low-altitude UAV pose | `lowalt.position`, `lowalt.velocity` |
| `air_motion` (kinematic) | Traffic responder UAVs | `he.aircraft.position_enu_m`, `traffic.uav.velocity_enu_mps`, `traffic.uav.energy_j` |
| `road_motion` (traffic_road_motion) | Road vehicles | `traffic.road.position_enu_m`, `traffic.road.velocity_enu_mps`, `traffic.road.speed_mps`, lane/route/blocked fields |
| `route_inputs` (traffic_route_inputs) | Authored routes | `traffic.route.*` |
| `tasks` (lowalt_tasks) | Task state, assignments, work routes | `lowalt.task_*`, task site/vehicle state |
| `airspace` (lowalt_airspace) | Geofences, separation and wind detection | `lowalt.zone_id`, `lowalt.zone_active`, incident-zone bounds, `lowalt.membership`, `lowalt.conflicts`, `lowalt.breached_zones`, `lowalt.conflict_partner`, `lowalt.conflict_gap_m`, `lowalt.wind_mps` |
| `energy` (lowalt_energy) | Battery and charging | `lowalt.soc`, `lowalt.charging`, `lowalt.energy_used_j`, `lowalt.distance_m`, `lowalt.motion_energy_j` |
| `environment` | Weather | `lowalt.wind` |
| `traffic_assessment` | Feasibility/eligibility inputs for responders | `traffic.assessment.*` |
| `behaviour` | Accident and lowalt rule chain state, bids, reports | `traffic.task.*`, `traffic.incident.*`, `traffic.bid.*`, `traffic.report.*`, `lowalt.weather_held`, `lowalt.returning_to_charge`, `lowalt.wind_limit_mps` |
| `capture` (traffic_camera_capture) | Camera capture artifacts | `traffic.capture.request_id/asset_digest/...` |
| `capture_bridge` | Upload acceptance | `traffic.capture.incident`; joins capture-to-actor/incident relations |
| `decisions` (traffic_decisions) | Explicit scripted proposals | proposes only; runtime rules authorize |

Algorithms (allocation, path planning, conflict resolution, charging) are
plug-in slots; they only propose, and the owning engines commit.

## The day's timeline (authored)

These are simulated seconds authored in the scenario and behaviour package;
actual downstream events (such as when the responder UAV arrives at the
accident) are measured from the run, not known in advance — read them from
the journal or event feed of your own run.

| Time | Authored event |
| --- | --- |
| 8 s | Traffic accident activates in the shared behaviour chain |
| 65–105 s | Wind gust (9 m/s) — aircraft above their class wind limit hold |
| 120 s | Operator closes the `east-port` vertiport; arrivals divert to `north-port` |
| 160 s | Operator injects an urgent medical order (hospital → clinic) |
| Incident detected | `incident-no-fly` activates at the measured accident position and interrupts affected routes |
| 110 s | The incident zone expires and the airspace opens again |

Business tasks run throughout: parcels (from 0 s), inspections along the
north line, passenger taxis from the hospital vertiport to `east-port`, and urgent medical
flights at 8 s and 145 s.

The Huangpu layout starts one parcel drone at the hospital roof and two at the
west plaza. The nearest hospital parcel is preempted by the first medical order;
the plaza parcels and taxi are already routed when the incident is detected.

## Wind, zones and battery at a glance

| Class | Wind limit (m/s) | Battery |
| --- | --- | --- |
| parcel / medical | 12 | 240 kJ, threshold-return charging |
| inspection | 6 | 240 kJ |
| spraying | 5 | 240 kJ (city); the standalone crop template uses 20 kJ swaps |
| passenger | 15 | 240 kJ |

Zones: `main-corridor` (corridor, 0–300 s), `incident-no-fly` (dynamic,
activated by the incident event and expires at 110 s), plus the accident chain's own staging. External traffic
responders are exempted from `incident-no-fly`; low-altitude aircraft are
not, and the task owner replans their routes.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/city-day/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # existing browser console
```

The pack declares event narratives and template KPI names for the concurrent
API job. This backend change provides their committed facts and events;
the API job owns feed rendering and KPI projections.

For a live SDK session, set the operator stream initial watermark to 0 before loading, advance it to each known input boundary, admit commands via `RunSession.submit_live`, then advance progress. The offline template contains those same operator decisions as scheduled commands. `test_lowalt_fabric.py` exercises live admission at 120 s and 160 s.

City defaults use `hold_and_go` so congested pickup points make progress; the standalone air-taxi template demonstrates `altitude_layers`. The city includes the full public road workload (63 road vehicles and eight traffic UAVs) plus its six lowalt aircraft.

The city camera reuses the traffic capture owner and binds storage to the actual RunSession directory. It honors an explicit Chromium override, discovers an installed Playwright headless shell, or uses Playwright's standard browser installation. No image fixture substitutes for a failed render.

## Measured default run (2.1)

With the public lite city, no AeroGraph checkout and fast pacing, the 300 s
city run took 132.76 s: **RTF 2.26×**. This measures `RunSession.run()`, including
execution, native capture and journal close; scenario parsing, constructor
setup, KPI projection and replay are excluded. This is one shared-host sample
on 96 logical CPUs, with load averages 34.33 / 31.83 / 31.40 at start, not a
hardware-independent guarantee. The journal contains one real accepted capture,
two wind holds, 16 separation alerts and 14 completed tasks.

Default seed-42 delivery examples complete 18/20 parcel orders and 15/18 medical
orders. The existing `hold_and_go` policy avoids repeated altitude manoeuvres
at busy pickup points; failed terminal-reservation prototypes were rolled back.
The three-seed allocator/fleet experiment nevertheless shows congestion can
make a larger fleet complete fewer orders. See
[the comparison and its limits](../guides/algorithms.md#comparing-allocators).

## Huangpu placements (LA3)

All seven lowalt templates now use the committed Huangpu lite city's ENU frame
(E=X, N=-Z, U=Y). Regenerate coordinates from the actual building outlines,
heights and road polylines with:

```bash
PYTHONPATH=aerokernel:src python tools/templates/place.py
python tools/templates/place.py --check
```

`scenarios/templates/huangpu-placements.json` records the source SHA-256 and
placements. Roof pads sit at the source roof height; the west plaza is outside
all building outlines. Customer locations are reproducible interior roof points.
The flight owner uses vertical departures/arrivals and 125 m cruise (105 m
highest roof plus 20 m); altitude layers retain this floor. Inspection follows
the northern road's first traversal, while taxis use the main road corridor.
Patrol follows the landmark block near the north end of the incident lane.
The temporary incident restriction extends to 180 m, so it still changes routes
at the new cruise height. Transit avoidance replans from the new safe position;
coverage resumes its interrupted work segment. Passenger pad capacity is checked
before both pickup and destination descent, with boarding/arrival dwell.

There is no source cropland in the lite scene. The green field is an explicit
160 × 120 m demonstration polygon just beyond the northernmost buildings,
not an OSM land-use claim. Its inline glTF plane is anchored in ENU and uses the
existing model renderer; field work stays at 5 m, with the swap pad at ground
level. City spraying transits above roofs before descending into the field.
The optional detailed city binding is owned by job ASSETS; LA3 adds no `city:`
reference to scenario documents.

The fabric proof streams diagnostic records and compares every committed field
(including dynamically created entities) one at a time after releasing the live
runtime. It retains all business interaction assertions and checks that a
cross-business conflict is resolved; no fields or events are sampled away.

The standalone 300 s fabric pytest, including the real live-ingress capture and
full official replay, peaked at 418.4 MiB RSS (previously about 3.3 GiB). The
large diagnostic tuple was the avoidable allocation; this is separate from the
240 MiB headless run measurement above.

The four operational rules are editable predicates and event chains; see
[the rules guide](../guides/lowalt-rules.md).
