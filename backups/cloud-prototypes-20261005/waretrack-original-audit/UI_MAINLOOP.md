# WareTrack source audit: UI, controls, inspector, town tools, and main loop

## Scope and confidence

- Snapshot: `source/index.html`, pinned main commit `f2bd7329e95629bb09e761929c7ddbf3abfa6183`.
- Reviewed spans read **every line in sequence**: 1–715 and 3000–4022, 1,738 substantive source lines total. See complete coverage ledger at the end. Byte-exact source verified against Git blob SHA.
- Cross-references read where necessary: 861–903, 1949–1970, 2788–2822, and 2958–2999. Definitions in other spans are covered by the other audit sections.
- This is a source audit, not a browser test. “Implemented” means there is actual source-backed UI and/or state-mutating behavior. No claims of rendering, performance, cross-browser correctness, or playthrough validation.
- No source changes or external writes were made. This report is the only created deliverable.

## Direct answer: are orders displayed?

**Yes. Orders are a first-class, implemented UI, not merely a decorative number.**

1. The lower-right panel has seven tabs, and **Orders is the initial selected tab**: Orders, Docks, Forklifts, Trucks, Vans, Stock, Log (690–697; `tab='ord'`, 3442).
2. `renderPanel` produces a **Pick queue** from the actual `sim.orders` array (3514–3519). Each row contains an order ID, product name, optional customer/shop name, time until due or overdue, RUSH flag, and “no stock” status when no free pallet exists. Non-first orders have a **Move to front** button, connected to `prioritize` (3456–3462).
3. A separate **In progress** section shows active forklift picks and staging-slot orders, with the forklift ID or “Staged, loading out,” due/staged-time status, and SKU code (3515, 3520–3521).
4. Clicking an order writes its ID to `game.trkOrder`, selects outbound tracking, and re-renders (3452). The bottom shipment card has six milestones: **Order placed → Picked → Staged → Shipped → Out for delivery → Delivered**, with available timestamps, destination/customer, delivery versus shipping deadline, status, associated van, and map-centering action (3630–3639). The supporting `orderTrack` reads queue, forklift, staging, waiting delivery jobs, vans, and recent delivery history (1949–1965).
5. Clicking a customer shop/home exposes open orders, delivery statistics, and a last-delivery tracking link (3256–3263).
6. The top HUD includes **Orders on time**, and shifts track “Orders staged on time,” on-time rate, score/combo, and a report (658, 3573–3575, 3602–3620, 3790–3816).

Important qualifications: Orders count is the **pending pick queue count**, not all orders ever created or all deliveries (3510). Completed delivery history is in the delivery subsystem, not retained forever in this pick queue. The tracker can be visually hidden by the expanded mobile panel, and an existing selected vehicle/customer can take precedence over a clicked order ID; see limitations below.

## Layout, CSS, and overlay inventory

### Whole page and design system

- Browser game metadata, viewport safe-area support, embedded SVG favicon, Google Fonts (Bricolage Grotesque, Figtree, JetBrains Mono) are declared at 1–11. Font loading is awaited for at most 1.8 seconds before boot (4016–4017). Three.js r128 is loaded from cdnjs, with a jsDelivr fallback script on load error (710).
- The root CSS defines light-mode colors for warehouse ground, translucent white glass, cobalt controls, safety yellow, success/warning/error, text tiers, radii, shadows, and fonts (14–34). Shared visible focus outlines and disabled button styling exist (84–88).
- `html,body{height:100%;overflow:hidden}` prevents document scrolling (74). The stage and HUD are fixed to all viewport edges (76–81). Canvas touch actions are disabled so the game handles pan/pinch (77). Body margin is explicitly zero (613).
- HUD is pointer-transparent by default, with children made interactive; the scene remains draggable in uncovered areas (80–81). Glass cards use blur/saturation, borders, and shadow (82).
- The main page intentionally has **no normal page scroll**; scrollable content is inside cards, panels, sheets, and dropdowns. This is source-backed layout intent, not a measured guarantee on every viewport.

