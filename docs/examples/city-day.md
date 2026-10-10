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
| `airspace` (lowalt_airspace) | Geofences, separation, wind holds | `lowalt.zone_active`, incident-zone bounds, `lowalt.membership`, `lowalt.conflicts`, `lowalt.weather_held` |
| `energy` (lowalt_energy) | Battery and charging | `lowalt.soc`, `lowalt.charging`, `lowalt.energy_used_j`, `lowalt.distance_m`, `lowalt.motion_energy_j` |
| `environment` | Weather | `lowalt.wind` |
| `traffic_assessment` | Feasibility/eligibility inputs for responders | `traffic.assessment.*` |
| `behaviour` | Accident chain state, bids, reports | `traffic.task.*`, `traffic.incident.*`, `traffic.bid.*`, `traffic.report.*` |
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

## Measured default run

With AeroGraph unset, the 300 s headless city run took 129.08 s: RTF 2.32,
peak RSS 236.0 MiB, journal 27.35 MiB, and one real accepted PNG capture.
These are CLI wall-time/RSS measurements, excluding replay. All five individual
templates also exceed real time (RTF 40–77). The city includes 63 road vehicles,
eight traffic UAVs and six lowalt aircraft. Its larger shared traffic/behaviour
workload and journal are the likely source of the difference; no CPU profile
was collected.
