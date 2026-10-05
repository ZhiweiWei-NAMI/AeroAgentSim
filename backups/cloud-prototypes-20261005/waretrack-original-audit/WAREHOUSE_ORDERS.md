# WareTrack audit: warehouse, orders, docks, vehicles and game loop

## Scope and verification

- Source: `source/index.html`, pinned to commit `f2bd7329e95629bb09e761929c7ddbf3abfa6183` (verified current main).
- Source SHA-256 observed here: `68a0425674af24ecae5a74715653403c3de13678ac19b0357f1fbde5660c54ee`.
- Read every line **2031–2999**, in order, in six non-truncated chunks. Coverage ledger: `READ_COVERAGE.md`.
- Read targeted definitions/call sites elsewhere to establish actual rendered order UI, delivery handoff, timers, stock metadata and state reset. This is a source audit, not a browser/runtime verification. No application source was edited; no external writes were made.
- All line citations below are to `index.html` in this pinned snapshot. The main reviewed span is distinguished from cross-references where useful.

## Direct answer: does it display orders, and what else?

**Yes. It implements both an order display and an interactive, simulated fulfillment loop.**

1. **Order request cards:** customer/walk-in, one-pallet SKU, stock/free-stock state, countdown, and Accept / Rush +25 / Decline (`buildActs`, 2072–2085; `renderActs`, 2089–2105; action handlers 2106–2113).
2. **Orders tab:** pick queue, order number, SKU/customer, due/late/no-stock label, RUSH badge, Move to front, plus in-progress Picking and Staged rows (`renderPanel`, cross-reference 3514–3521).
3. **Order operations:** accept/decline/expire, rush priority, optional manual release, stock checks, attempted inter-site replenishment, automatic forklift pick/put-away, staging and shipping (`acceptReq` 2035–2046; `tryOutbound` 2329–2347; `prioritize` 2501).
4. **Order/shipment tracking:** Order placed → Picked → Staged → Shipped → Out for delivery → Delivered (`orderTrack`, cross-reference 1949–1957; `renderOutTrack`, cross-reference 3630–3639).
5. **Adjacent warehouse operations:** rack/SKU stock, pallet IDs/lot labels, forklift tasks/battery/charging/breakdowns, inbound shipments/trucks, dock occupancy/queue/detention/turnaround, supplier expedites, transfers, customer deliveries/vans, visual other warehouses, incidents and warehouse construction.
6. **Game progression:** free play, timed shifts, score/combo, stars, credits, XP, daily seeded challenges, achievements, guided tutorial, upgrades/build options. These are operating a fictional warehouse simulation, rather than evidence of a connected production WMS.

## Entities and relationships

