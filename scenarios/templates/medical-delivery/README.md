# Urgent medical delivery

A beginner template: a couple of drones carry routine parcels, and when an
urgent medical request appears they pause the parcel and fly the medicine
first. One shared simulated world, one clock, 300 simulated seconds.

## What happens

- Routine parcel tasks are released from the home vertiport over the run.
- Urgent medical tasks (priority 100) are released at 8 s, 110 s and 200 s.
- With `medical_preemption` on, an eligible parcel flight is suspended, the
  drone flies the medical leg, then the parcel resumes.
- The environment engine publishes `lowalt.wind`; each aircraft class has a
  wind limit and holds position when the wind exceeds it.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 2 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |
| `allocator` | Task allocation: `nearest`, `auction`, `hungarian`, `llm` | nearest |

Knobs are applied to the scenario before validation; errors are reported in
plain language.

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, plus medical-specific `medical_mean_task_latency_s`
and `medical_p95_task_latency_s`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/medical-delivery/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

All engines write through one shared runtime with a single writer per field,
so a completed run replays deterministically.

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).