### Desktop positions and internal scrolling

- Top bar: safe-area-aware top margin, left/right 16px, horizontal flex controls (90–138).
- KPI strip: top+74px, left 16px, initially four stat cards (140–152; markup 655–660).
- Left column: top+150px, left 16px, width 300px; shift goals, missions, incident, and action cards (155–197, 662–666). It has capped height and internal vertical scrolling with hidden scrollbar (51–52).
- Main tab panel originally defines a right-hand column but desktop overrides make it a **bottom-right card, height min(330px,40vh)** (201, 524–527). Its body flexes and scrolls internally, and tabs horizontally scroll (216–223). Its old title/chips are explicitly hidden by the newer layout (528–530).
- Inspector is a separate **top-right card**, width 340px with max-height avoiding the bottom panel, internally scrollable (467–496). It shows the selected object or site overview.
- Shipment/selection tracker: bottom-left; width up to 640px while leaving room for the right panel, with a main timeline and 190px side detail block (261–287).
- Camera buttons: vertical stack beside the right cards (290–295).
- Build/town mode hides tracker, left column and KPI cards and expands/replaces the tab panel (288, 526; state classes set in 3011–3018 and cross-reference 2962–2970).

### Overlay layers

- 3D projected selection tags are fixed DOM overlays at z-index 3 (298–300; updated 3973–3981).
- Screen-wide score pop container is pointer-transparent at z-index 2 (301–303); pops float upward/fade and expire after 1.5 real seconds (3988–3992).
- Confetti is a pointer-transparent full-screen canvas at z-index 8 (304, 620, 3994).
- Toasts are centered near the top, z-index 9, bounded width and slide-in/out animations (305–316, 621).
- Coach/tutorial card is z-index 4 and may highlight controls via `.spot`; hint text appears above tracker and disappears on interaction or after 14 seconds from boot (317–324, 704, 3906, 4013).
- Build and town mode bars contain their instructions, Done, and town Undo (325–327, 671–672).
- Loading screen covers viewport at z-index 10 until boot (328, 618, 4006).
- Modal scrim is full-screen at z-index 5, containing a bounded-width, scrollable white sheet; modal and sheet both permit internal overflow (330–353, 708).
- Site and notifications dropdowns have z-index 6, max-height 70vh and internal scroll (497–514). Desktop placement clamps horizontally to viewport; mobile placement uses 16px side gutters (3330–3333).
- Detailed CSS also styles store site cards, daily challenge, achievements, cosmetics, career stats, settings switches, reset confirmation and score reports (355–464), plus action cards/missions/tracker modes (36–73).

### Responsive behavior

- Search is hidden at widths ≤1500px (531; redundant ≤1280px rule at 534). Top button text hides ≤1380px (532). Profile text hides ≤1240px (533). Site chip and fourth KPI hide ≤1160px (535). The third KPI, tracker side detail, some clock/combo labels hide ≤980px (536–544).
- At ≤760px, the header is compact; brand text, profile chip, sound button, notifications button, camera zoom buttons, and several labels disappear (545–570, 590). All four KPIs return inside a horizontal scroller (558–563).
- Left cards become full-width beneath the KPIs. Shift goals collapse into a summary until opened (564–568; mobile click logic 3622).
- Tab panel becomes a rounded **bottom sheet**, max-height 55%; its body is hidden until `.open`, and selecting a tab opens it (571–578, 3445–3448). Build panel max-height is 48%.
- `updateSheetVar()` measures panel height into `--sheet`, allowing tracker, coach, hints, and camera controls to sit above it (569, 579, 586, 591; 3369).
- The inspector is normally absent on mobile but reappears for a selected object at top+116px with max-height 40vh; while inspecting, the left column is hidden (587–589).
- Tracker milestone timestamps are hidden on phones, and steps are smaller (580–584). Expanded mobile panel hides tracker entirely; coaching can also hide it (607).
- Modals align to bottom on phones; cards/grids collapse to a single column, career stats retain two columns, and scores/stars shrink (593–605).
- Reduced-motion CSS disables a listed subset of animations/transitions (608–611), but is not a global simulation or animation stop.

