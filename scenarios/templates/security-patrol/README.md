# Security patrol

A beginner template: a drone patrols a fixed loop of waypoints while an
operator can report an incident, which creates a higher-priority
investigation task.

## What happens

- Patrol tasks at 0 s, 100 s and 200 s drive the drone around the
  configured patrol circuit (`lowalt.patrol.visited` at each point).
- At 55 s the operator ingress injects an incident report; the task engine
  creates an `incident_response` task with priority 100 toward
  `incident-site`.
- The drone breaks off the patrol (priority preemption), investigates, then
  returns to the loop.

## Knobs

| Knob | Meaning | Default |
| --- | --- | --- |
| `fleet_size` | Number of aircraft (1–12) | 1 |
| `wind_speed` | Base wind speed in m/s (0–15) | 2.0 |

## KPIs to watch

`tasks_completed`, `mean_task_latency_s`, `energy_used_kj`, `weather_holds`,
`separation_conflicts`, and `patrol_visits`.

## Run it (existing CLI)

```bash
aeroagentsim run scenarios/templates/security-patrol/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
aeroagentsim serve   # browser console for the same scenario
```

Operator events enter through a declared ingress stream onto the same
shared runtime; behaviour owns task state, motion owns pose, one writer
per field.
