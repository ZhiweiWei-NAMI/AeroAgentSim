# Edit the low-altitude rules

Every lowalt template includes the same `lowalt.rules` behaviour package in
its top-level `behaviours` list. Open a template in Studio, then select
**Predicates** or **Event chains**. Edit the predicate parameters to change
policy; owning engines still execute algorithm proposals and motion commands.

| Predicate | Chain and observable effect |
| --- | --- |
| `battery_below_reserve` | SOC × 100 < `reserve_percent`: emit `lowalt.energy.low`, mark `lowalt.returning_to_charge`, command the charging slot to choose a pad. The command succeeds only when charging finishes; the chain then clears the assignment block. |
| `inside_no_fly_zone` | For each aircraft / protected-zone pair, test the zone's committed `lowalt.zone_id` against `lowalt.breached_zones`; emit that zone's `geofence_entered` once and command rerouting through the path-planning slot. Overlapping zones each have their own entry / exit. |
| `closer_than_safety_distance` | Read a committed conflict partner and horizontal gap; compare with `horizontal_m`, emit `conflict`, command the conflict-resolution slot, then emit `conflict_resolved` on its real separation receipt. |
| `wind_above_limit` | Compare measured wind with the parameter for the vehicle's class; emit `hold` and set `lowalt.weather_held`. On exit, emit `release`, clear the flag and resume. |

The Battery reserve (%), Safety distance (m), and Wind limit (m/s) template
knobs point into these predicate parameters. City-day's wind-limit knob edits
the inspection-class limit; delivery and passenger limits remain separately
editable in Studio. Medical delivery uses parcel-class aircraft carrying
both routine and urgent orders. The committed `lowalt.wind_limit_mps` fact
reflects the authored class parameter at load. Dispatch eligibility uses the
same authored battery reserve as the predicate, so a below-reserve aircraft
cannot receive an order while the reactive recall flag is still being committed.

Airspace retains the spatial hash and swept geofence detection: these need
geometry, sampling history and zone exemptions, and are not pairwise predicate
joins. It alone owns membership, breached zones, conflict partner/distances,
zone activation and measured wind speed. The predicate reads those committed
facts. No-fly bindings are aircraft × protected zones, not aircraft × aircraft;
the finite instance budget accounts for those zone pairs. A lead aircraft exposes
one current separation pair at a time; the complete
`lowalt.conflicts` list retains all detected partners. Resolution considers
the current conflict set once per simulated instant to avoid contradictory
layer proposals. A duplicate resolution request is rejected rather than
replacing the executing request: the original chain retains its physical
separation receipt. Landed pairs on site aprons are excluded; hovering aircraft
at cruise height are still monitored. Transit routes retain assigned altitude
layers rather than immediately descending into the same conflict.

When a zone activates around an active flight, its owner can prepend a bounded
emergency exit and then use the configured planner to reach the original goal.
The first waypoint must be outside **all** start-containing no-fly boxes. Only
the unavoidable first straight exit prefix is exempted; other boxes and every
later segment retain the normal inclusive crossing guard. Zones are never
silently deactivated for planning. A forbidden destination or exhausted bounded
search retains a stopped flight and its goal for retry, without false pickup or
completion; this is not a complete arbitrary-3D escape algorithm.

Goal-less idle evacuation remains unimplemented. Already charging and
weather-held aircraft retain their existing owner-controlled movement gates;
the rule does not invent a charging-stop handshake or a certified emergency
priority policy.

The behaviour engine alone owns the weather hold, battery recall and class
limit facts. Energy owns SOC and charging; tasks own assignments and route
execution; motion owns pose. No operational alert is also emitted by its
detection engine. Zone activation and charging start/finish remain physical
engine events, with the existing design payloads. City-day composes the rules,
traffic accident and operator packages under one behaviour engine.

In Inspect, select a drone and review **Event chains** and **Conditions
(predicates)**. On a low-battery event, **Why did this happen?** shows the
SOC read, reserve predicate, battery chain and the recall flag's before/after
change. Follow the command and its charging receipt to see physical completion.
Journal replay reconstructs these records without executing the rules again.

Regenerate authored documents with `tools/templates/place.py`, then
`tools/templates/rules.py`; their `--check` modes verify reproducibility.
The rule package source is `src/aeroagentsim/packs/lowalt/rules.py`.
