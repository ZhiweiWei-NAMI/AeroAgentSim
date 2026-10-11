# Restaurant meal delivery

Two modeled restaurant kitchens prepare seeded meal orders. Kitchen capacity is
the number of meals that can be prepared concurrently. Waiting orders enter
`preparing` when a slot becomes free, and enter the allocation queue only after
the configured preparation time elapses. FoodOrder is a distinct subtype of Task;
Restaurant is a distinct subtype of Site.

The existing allocation, kinematic motion, energy and airspace engines then move
the courier to the restaurant, load the meal on a successful arrival receipt,
and deliver it on the destination receipt. Every order's promised deadline is
creation time plus its authored delivery window. `on_time_rate` uses committed
food delivery events and deadlines. This models preparation and delivery timing;
it does not estimate food temperature or claim a food-safety outcome.

Edit restaurant position, preparation seconds, kitchen capacity and opening state
in Studio's ordinary entity fields. Edit meal destinations, release rate,
payload and delivery window in the task generator; edit aircraft capacity,
battery and fleet count in its fleet generator. Template knobs expose the common
parameters directly. The six authored order slots bound this starter scenario;
additional orders require additional FoodOrder entities in the generator pool.

The `restaurant_delay` injection point accepts:

```json
{"restaurant": "restaurant-east", "delay_s": 60.0}
```

Choose `run_mode=interactive` when launching from the template to accept live
events. This mode begins with operator progress at zero and waits for the console
to advance simulation time.
Interactive waiting has no time limit: leaving the run view or disconnecting
does not terminate the run, and Stop remains available in the console.
The default `scheduled` mode closes operator input
through the run horizon, so unattended CLI runs finish without input waits;
scheduled typed commands can still be authored before launching.

In interactive mode, advance to 10 simulated seconds, then inject the event during
preparation. It extends the
remaining preparation of every active order at that kitchen by 60 seconds.
Waiting orders consequently start later when the occupied slot is freed. The
`lowalt.food.preparation_delayed` event records the actual affected order IDs;
an empty list means that no order was preparing there at the injection time.
Prepared meals and deliveries already underway are unaffected.

```bash
aeroagentsim run scenarios/templates/food-delivery/scenario.yaml
aeroagentsim metrics runs/<run-directory>
aeroagentsim replay runs/<run-directory>
```

This is the same independent AeroKernel runtime and generic console as the other
templates. Replay consumes the committed journal without model calls. Site
coordinates come from the existing public Huangpu template placements; restaurant
names describe modeled facilities rather than identified real businesses.

With seed 42 and the default two couriers, all six orders arrive by 321 s of the
360 s run; five meet the 240 s delivery promise. Mean order latency is 169.83 s.
Injecting a 60 s delay at the east kitchen at 10 s moves the first meal's ready
time from 38 s to 98 s and its delivery from 89 s to 149 s. All orders still
finish by 327 s, but only three meet their deadlines and mean latency rises to
187.50 s. These are outcomes from the same committed state and event stream used
by the console and replay.