## Implemented UI and actions

### Top bar / utility controls

- Menu via brand and M; site dropdown; clock and shift time-left/progress; score or “Free play”; combo multiplier and pips; pause/1×/3×; town/build toggles; notifications; sound; level/profile; **Call truck** (624–653, 3846–3855, 3864–3905).
- **Call truck** schedules an additional inbound truck two simulation minutes away, logs it, and opens Docks on desktop; it is blocked only by warehouse build mode in this handler (3870).
- Notifications are in-memory, capped at 30 notices; dropdown displays the newest 14, counts unread up to `9+`, marks read when opened, and supports clearing (3327–3348, 3848, 3856). This is an implemented log-style UI, not external push notifications.
- Search is Enter-triggered substring matching: active-site trucks/forklifts by ID or carrier, queue orders by ID (opens Orders), products by name or SKU (opens Stock and highlights for 3.5 seconds); failed search selects the input text (3897–3904). It is not a full database/live filter search.

### Tab panel

- **Orders**: described above; reordering does mutate real queue state through `prioritize`.
- **Docks**: each bay’s truck/carrier/pallet count, rush status, unloading progress; free bay status; waiting gate queue and detention warning; booked arrivals and estimated minutes/“Arriving” (3527–3537). Occupied rows select and focus the truck (3463).
- **Forklifts**: count working/total, each forklift’s task, break-down countdown, battery percentage/bar, temp-driver expiry, selected highlighting; **Hire temp** dispatches `hireTemp(60)` and applies the shift score cost if successful (3538–3543, 3461).
- **Trucks**: current truck rows including departing trucks, carrier/bay/gate, current state, selection and map centering (3544–3546, 3463). The count itself excludes departing vehicles (3511, 3545).
- **Vans**: calls the implemented `renderDelPanel` from the delivery subsystem (3547–3548). Parent/other span audit owns the full contents. The delegated click handling supports delivery upgrades (`buyDel`), order tracking and selecting/focusing vans (3451–3453).
- **Stock**: each SKU color/name/code, pallets, units, Out/Low/In stock; low stock offers inter-warehouse **Transfer** and **Expedite**, or a Rush inbound flag (3522–3526). Handlers call `requestTransfer`/`expedite` then `spend(15/35)` if successful (3459–3460). `spend` affects score only during a shift, not persistent credits (991 cross-reference from search).
- **Log**: up to 50 newest current simulation log entries with game clock and severity (3549–3551).
- Re-rendering retains tab panel scrollTop (3553). HUD re-renders all main cards roughly every 0.4 real seconds or immediately when `dirty` (3999).

### Inspector and object selection

`pickAt` raycasts vehicles first (active trucks/forklifts, background trucks/forklifts, vans), then pallet slots/staging, bays and chargers, then selectable town objects and background warehouse structures (3417–3425). Sprite hits are ignored. Selection adds blue brackets, translucent body/ground highlights, projected name/state tag and moving-agent route; closing deselects. Changing selection clears camera follow (3148–3161, 3199–3215, 3434–3439).

Actual inspector variants (3234–3325):