| Entity | Implemented data and relationship | Evidence |
|---|---|---|
| SKU | Five hard-coded products, fixed units per pallet, visual color/height and popularity weight. Key is stored in order, pallet metadata and inbound cargo. | Cross-reference 767–774; 2158–2164; 2463–2466 |
| Order | `{id, sku, created, due, rush, late, shop}`; later `released`, `pickedAt`, `stagedAt`. One SKU represents one pallet, not editable line items or quantity. | 2463–2466, 2075, 2334–2344 |
| Request wrapper | `{o, t, ttl}` in `reqs`, separate from accepted `sim.orders`. At most six waiting requests. | 2031–2033; cross-reference 2029–2030 |
| Customer | Optional `shop` object, either connected city shop/home or eligible player shop: key, name, address, home flag, road tile, map coordinates. | Cross-reference 1653–1657 |
| Rack slot | Row `r`, horizontal index `i`, level `lv`, position, pallet reference, claimed flag, ID like `A-01-1`. Eight positions at each configured level. | 2228–2231 |
| Pallet | Three.js group, `sku`, synthetic metadata `{id, lot, recv}`, selection metadata. Lives in a slot, forklift carriage, staging position or bulk visual. | 2157–2165; cross-reference 3193–3198, 3318–3322 |
| Forklift task | `Put-away` references SKU/truck/destination slot; `Pick` references SKU/order/source slot/staging position; `picked` protects in-flight work from pre-pick cancellation. | 2318, 2338, 2349–2353 |
| Forklift | ID, temp flag, transform, step queue, task, last direction, battery, home/charger, carry pallet, movement parameters, broken-until and temp-until. | 2295–2305 |
| Staging position | Eight fixed positions with pallet, claim, elapsed time, order reference and map pin. | 2231; 2344; cross-reference 3937–3939 |
| Shipment | Generated ID, shipment code, carrier, SKU-array cargo, late flag, optional expedited SKU; scheduled ETA while en route. | 2398–2407, 2452–2454 |
| Active inbound truck | Shipment data plus bay, state, reviewed/unloaded counts, busy forklift, step queue, timings, detention count. | 2409–2417 |
| Dock/bay | Position, truck reference, door/light graphics, opening fraction, docked count. A bay can hold one truck. | 2140–2154 |
| Delivery job | Same order ID and order reference, customer, route distance, created/due timestamps, rush flag; later sent and delivered timestamps. | Cross-reference 1794–1798, 1814–1815, 1825 |
| Van | One or two delivery jobs depending on upgrade; route steps, driver, state, earned credits and return trip. | Cross-reference 1821–1845 |
| Other-site light simulation | Per-site docks, forklifts, random trucks, fill fraction, counters. This is deliberately lighter than rack/order simulation at the active site. | 2634–2779 |

## Order requests, manual actions and display: 2031–2138

### Request acceptance and shortage handling

- `offerOrder(o)` (2031–2034) drops/expires the oldest request when six already exist, adds the new wrapper with the 16-second TTL, plays feedback and marks the UI dirty.
- `acceptReq(r,rush)` (2035–2046) removes the request first. It looks at free unclaimed rack pallets and incoming **active** truck unreviewed cargo plus matching transfer trucks.
- Rush marks the order and sets `due = clockMin() + 25`; it inserts at the front, while normal acceptance appends. The “Rush +25” button does **not** charge 25 credits or call `spend(25)`. Base on-time staging points rise from 15 to 40, a 25-point difference, except the rush challenge modifier raises rush base points to 60 (2485). The deadline is 25 simulated minutes.
- If no free stock and no recognized inbound stock, acceptance attempts `requestTransfer`. Success spends **15 shift score points**, extends the deadline to at least transfer ETA + 20, and keeps the order. `spend` reduces game score during a shift, not the saved credits balance (cross-reference 991).
- If transfer fails, it adds a backorder counter / −15 score during shift and returns without putting the order in any persistent backorder queue. The request was already removed. “Backordered” here is a log/scoring outcome, not a retriable backorder entity.
- `declineReq` (2047) removes the request and reduces delivery rating by 0.05, floored at zero. `missReq` (2048–2053) reduces rating by 0.15, and in a shift increments missed requests and deducts 5 score. Both log their outcomes; expiry has lost sound feedback.
- `tickReqs` (2054–2057) increments real-time elapsed counters. Cross-reference `tickBody` 3916–3917 and 3947 confirms request countdown is based on `rdt`, not speed-scaled simulated `dt`; it pauses with the simulation and when document hidden.

### Autopilot / hands-on controls

- `setHands` (2058–2064) is disabled in tutorial, persists the setting, and toggles the three manual gates. Turning it off releases all queued orders and waiting deliveries. Hands-on makes positive score awards 25% larger (2512–2515), rather than increasing resource production.
- `frontQueued` / `manualDock` (2065–2071) act only on the front queued truck with a free dock, and prevent docking while the relevant yard lane is occupied by a departing truck.
- `buildActs` (2072–2085) prioritizes all request cards first. In hands-on it then includes the front truck at gate, up to three unreleased orders, and up to three unsent delivery jobs. It supplies due/wait text, severity, progress bars and button actions.
- `renderActs` (2089–2105) hides the panel while building/town planning; shows one card on width ≤760, otherwise three, plus a “more” count. Thus pending cards may exist beyond those currently visible. Its `force` parameter is not used.
- `doAct` (2106–2113) maps buttons to accept/rush/decline/dock/release/send; release merely sets `released=true`; send merely sets delivery `go=true`, leaving actual work to the simulation dispatcher.

