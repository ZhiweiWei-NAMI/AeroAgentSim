# Power line inspection

A beginner template: one drone flies along an authored power-line corridor,
records survey observations at measured positions, and waits on the ground
when wind is stronger than its limit.

## What happens

- Inspection tasks are released at 0 s, 130 s and 220 s between `line-start`
  and `line-end`.
- The `survey` work route flies the drone through the configured waypoints of
  the line; each observation event carries a committed position.
- A wind gust is authored between 35 s and 75 s at 9 m/s, above the
  inspection wind limit of 6 m/s, so the drone holds and then continues.
- The airspace engine owns geofence zones; the flight stays inside the
  `powerline-corridor` zone.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 1 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, and `inspection_observations`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/powerline-inspection/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

One shared kernel: task state, motion, weather, airspace and energy are
separate engines with one writer per field on the same clock.