- **No selection / active warehouse**: operational/incident state; docked/arriving/staged counts; occupied slots versus capacity; bay utilization; outbound orders today; put-aways; top four inventory SKUs and stock levels; first working forklift/battery and working count (3240–3254).
- **Background site**: utilization, bay occupation, trucks turned, on-time departures, working forklifts, up to four truck rows, network transfers, description, and **Run this site**, disabled during shifts or while locked (3219–3232, 3255).
- **Shop/home customer**: name/address/customer type, open-order count, local/served-by site, delivered/on-time/paid totals, up to five trackable open orders or last delivery (3256–3263).
- **Transfer truck**: source/destination warehouses, pallets/mixed or primary SKU, departure, ETA, route progress and representative speed (3264–3268).
- **Delivery van**: driver, route state, next customer, promised deadline, drop count, trip earnings, representative speed, customer rating (3269–3273).
- **Background truck**: carrier, generated driver/plate, direction, origin/destination, shipment, representative speed, bay, cargo and progress (3274–3280).
- **Background forklift and bay**: task/battery/carry/moves/fork height/site; truck/door-open percentage/trucks today/site (3281–3290).
- **Active truck**: carrier, generated driver/plate/shipper, shipment, status and approximate ETA/speed, bay, unloaded/total pallets and cargo tonnage (3291–3299).
- **Active forklift**: operator/model, task/fault countdown, battery, exact carried pallet/SKU, moves, representative speed, fork height, reviewed charger, site (3300–3307).
- **Active dock**: truck/state, reverse/unload/depart description, rear-door opening percentage and daily truck count (3308–3312).
- **Charger**: reviewed forklift, Free/Charging, battery, sessions, cumulative energy, modeled charge rate, site (3313–3317).
- **Pallet**: product/category/code, pallet ID, Stored/Staged/Moving/Bulk location, units, calculated gross weight, lot, received time, site (3193–3197, 3318–3322).

Driver/model/shipper/license-plate enrichment is deterministic from arrays/hashes, not a connection to real carrier telemetry (3128–3134). Some quantities/speeds/ETAs are explanatory approximations (e.g. trucks display 22 or 5 km/h when position changed, not measured velocity; 3276–3280, 3295–3299). These are substantive simulated entities with enriched display metadata, not external operational records.

### Tracking and routes

- Inbound tracker follows a selected active truck/forklift, otherwise a requested incoming transfer, otherwise an unloading/docking/queued/current truck (3623–3627).
- Inbound/outbound toggle is appended to tracker headers; switching clears relevant active selection and chosen order (3629, 3857).
- Transfer tracker shows Requested/Loaded/On road/Gate/Unloading/Received and live ETA while transfer exists (3652–3657).
- Background-truck tracker includes confirmed/picked/gate/docked/loading or unloading/received or departed milestones with available timestamps (3658–3665).
- Active inbound tracker uses confirmed/picked/loaded/gate/unloading/received milestones and carrier, source warehouse, bay/gate and pallets remaining (3674–3685).
- Selected active forklift replaces the timeline with task/battery/moves/fork-height/load information (3667–3673).
- Routes draw the selected truck, forklift or van’s queued move steps, up to roughly ten points. Initial segments are solid until accumulated distance passes ten world units; remaining segments are dotted; final visible point gets a pin (3162–3177). This is a preview of **already planned movement**, not user-editable route planning or a live road navigation service.
- Selected routes rebuild each 0.3 real seconds; brackets and projected tags move each frame, and Follow advances target to the selected object (3973–3983).

## Camera, input, and network browsing

- Orthographic isometric camera with fixed elevation (`EL=0.6155`), smooth target/azimuth/zoom, responsive default framing (3354–3380). Camera also controls cutaway back/left wall visibility, distance fog and a modeled dawn/noon light/ground tint.
- Pointer capture supports pan, tap selection and two-touch pinch. Movement >6px starts drag, cancels following, hides hint, marks tutorial pan achieved; clicks shorter than 500ms pick an entity, build ghost or apply a town tool (3383–3413).
- Pan target bounds: x −250..260, z −130..140. Wheel/pinch zoom: 0.08..3.2. Zoom-in button has a 0.55 minimum (can jump in from network scale), zoom-out down to 0.08. Rotation buttons move by quarter turns; Home resets azimuth/default view (3390–3398, 3414, 3878–3882).
- Site dropdown shows **all five sites**, capacity and docks, active/watched state and locks; Network overview zooms out to all five (3334–3343, 3850–3855).
- Switching/activating a site uses fly animation and only activates if unlocked and not currently in a shift (3349–3353, 3964–3965). During an active shift you can watch another site; your active shift continues. Locked sites can still be watched (3854–3855).
- Site framing is inferred from camera x target via `viewIdx` (3218). Top KPIs and no-selection inspector follow the viewed site, while the lower operational tabs remain the actively managed site (3498–3511 versus 3555–3579).
- Keyboard: Space pause/resume; B build; T town; M menu; / search; Esc exits town/build or selection, or dismisses menu/resumes shift; 1/2 choose incident options; Ctrl/Cmd+Z town undo (3884–3895). Search-focused Escape only blurs it. There are no keyboard arrow/WASD camera controls here.