### Delivery panel

- `ratingStars` / `renderDelPanel` (2115–2138) display net credits today, rating/stars, vans out/capacity, on-time tip streak, driver-gated districts, waiting jobs/due status/estimated fee, active vans, last six drops, purchasable Staff/Tools upgrades and lifetime totals.
- Waiting job and recent drop rows select that order's tracker; van rows select the van. Upgrade buttons use saved credit affordability and level/max ownership state.
- The accompanying fee descriptions match core delivery logic: early ≥10 minutes gives 25% extra, late pays half, rating affects fee, streak adds up to 25%, normal trips cost 2 credits and EV removes the trip fee (cross-reference 1788–1818, 2017).

## Physical warehouse entities and automatic task execution: 2140–2394

### Docks/models/inventory

- `buildBays` (2141–2154) builds selectable docks with color status light and opening door plus runtime truck/count fields.
- `makePallet` (2158–2165) draws a pallet and SKU-colored load and assigns a generated `PAL-` ID, generated `L25…` lot label and randomly backdated received time. No supplier batch entity is constructed.
- `labelTex` / `makeTruck` (2166–2201) cache/render branded carrier decals, truck body and expedited visual light. These are rendering, not a document/label-printing workflow.
- `makeForklift` (2202–2225) draws driver/forklift, applies selected cosmetic paint or temp colors, and exposes carriage, warning, beacon and body parts used by the simulation.
- `buildSlots` (2228–2232) creates 8 locations per rack row/level and 8 staging positions. `blocked` checks simulation-time aisle closure (2233).
- `stockBySku`, `totalUnits`, `occupied` (2234–2236) derive stock only from occupied rack slots. Pallets in trucks, forklifts or staging are not counted as rack stock. `free` excludes claimed stock; `pallets`/`units` include claimed pallets until physically picked.
- Bulk floor pallets (cross-reference 1222–1224) and wall pallets (1252) are rendered separately, outside `slots`; their visibility is not evidence that they contribute to rack stock or can be picked for an order. Charger session/kWh totals start from deterministic synthetic values (1233), then actual simulated charging increments them (2371).
- `seedRacks` (2237–2241) populates lower two levels using site seed amount and hard-coded SKU weights, with 2 chair pallets. `placeInSlot` (2242) updates the pallet reference, transform and world membership.

### Step engine, routing and put-away/picking

