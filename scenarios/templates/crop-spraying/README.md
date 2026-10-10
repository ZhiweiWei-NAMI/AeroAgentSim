# Crop spraying

A beginner template: a drone treats a rectangular field in alternating rows,
swaps batteries at the field edge when its charge runs low, and pauses
spraying while wind is too strong.

## What happens

- One spraying task crosses the field via the `coverage` work route
  (boustrophedon rows).
- The battery capacity is sized for a partial treatment; when the state of
  charge falls below the low threshold the drone returns to `home-pad` and
  the energy engine swaps in a fresh pack.
- A wind gust is authored between 90 s and 125 s at 8 m/s, above the
  spraying wind limit of 5 m/s, so spraying pauses until it passes.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 1 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |
| `row_spacing` | Distance between spraying rows in m (10–80) | 30 |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, plus `spraying_distance_m` and `battery_swaps`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/crop-spraying/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

Motion, energy, weather, airspace and task state each have exactly one
writer engine on the shared discrete-event clock, so results replay.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

The green field is an explicitly authored demonstration polygon beyond the
northern dense core, not source OSM cropland. Spraying rows are 5 m above its
ground plane; the standalone swap pad is at the field edge.
