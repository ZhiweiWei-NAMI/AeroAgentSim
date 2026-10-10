# P02 Parcel Operations — local prototype

An independently authored, bilingual logistics visualization. It makes parcels, custodians, transfer stations, vehicles, rule inputs and response receipts visible on one deterministic replay cursor.

## Run

Node.js 24.15 or newer:

```sh
npm start
```

Open `http://localhost:4321` in the same execution environment. The server binds only to loopback and serves only frontend assets. There are no runtime dependencies, external API calls, paid models, private maps, or copied BENCH implementation/assets.

For tests, install the development dependency using the lockfile:

```sh
npm ci
npm run check
npm test
npm run build
```

The static distribution is `dist/`. This is local-only work; nothing is published by these commands.

## What works

- Chinese and English controls, state inspector and event text.
- Isometric schematic park, warehouse loading bays, visible parcel glyphs, ground carrier, drone, pad, transfer tray, relay and delivery lockers.
- Persistent parcel IDs and labels; scene or list picking; selection lock and follow view across pause, seek and replay.
- One 10 Hz authored replay, 0.1–84.0 seconds. Renderer and inspector receive the same frame key.
- Warehouse → UGV → transfer tray → UAV → locker. Parcels attach to the carrier during transport and appear once. One policy custodian is retained until each transfer receipt.
- Current ENU positions / velocities / units, attachment offsets, custody, source pointer and source frame.
- Scheduled delivery-phase communication degradation → demo predicate flip → hold → retry → replan, with later-frame execution receipts. The vehicle path visibly stops during hold and takes the alternate path after execution.
- Rule inputs, truth, latest flip time and fixed binding (`uav.01 × link.delivery`) are visible. Atlas itself remains explicitly unbound/unknown.
- Event timestamps can seek the shared cursor. Future script entries are separately marked and hidden by default.

## Truth boundary

All scene positions, dimensions, RSSI values, predicates, event receipts and trajectories are hypothetical fixture data. The fixture is not a physics engine, a live network simulation, a controller, a real BENCH replay, or an Atlas evaluator. It does not prove communication causality. Trajectories and rule/response records are authored to form a coherent example.

The parcel glyphs are enlarged for visibility. Their displayed size is not collision geometry. The static SVG park is illustrative ENU geometry, not a real-world map. The visual reference informed only the bright, blue-warehouse/isometric composition; the linked video's dynamic behavior could not be verified.

## Integration

See `INTEGRATION.md` for the framework-neutral component, real-data gaps and required host validation. BENCH remains the state/time authority. `src/fixture.js` is removable and must not produce live state. `normalizeBenchMotion` is a small field-path projection, not complete schema/integrity validation.

## Verification limits

See `VERIFICATION.md`. Functional and DOM-emulated tests are not browser visual QA. The existing cloud browser's localhost restriction was respected. No real-browser screenshot or responsive visual pass is claimed. The server works in the bounded HTTP test; separate tool processes could not reach the persistent preview session, so cross-tool preview availability is unverified.
