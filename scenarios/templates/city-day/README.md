# City day

A busy but beginner-friendly template: several low-altitude businesses plus
the public traffic-accident chain share one simulated district for 300 s.
A companion walkthrough lives at [docs/examples/city-day.md](../../../docs/examples/city-day.md).

## What happens

- Parcels, inspections, passenger taxi flights and urgent medical deliveries
  run concurrently in the same airspace, from the same vertiports.
- The traffic-accident chain runs in the same world: a road incident forms,
  a reporter chooses a bypass, a responder UAV is dispatched, captures the
  scene and uploads it. Responders keep their own sole motion owners
  (`air_motion`, `road_motion`); the low-altitude airspace engine only
  watches their published positions and moves the low-altitude fleet around
  them.
- Authored timeline: accident activates at 8 s, a wind gust 65–105 s,
  the operator closes `east-port` at 120 s, and an urgent medical order is
  injected at 160 s. A dynamic no-fly zone around the accident activates on detection and expires at
  110 s (responders are exempted). When the responder actually arrives is a
  measured outcome of your run — read it from the journal, not this page.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Parcel drones (1–12), plus three specialist aircraft | 3 |
| `wind_speed` | Wind front speed in m/s (0–15) | 9.0 |
| `allocator` | Task allocation: `nearest`, `auction`, `hungarian`, `llm` | nearest |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, `geofence_violations`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/city-day/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # existing browser console
```

Field ownership follows the shared kernel contract:

| Fields / actions | Sole owner |
| --- | --- |
| `lowalt.position`, `lowalt.velocity` | `motion` |
| Traffic UAV pose, velocity and energy | `air_motion` |
| Road position, velocity and occupancy | `road_motion` |
| Lowalt task state, payload, site configuration/open state; static zone geometry | `tasks` |
| Incident zone geometry/activation, membership, conflicts and wind holds | `airspace` |
| Lowalt SOC, charging, consumed energy, distance and available motion energy | `energy` |
| Shared `lowalt.wind` | `environment` |
| Traffic incident/task/bid/report state and operator event admission | `behaviour` |
| Traffic route inputs | `route_inputs` |
| Bid feasibility measurements | `traffic_assessment` |
| Real PNG artifacts and capture metadata | `capture` |
| Capture/incident relation and receipt forwarding | `capture_bridge` |
| Scripted decision proposals | `decisions` (no field writes) |

Offline defaults schedule the two operator decisions. For live SDK control, use an operator stream starting at watermark 0 and advance progress after admitting the next command; see the fabric test and the city-day guide. The traffic camera uses the existing headless Chromium/Playwright renderer.

The city camera reuses the traffic capture owner and binds storage to the actual RunSession directory. It honors an explicit Chromium override, discovers an installed Playwright headless shell, or uses Playwright's standard browser installation. No image fixture substitutes for a failed render.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

The green field is an explicitly authored demonstration polygon beyond the
northern dense core, not source OSM cropland. Spraying rows are 5 m above its
ground plane; the standalone swap pad is at the field edge.