## Town-planner operations in this span

- Enter town exits build, closes menu, displays grid/ownership tint and planner panel, records previous camera, deselects, and zooms out to town (3000–3019). It does **not itself change simulation speed**.
- `tileInfo` distinguishes city/private roads, own warehouse grounds, existing shop/home customers, public property, for-sale Unit 7, owned Depot 2, sale/owned grass and player buildings (3021–3035).
- Tools from cross-reference 2986: Buy land, Road, House, Shop, Office, Depot, Park, Trees, Bulldoze.
- `applyTool` enforces ownership/funds and valid build sites (3037–3080):
  - Buy vacant grass and save ownership; land price depends on prior helper.
  - Buy Unit 7 for the configured credit cost, convert its tiles to owned depot, recompute income, celebrate, unlock achievement; this special purchase clears Undo.
  - Bulldoze player-built supported buildings/roads for 40% of building cost; cannot demolish pending construction with the ordinary bulldozer.
  - Most building types need an adjacent road; roads, trees and parks do not.
  - Roads build immediately; other buildings create pending construction using `queueConstr`. Shops receive cyclic generated names/colors.
  - Credit balance, building/ownership data, achievement, tint and economics are updated.
- Single-action **Undo** stores last plot state and cost; it cancels matching pending town construction, restores ownership/building state, reverses cost, then clears itself (3081–3088). It is not arbitrary history/multistep undo.
- Planner panel lists credits, gross hourly income, upkeep, building count, current tile, tool descriptions and costs. It explains road connectivity, player shops becoming delivery customers, and automatic junction lights (3090–3101).
- `townAccrue` adds modeled net hourly income into a fractional bank, transfers whole credits and shift-town earnings, charges negative net without going below zero credits, and periodically writes save (3103–3108). It runs only with nonzero simulation `dt` (3993).
- Hover highlights tile with ownership/sale color and projected title/tool (3115–3124). `resetTown` clears/rebuilds town/world-related state (3110–3113).

## Menus, shop, settings, and progression display

- Modal has **Play / Daily challenge / Shop / Achievements / Profile** tabs (3742–3748).
- **Play**: guided training offer; selectable unlocked site cards with stars, docks/rows/forklifts/build count/difficulty/best score; eight-minute shift goal/rule explanation; advice; Start shift or Free play (3701–3711, 3749–3756, 3783–3787).
- **Daily**: date, selected site, two modifiers, target, local best, credit reward/claimed state, current/best streak and seven-day clear history; daily may preview a locked site; Start/retry action (3729–3731, 3757–3763).
- **Shop**: town planner, global upgrades, forklift paint, delivery-van livery, and per-site building mode (3713–3727, 3764–3766). Global upgrades are disabled in shifts and take effect from the next shift per UI; permanent builds and currency are actual persistent game state.
- Purchases debit credits and save; cosmetics unlock/equip and forklift colors update on current nontemporary forklift models; settings handlers actually apply audio and persist (3822–3826). Van livery runtime application is defined outside this span.
- **Achievements**: list of all achievements, earned/locked appearance and acquisition date (3767–3768).
- **Profile**: career totals for shifts, put-aways, shipped/rush orders, 3-star shifts, best score, fixes, builds and streak; shortcuts; independent sound/music toggles, volume 0–1; replay training; local-save explanation and **two-step Reset all progress** (3769–3781, 3841–3843).
- **Report**: shift/daily score, stars, earned credits, animated XP progression, promotion/daily/unlock/hands-on bonus/missed requests/delivery income/town income/new achievement notices, goal outcomes, best combo, ledger of positive and negative scoring, replay/next warehouse/shop/menu (3789–3819).
- Active-shift menu offers Resume / Restart / End now; report links create a fresh free-play world or replay/next shift (3784, 3827–3843).