- `runSteps` (2245–2274) interprets move/turn/lift/wait/callback steps. Vehicle motion uses distance, yaw, speed and reverse settings; truck/van traffic checks can block movement. Lift speed is fixed at 1.6 units per time. Forklifts do not receive the truck/van traffic blocking condition in this function.
- `planner` (2275–2289) creates steps. Warehouse `go` uses one main aisle (`MAINX`) when changing row rather than general path optimization. `lx`/`place` (2291–2292) handle local/world offset and transforms.
- `addForklift` (2296–2306) allocates the first unused home spot, IDs, randomized regular battery (62–94), speed upgrades and crew-room multiplier, and fast-charger flag. Temps start at 100%.
- `freeSlots` / `nearestFirst` (2307–2308) require unoccupied/unclaimed/unblocked locations and rank by Manhattan horizontal distance plus level penalty.
- `tryPutaway` (2310–2328) selects the first unloading truck that has no busy forklift and unreviewed cargo, claims a free slot and increments the truck reviewed count. It favors ground level 60% when available and randomly chooses among the six nearest candidates. One truck pickup is busy at a time; once the pallet is collected, its busy marker is cleared while that forklift continues traveling to its rack.
- Put-away physically creates the pallet at pickup, moves/lifts it to its reserved rack, releases the slot claim, increments pallet/mission/moved/receive counters, and awards +5 shift score (2321–2324).
- `tryOutbound` (2329–2347) refuses all outbound work when the last row aisle is blocked or staging has no empty unclaimed position. It scans queue order, skipping unreleased hands-on orders and SKUs with no available unblocked pallet. It does not require strict FIFO across unavailable orders.
- Matching pallet selection is random among the four nearest ranked matching rack slots, **not FIFO/FEFO/lot-based**. It removes the order from `sim.orders`, reserves pallet slot and staging, attaches the pallet to the forks, clears rack stock at pickup and records `pickedAt`, then moves it to staging, records `stagedAt`, releases staging claim, and triggers on-time/late scoring.
- `abortTask` (2349–2354) only cancels tasks before pickup. Put-away cancellation clears truck/slot claims and decrements reviewed cargo. Pick cancellation releases both locations and unshifts the same order back into the queue.
- `goHome` (2355–2359) routes to the forklift's reviewed charger/home.

### Battery, breakdown and staffing

- `updateForklift` (2360–2388) freezes broken machines until repair time, then resumes. A breakdown after pickup preserves its carried pallet and steps because `abortTask` refuses picked tasks.
- Movement drains battery at 0.07 or upgraded 0.045 per simulated minute, multiplied by 1.8 under brownout. Charging is 2.5 or upgraded 5 per minute, doubled by fast charger. Below 22% an idle forklift heads home to charge until ≥92%. Idle-at-home machines also charge at half rate.
- Dispatch preference is outbound when orders exist and previous job was inbound, occupancy >78%, or any order is due in <12 minutes. Otherwise it tries inbound first, then outbound, then home/idle. It is autonomous task choice, not a UI for assigning an individual forklift to an arbitrary task.
- `hireTemp` (2389–2394) enforces at most two temps and available home slots, with default 60 simulated minutes. A temp only clocks out while it has no steps, so expiry does not interrupt an in-progress step queue.

## Trucks, cargo, docks and supplier replenishment: 2396–2460

- `newShipment` (2398–2408) randomly chooses carrier and generates cargo. Normal shipments have 3–6 SKU entries (+2 for heavy loads) and overweight SKUs with <3 rack pallets; >10 pallets downweights a SKU. Focused expedite ships four focused SKU pallets plus one random pallet.
- `spawnTruck` (2409–2418) creates an incoming queued truck, counters and fabricated pre-arrival confirmed/picked/loaded timestamps. Optional direct bay initialization creates a truck already unloading.
- `assignBay` (2419–2424) reserves a bay immediately, builds the approach/reverse docking route, then marks unloading, increments bay count and timestamps arrival.
- `dispatchQueue` (2425–2436) sorts queued trucks by position, automatically admits one at a time when no departing-lane conflict and not hands-on, moves the queue into positions and stamps gate arrival. In shifts, waiting >15 minutes causes −2 detention points per five minutes, capped at six charges (−12 per truck).
- `departTruck` (2437–2446) records turnaround from gate time and earns +25 if ≤45 minutes, +10 if ≤70, zero otherwise. It increments truck/fast counters and mission, departs on a route and releases the bay only after clearing it. The mesh and truck record are removed on exit.
- `updateTruck` (2447–2451) departs only when every cargo item has been collected (`unloaded`), no busy pickup/remaining truck steps, and no relevant docking/departing conflict. That does not require every unloaded pallet to have reached its rack yet.
- `scheduleTruck` (2452–2455) generates shipment, adds ETA and sorts en-route list. `rushInbound` (2456) detects transfer/expedite already coming. `expedite` (2457–2460) avoids duplicate focused replenishment and schedules the 5-pallet truck for 4 simulated minutes later. Callers apply the advertised −35 shift-score cost.
- Active-site outbound orders are **not loaded onto a modeled outbound freight truck here**. They sit at staging, then after 6 simulated minutes shrink and at >6.8 are removed and handed to `queueDelivery` (cross-reference 3937–3939). Modeled outbound loading trucks exist in other-site light simulation.

