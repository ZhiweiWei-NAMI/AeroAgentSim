# Parcel delivery

Eight drones deliver seeded orders from two vertiports to 25 customer rooftops.
Use the fleet size, order rate, allocator, path planner and wind knobs to compare
completed deliveries, latency and energy in a 300 s run. Orders arrive in bursts of four and are allocated every 10 s over all pending orders and free drones, so `nearest`, `auction` and `hungarian` give different results (see `docs/guides/algorithms.md`).

## Huangpu city placement

Coordinates are derived from the committed lite-city building outlines, roof
heights and roads by `tools/templates/place.py`. Roof pads use their actual roof
height; inter-site flights depart/arrive vertically and cruise above the 105 m
roof ceiling. Names and business metadata are pinned with each run. See
[the shared placement guide](../../../docs/examples/city-day.md#huangpu-placements-la3).

New order bursts stop at 180 s, leaving two minutes for the backlog to clear.
Unreleased authored task slots are capacity, not created orders. Multi-bay
vertiports have four bays; parcel pickup does not use the passenger pad queue.
Battery, airspace and wind policy is editable in [the shared rules](../../../docs/guides/lowalt-rules.md).

The measured default completes 13 of 20 created orders by 300 s; the rest
remain pending or in flight. Repeated airborne separation interruptions still
limit completion. These are unfinished orders, not reported task failures.