## Persistence and reset semantics

- Cross-reference 865–884: localStorage key `waretrack.game.v2`; defaults include best scores/stars, credits, upgrades, builds, XP, achievements, career totals, daily streak/history/best, audio settings, tutorial completion, town ownership/buildings/bank/names, owned/equipped cosmetics, construction queue and cumulative simClock. V1 best/credits/upgrades migrate. Hands-on mode is restored from settings asynchronously (883).
- `writeSave` refreshes lastSeen and catches storage errors without notifying the user (884). Writes occur at purchases/settings/town actions, every 20 real seconds and on visibility changes (4011). There is no backend account/cloud save in this code.
- This is **progression save**, not a complete resumable simulation. Cross-reference `loadSite` clears vehicles/pallets/orders, resets simulation, seeds racks/forklifts/trucks and starter orders (2788–2807). Reload/site change does not resume the exact active order queue, movement and shift state.
- Reset replaces save with defaults, writes, restarts free play at site zero, reapplies audio, emits toast and rebuilds town (3843). UI requires clicking Reset then Delete everything; ordinary code does not request browser confirmation.
- Boot chooses the highest unlocked site, reveals HUD, loads that site, starts at 1×, restores sound button, selects daily menu if eligible, calculates welcome-back earnings and opens menu (4002–4017). Welcome-back message reports approximate simulated productivity (3740); audit verifies it is formula-based bounded idle income, not a fully executed offline warehouse simulation.

## Main simulation/render loop and time control

- `tick` catches/logs a thrown frame error and always schedules the next frame (3911–3913). Errors may therefore recur in console without any on-screen error state.
- `tickBody` clamps wall-clock frame delta to at most 0.05 seconds; hidden tab sets it to zero. Simulation `dt=rdt*sim.speed`, with UI values 0/1/3 (3867–3869, 3915–3917). One simulation unit is one modeled minute via `clockMin=sim.startMin+sim.sec` (989); eight simulated hours take about eight real minutes at 1× as the menu explains (3753).
- With `dt>0`, the loop advances shift sim time and persistent construction clock; progresses construction; generates orders on configured cadence (surge doubles timer rate); schedules inbound trucks when en-route count is under three; spawns arrivals while fewer than four trucks queue; dispatches bays; updates active trucks, background sites/transfers, forklifts; removes expired temp forklifts (3918–3936).
- Staging pallets remain for six simulation minutes, shrink over the following 0.8, then disappear into shipment: increment shipped count, call `queueDelivery(order)`, and log shipped/on-time status (3937–3939). Thus loading-out is a timed animated handoff into the last-mile subsystem; this span does not attach that pallet to a fully modeled outbound main-site truck.
- About every simulation minute it checks overdue orders, clears expired spills, rolls shift events, checks low-stock warnings and ends the shift at SHIFT_LEN (3940–3946).
- Requests and incident response timers use **real frame delta** while running, not simulation-scaled dt; incident expiry selects its default option (3947–3951). 3× speeds yard/order movement but leaves real response windows unchanged. Pausing freezes these because they are inside the `dt>0` branch.
- Tutorial progression runs outside the simulation branch (3954).
- Outside the branch, dock doors interpolate on real delta, lights reflect occupancy, ghost plots pulse, camera/zoom and site fly animate, tutor selection ring pulses, selected-object tags/brackets/routes update, pins bounce, gates lift by proximity, network pins scale at low zoom, warehouse shell fades based on zoom/town mode, and score popups animate (3955–3992).
- `updateCars` receives real delta multiplied by min(speed,2) or **0.5 when paused**, explicitly preserving scenic traffic motion; delivery vans receive true simulation dt. Town credits only accrue when dt>0 (3993). Confetti, music, rendering, clock label and timed/dirty HUD refresh follow (3994–3999).
- **Pause is a logistics clock pause, not a frozen image.** Camera, some animations and decorative cars continue. Hidden-tab simulation is stopped; missed real elapsed time is not integrated by this loop.
- Opening the menu auto-pauses only during an active shift; free-play simulation continues behind it (3692–3696). Resume/Escape restores previous nonzero speed (3833, 3886). Space resumes at 1×, rather than restoring 3× (3890).
- Warehouse build mode pauses and disallows speed/call-truck controls; town mode does not itself pause (cross-reference 2958–2971; 3869–3870; 3010–3014). A town planner opened through an already-paused shift menu inherits that pause until the user changes speed.