## Orders, lateness, scoring and incidents: 2462–2603

### Generation and fulfillment accounting

- `newOrder` (2463–2467) chooses SKU by popularity and a random eligible customer; default due is site SLA ± random offset (−4 to +6), rush is +25. IDs are incrementing synthetic `ORD-` numbers.
- `releaseOrder` (2468–2478) stops when accepted waiting queue already has 8 entries. During a real shift outside tutorial it offers the generated request for a player decision. In free play/tutorial it adds directly if free or recognized truck cargo exists; otherwise it logs a lost/backorder outcome.
- `comboMult` / `breakCombo` (2479–2480): every 3 consecutive on-time staged orders adds ×0.25, capped at ×2; lateness resets the combo.
- `onStaged` (2481–2495): shift-only. On-time standard order base +15; rush +40, or +60 with rush modifier; score receives combo, then hands-on bonus through `addScore`. It updates missions, ledger, rush/order counters, visual/sound effects and ×2 achievement. Late staged order receives +2 and breaks combo.
- `lateCheck` (2496–2500) scans queued and forklift-held orders, marks each late once after due, deducts 10 shift score, increments late-order count, breaks combo and logs. Staged orders are outside this scan because staging is the warehouse SLA completion point.
- `prioritize` (2501) moves any non-first accepted queued order to front; it does not interrupt an already reviewed forklift task.
- `checkLow` (2502–2509) outside tutorial warns when rack pallets ≤1, not recently warned for 70 simulated minutes, and no rush inbound exists. Choice is supplier expedite for −35 / 4 minutes or free wait. Its text says new orders backorder; actual accepted requests may attempt transfers first.

### Incident management

- `addScore`, `popup` (2512–2525) update shift score and transient world-space feedback. Positive hands-on awards gain rounded 25%; negative scores do not.
- `raise`, `showIncident`, `chooseIncident`, `renderIncident` (2526–2549) queue and show choices, default TTL 22 real-time seconds, spend chosen costs, execute callbacks and render buttons/queue count/timer. Cross-reference 3948–3951 executes the configured default on timeout. `chooseIncident` does not check an affordability threshold, and charges a cost before invoking the callback.
- `addSpill`, `clearSpills` (2550–2563) draw wet floor/cones and clear visuals when blocked-until expires.
- `rollEvent` (2565–2603) implements five incident families:
  - Breakdown: eligible regular forklift; pre-pick work aborted where possible; wait 90 simulated minutes or pay 40 score for repair by 20 minutes (2575–2580).
  - Spill: block an aisle for 60; pre-pick tasks in it are aborted; last aisle also blocks assigning outbound staging; pay 20 score to reduce to 8 (2581–2586). Existing picked tasks continue their planned routes, so the narrative “cannot use aisle” is broader than the actual assignment/cancellation checks.
  - Rush order: select an in-stock SKU; accept at front with 25-minute due / +40 or +60 base award, or decline for 10 score (2587–2591). Its timeout default is **accept**, unlike most incidents' free/wait default.
  - Shipment delay: first en-route shipment ETA +25 and late flag; pay 30 to add backup truck arriving in 3, or accept delay. Backup does not replace/cancel original truck (2592–2596).
  - Surge: double order generation for 45 minutes; pay 60 for 60-minute temp, or ride out (2597–2601). Hiring may fail on staffing capacity after cost has already been spent.

## Building visuals and other-site live simulation: 2605–2779

