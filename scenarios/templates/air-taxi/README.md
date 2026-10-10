# Air taxi service

A beginner template: small passenger aircraft fly between three vertiports,
share limited landing pads, and separate from each other when their paths
come close.

## What happens

- Passenger tasks run between `home`, `east-port` and `north-port`
  (eight departures spread over the 300 s horizon).
- Aircraft start close together, so the airspace engine detects separation
  conflicts and the `altitude_layers` conflict-resolution algorithm assigns
  different cruise altitudes; `hold_and_go` holds on the ground instead.
- When a landing pad is occupied the aircraft waits (`lowalt.mobility.pad_wait`).
- Two corridor geofence zones shape the east–west and north flows.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 3 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |
| `conflict_resolver` | `altitude_layers` or `hold_and_go` | altitude_layers |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/air-taxi/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

The airspace engine is the only writer of separation decisions; motion
engines execute the authorized commands on the same shared clock.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).