## Source-backed limitations and edge cases

These are static findings, not browser-observed failures.

1. **Order click versus current selection conflict.** Order-row handler sets `game.trkOrder` without clearing selected entity (3452). Tracker gives a selected van or destination precedence, and selected active truck/forklift forces inbound/forklift tracking (3644–3648). Consequently clicking an order can highlight its row while showing another tracked entity. Customer inspector order links similarly set an ID, while the destination branch selects its first open order (3861 versus 3645).
2. **Mobile order tracking can be hidden.** Selecting a tab opens `.panel` (3445–3446), and `.panel.open ~ .track{display:none}` at ≤760px (607). Clicking a queue order does not close panel (3452), so its tracking result is not immediately visible until the sheet is collapsed.
3. **Watched-site versus managed-site mismatch is intentional but easy to miss.** KPIs, top chip and no-selection inspector change with camera/viewIdx; tab panel operations still read active-site arrays. Watching another site does not let that Orders tab manipulate its background orders (3218, 3240–3241, 3498–3551, 3555–3579). The shift/free-play info card explains which warehouse is managed (3595–3598).
4. **Search availability is narrow.** Search CSS hides it at ≤1500px, including many desktops and all phones (531). `/` still calls focus on that hidden element (3888); no mobile search overlay or alternative is implemented. Search excludes vans, background vehicles, completed/in-progress orders and customer names unless found through another UI (3897–3904).
5. **Inspector “links” are mostly styled text.** Shipment, bay, truck, charger and forklift fields often wrap values in `<span class="link">` but have no anchor/action binding (3290, 3299, 3307, 3312, 3317). The inspector event handler only recognizes specific `data-*` controls (3860–3863). The blue styling alone does not make those values navigable.
6. **Approximate tracking states.** For active inbound trucks any state besides unloading/departing maps to milestone index 3 (“At gate”), including approaching queued/docking trucks (3675–3678), even when detailed status says “Driving in.” Transfer tracker fixes current stage to “On the road” while a transfer object exists (3654–3655). It is a useful game timeline, not authoritative shipment-event telemetry.
7. **Orders-on-time metric is warehouse staging performance.** Active shift KPI uses on-time staging versus late-order count (3573–3575, 3602–3606); delivery on-time metrics are separate. Free play displays “— / starts with a shift.” Background-site KPI instead shows modeled truck on-time departures (3561); its static card heading remains “Orders on time” (658).
8. **Persistence robustness is limited.** Save writes silently ignore exceptions; there is no cloud sync, export/import, or exact in-progress simulation restore here (865–884, 2788–2807, 3779, 4011). Notifications are session-only.
9. **Modal accessibility is partial.** Dialog/tab/button ARIA, focus-visible and polite live regions exist (86, 621, 640, 683, 690–708, 3748), but these source spans implement no focus trap, return-focus management or tab-arrow keyboard semantics. Escape closes a menu, not a report (3886). Reduced-motion does not disable every animation or scene motion (608–611, 3955–3995).
10. **No universal border-box reset.** Dimensions use content-box defaults; cards/sheets have explicit widths and padding (201, 262, 332, 468, 571–599), so fixed viewport / no document scrolling should not be taken as a proof that every narrow or short device avoids clipped/overflowing overlays. This needs browser QA, not a source-only visual claim.
11. **Frame-clamp timing and errors.** Frames slower than 50ms discard elapsed simulation time instead of catching up (3916). One-minute checks reset accumulated slowT to zero rather than preserving remainder (3941). Tick exceptions are console-only and retried each frame (3912–3913).
12. **Dynamic graphic cleanup is incomplete.** Selection brackets/routes remove old children without calling geometry/material disposal (3153–3158, 3165–3176); route rebuilds every 0.3 seconds (3977), including new pin shadow materials (3145). Potential long-run GPU resource accumulation needs runtime profiling; it is not a proven memory/performance measurement.