- `buildRoof` / `siteExtras` (2606–2632) render site-type variations: cold store plant, cross-dock sawtooth roof/external pallets, robotic site's solar panels/utility building, standard/depot roof and WareTrack logo. These visuals do not by themselves implement temperature, robotics or cargo compliance rules.
- `buildLite` (2638–2680) creates other warehouses' shells, docks, seeded fill, queues and counters, one forklift per two docks, and initially docked trucks. It excludes the active full site.
- `placeL` / `removeLite` (2681–2687) update/remove visuals and selections.
- `setStack` (2688–2695) shows up to three decorative random-SKU pallets per dock based on loaded/unloaded count. These do not correspond to a SKU-conserving other-site rack inventory.
- `spawnLiteTruck`, `liteDock`, `liteDepart` (2696–2722) produce random inbound/outbound trucks with 3–6 or 4–7 pallets, random/synthetic route metadata, queue/dock/depart states; completion adjusts fill by +0.035 inbound or −0.04 outbound, clamped 0.2–0.95, and truck on-time threshold is 70 minutes.
- `liteForklift` (2723–2742) is reviewed a fixed pair of docks, animates random pallet pickup/drop between door and yard, increments done counts and simple battery values; no rack slots, real SKU stock matching, charging routes or order relationship is modeled here.
- `updateLite` (2743–2767) generates trucks when queued <3; dispatches with lane/transfer checks; handles arrival, loading progress, departure, door animation/status light and forklift movement. Traffic rate is site truck interval ×0.55.
- `liteStats` / `liteTruckStatus` (2768–2779) expose fill, counts, completed/on-time trucks and **estimated** units `fill × site rows ×16×2×40`; default on-time before data is 96%. These are simulation/derived values, not live imported warehouse metrics.

## Site loading, modes, shift report and tutorial: 2782–2929

- `computeLayout` (2783–2787) applies purchased site layout: ≤5 bays, ≤4 rows, third levels, fast chargers, crew room and decor.
- `loadSite` (2788–2813) clears active vehicles, slots, stage, docks, spills, requests, popups and vans, resets simulation, rebuilds/seeds racks, spawns initial forklifts/trucks, schedules an inbound and seeds up to three stock-backed orders. Site activation is a fresh simulated operating state, not restoring detailed persistent warehouse transactions.
- `freePlay` (2814) clears modifiers/daily and uses normal random generation. `startShift` (2815–2826) exits building/town, configures normal/daily/tutorial mode, uses daily seed/site if applicable, reloads state, resets game score/counters, sets phase and starts the tutorial if requested.
- `endShift` (2827–2883) changes phase to report; calculates goal stars using received pallets, on-time staged orders and staging on-time ratio; clears requests; awards credits `max(0, round(score/6)) + 10×stars`, adds XP, tracks site bests/stats, creates achievements and saves report/ledger. Daily clears award 40 + 10×streak capped at streak 7 once per date, with consecutive-date streak tracking. Report has score, stars, met goals, on-time, credits, XP, unlocks, counters, ledger, combo and achievements.
- Source caveat: report ratio denominator is `shipOn + lateOrders`, not total delivered orders. These are warehouse staging SLA metrics, distinct from last-mile on-time deliveries.
- Tutorial definition `TUT` (2887–2898) has ten steps: introduction, camera, truck inspection, shipment tracker, goals, order priority, combo, breakdown choice, speed, wrap-up.
- `startTutorial`, `nextTut`, `renderCoach`, `clearSpot`, `tutTick`, `endTutorial` (2899–2929) disable hands-on and random incidents during coaching; pause/highlight/wait on task conditions; allow skip; restore prior hands mode; mark training seen; grant 50 saved credits only for first completed tutorial; save achievement.

## Warehouse construction and town-planner entry: 2931–2999