## Implemented versus scenic versus absent in this span

- **Implemented operational gameplay:** simulated orders, priority queue, stock shortages/resupply, docks and inbound arrivals, forklifts/battery/tasks, staging shipment handoff, delivery tracking, transfers, events/missions integration, build/town purchases, income, upgrades, local progression and complete controls/menu/report UI.
- **Implemented scenic/illustrative behavior:** decorative road traffic still moving when paused; smooth door/gate/marker/roof/camera animation; generated operator/license metadata and simplified speed/ETA readouts; idle-income summary. These should not be described as real-world live logistics telemetry.
- **Not evidenced as implemented here:** live OMS/WMS/carrier integration, user-entered order CRUD or import, server/account/cloud sync, editable vehicle route dispatch/map navigation, long-term order archive/report export, real supplier/customer communication. The implemented actions are game commands and local simulation state.
- No major “placeholder tab” was found among the seven operational tabs or five menu tabs: handlers/content branches exist. Certain blue inspector fields are noninteractive text and some telemetry is deliberately simplified.

## Sequential complete coverage ledger

Each bounded `nl -ba | sed -n` read below returned completely with sufficient output budget. Initial unnumbered 1–180 preview was repeated as a numbered complete read; it is not used as sole coverage evidence.

| Sequential read | Span | Coverage |
|---|---:|---|
| 1 | 1–180 | Metadata, dependencies, root theme, action/mission CSS, viewport reset, header/KPI/left-column CSS |
| 2 | 181–360 | Incident, panel, tracker, camera, overlay, modal and initial site-card CSS |
| 3 | 361–535 | Remaining menu/report/inspector/dropdown CSS; desktop panel overrides and breakpoints |
| 4 | 536–715 | Remaining responsive/reduced-motion CSS; full HTML HUD/overlays; Three.js loader; JS opening |
| 5 | 3000–3139 | Town ownership tint, mode entry/exit, tile info, tool mutation/undo, economy, hover, inspector metadata/pin setup |
| 6 | 3140–3274 | Pin, brackets, routes, selection position/labels, lite-site card, inspector through van |
| 7 | 3275–3412 | Remaining inspector, notifications/site dropdown, camera, pointer pan/pinch/click |
| 8 | 3413–3556 | Picking/selection, panel event handlers, all panel rendering, KPI start |
| 9 | 3557–3697 | KPIs, shift goals, all tracking displays, menu opening/pause |
| 10 | 3698–3817 | Site cards, upgrades/cosmetics, menu tabs/settings, report rendering |
| 11 | 3818–3943 | Modal/action binding, top bar/keyboard/search, main simulation loop start |
| 12 | 3944–4022 | Main-loop remainder, animated scene/HUD, boot and closing HTML |

Union is exactly 1–715 and 3000–4022 with no internal gaps. Final end-of-file is `</html>` on 4022. 