- `buildOptions` (2934–2944) excludes already-owned/pending items and provides: dock +1 (150 credits, max 5); extra row (160, max 4); third rack level +8 slots (70 per row); fast charger ×2 speed (45 per spot); crew room +8% forklift speed (130); purely visual decor (25 each).
- `showGhosts` (2945–2957) displays selectable transparent geometry/plus markers for eligible placements.
- `enterBuild` (2958–2965) requires between shifts/free play, changes report to free play, pauses sim and opens build UI. `exitBuild` (2967–2972) removes preview ghosts, restores UI/speed unless silent.
- `buyBuild` (2973–2981) validates saved credit affordability, deducts credits, queues warehouse construction and scaffolds, reports build duration, clears selected build option. Completion is delegated to construction functions outside this span, not instantaneous here.
- Town planner declarations/`buildTownOverlay` (2983–2999) establish Buy land / Road / House / Shop / Office / Depot / Park / Trees / Bulldoze tools plus a grid/hover overlay. Actual placement/edit rules occur after the reviewed span and are covered by the adjacent audit section.

## Important limitations and interpretation cautions

1. **Not just static UI:** order lifecycle changes real JavaScript state and visible vehicle/pallet behavior. Conversely, it remains simulated state generated from fixed catalogue, random events and game clocks.
2. **Not a full commercial order model:** one SKU/one whole pallet, no user-entered order form in this lifecycle, no partial picks, multi-line quantities, pricing/payment/invoice/returns/customer editing in these functions. The broader repository audit should confirm global absence before making an absolute whole-app claim.
3. **No actual batch allocation:** generated lot and received labels are inspected/displayed; no code reference uses lot for picking. `tryOutbound` chooses nearest/random available matching SKU. No FIFO/FEFO/expiry/recall behavior is evidenced.
4. **Stock availability is approximate:** acceptance doesn't reserve stock per order. Multiple accepted orders may await the same currently free stock; reservation occurs only when a forklift receives its task. Acceptance's `coming` check ignores the `sim.enroute` supplier list and pallets already reviewed to in-flight put-away, so it can request extra replenishment despite those sources existing.
5. **“Backorder” is not a retained backlog:** failed acceptance removes request then logs/penalizes and discards order (2041). Free-play/tutorial shortage also logs and drops it (2473–2475).
6. **Automatic shipment abstraction:** the active-site staging pallet disappears after >6.8 simulated minutes and becomes a delivery job only if a customer and reachable route exist. `queueDelivery` silently returns for missing customer or path (cross-reference 1794–1798), so not every staged shipment has a last-mile job.
7. **Other-site stock is lighter/fabricated:** fill-based estimated units, random pallet visuals and truck counters do not establish conserved cross-site SKU stock. `requestTransfer` selects nearby available yard rather than querying its actual SKU availability (cross-reference 1912–1915). `spawnXfer` does not subtract/reserve the source warehouse SKU stock (1878–1887), and `arriveXfer` materializes the destination cargo array on arrival (1896–1898). Thus the route/action is implemented but a conserved cross-site inventory transfer is not.
8. **No arbitrary per-forklift task assignment:** dispatch is heuristic and automatic. User controls queue priority, release, incident repairs, temps and upgrades, but not an editor assigning a specific order to a selected forklift in this span.
9. **Scores and credits differ:** incident/transfer/expedite costs flow through `spend` (shift score), while warehouse builds, delivery fees/trips and purchases use `save.credits`. Do not summarize every numeric “cost” as real money or even the same in-game currency.
10. **Defaults need careful wording:** ordinary incident copy says free option chosen, but rush incident default accepts rush. “Autopilot” still requires handling new shift order requests; it automates docks/pick release/van departure, not accepting customer requests.
11. **Heuristic routing and blockage:** main-aisle routes and limited vehicle checks are not evidence of production routing optimization, collision guarantees or aisle safety enforcement.
12. **Static audit confidence:** the functions and handlers exist and their state transitions were inspected; this audit did not execute the app or validate visual appearance/interactions in a browser.
