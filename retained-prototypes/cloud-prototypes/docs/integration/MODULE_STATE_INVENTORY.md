# Initial module and state ownership inventory

Status: source-inspected review draft, 2026-10-05. This is an initial inventory
of **14 source/ownership groups**, not a count of Atlas states or predicates.

## Pinned evidence

- Repository: `ZhiweiWei-NAMI/AeroAgentSim`
- Inspected branch: `codex/aerobench-source-config-20261005`
- Inspected commit: `e8ea3ab3ffcf19c6165fb13be2e1ab58cb421fe7`
- BENCH export source: `ea295073cdd310a31fa2e29317a3571bc716bb87`, as
  declared by [EXPORT_MANIFEST.json](../../aero-bench/EXPORT_MANIFEST.json).
- PR10 reference: [source-only binding prototype at 54c5cef](https://github.com/ZhiweiWei-NAMI/AeroAgentSim/tree/54c5cef4673f87dc738790ed3f026651f04e5ee7/validation/predicate-binding-prototype).
  It is a separate draft PR, not present in this branch.

The public BENCH export intentionally omits release image locks, sealed run
evidence, private truth, map/asset packs and prior validation outputs. Historical
real-run claims in its README cannot be reproduced from this export alone.
This documentation task has not connected a host, started a backend or run the
test suite. “Test source present” below is not “tests passed.”

## Inspected source groups

All source paths in this table are relative to `aero-bench/`. Line ranges refer
to the pinned commit. Canonical state concepts describe the mapping to build;
they do not rename existing source API fields.

| # | State / record group and computing authority | Concrete source | Declared / implemented | Test evidence and connection limit |
| --- | --- | --- | --- | --- |
| 1 | Flight command lifecycle and accepted command arguments: flight-command module | `aero_bench/gateway/dispatcher.py:157–267`, `aero_bench/providers/px4_gazebo/provider.py:2757–2828`, `containers/px4-gazebo/service.py:6634–6663` | Request/grant checks, provider dispatch and MAVSDK `session.action` calls implemented. Accepted target must be mapped from its own command evidence, never actual pose. | `tests/test_gateway_dispatcher.py`, `tests/providers/test_px4_provider_command_lifecycle.py`, `tests/providers/test_px4_multi_vehicle_command_dispatch.py` exist; not run here; current-host connection unverified. |
| 2 | Physical UAV pose/contact and motion observations: declared PX4/Gazebo motion authority | `containers/px4-gazebo/service.py:5386–5499`, contacts `:5919–6069`, pose `:6722–6850`; `aero_bench/runtime/contracts.py:378–430` | Gazebo pose/contact evidence and MAVSDK telemetry streams feed typed motion samples. Preserve truth/estimate/source distinctions. | `test_px4_pose_stream.py`, `tests/providers/test_px4_contact_stream.py`, `tests/providers/test_px4_flight_observations.py` exist; the latter manually seeds a telemetry cache. No live run here. |
| 3 | Agent-visible telemetry/GNSS: observation boundary | `aero_bench/providers/px4_gazebo/observations.py:31–238`; service `:6445–6589` | Position, velocity, attitude, mode, armed/in-air/landed, remaining percentage and health; GNSS optional quality values remain missing. Fused-telemetry and `gps_info` source timestamps are missing; `raw_gps` supplies `source_timestamp_us` with unspecified epoch/boot basis. Both observation contracts lack source-to-simulation mapping. | Unit fixtures exist. Receipt freshness means latest sample received before the barrier, not a recovered source timestamp. No live observation here. |
| 4 | Battery remaining fraction: PX4 observation authority | Service `:6299–6308`, `:9825–9831`; provider `:2484–2487` | `remaining_percent` maps to `remaining_fraction`. Current/voltage/consumed mAh/temperature are declared optional in the generic schema but null in this PX4 path; non-null values are rejected. | No measured Wh, charging current, battery heat or full power account established by this source. No test run here. |
| 5 | Declared energy and charging estimates: logistics energy module | `aero_bench/tasks/logistics/energy.py:1–59`, `BatteryState`, `EnergyStep`; profile in `aero_bench/tasks/logistics/fleet.py` | Deterministic cruise/hover/ground-charge Wh arithmetic, declared efficiency and cost calculations implemented. These are declared-input simulation/planning estimates, not battery telemetry. | `tests/tasks/test_logistics_energy.py` and `tests/tasks/test_logistics_energy_review.py` exist. Motion/payload/network/compute coupled energy accounting is not established. |
| 6 | Payload physical mass/inertia/attachment: physical module is the intended authority | Existing PX4/Gazebo boundary; compare declared cargo in `aero_bench/tasks/logistics/orders.py:316–345` | Payload ownership requirement is defined. This audit does not establish a parcel-specific runtime attachment/mass/inertia update interface. Business cargo mass is not proof of a Gazebo inertial update. | No end-to-end payload attachment verification claimed. Requires source-field and physical-effect mapping. |
| 7 | Order status, declared cargo mass/capacity and assignment: business module | `aero_bench/tasks/logistics/orders.py:316–345`, `:561–615`; `containers/logistics-business/service.py:1848–1862` | Pure state reducer and business requests implemented. Physical pickup/handoff/deliver/charge are explicitly refused pending authoritative physical-evidence binding. | `tests/tasks/test_logistics_orders.py`, business provider/service tests exist. Reducer evidence-reference checks alone do not verify physical transfer. |
| 8 | Pad presence/dwell/eligibility: named derivation module reading closed motion and declared facility geometry | `aero_bench/tasks/logistics/physical_observations.py:12–94`; `aero_bench/tasks/logistics/dwell_eligibility.py:59–64` | Bounded point-in-time presence and eligibility computations implemented; caller-supplied provenance and declared footprint/contact assumptions remain explicit. | `tests/tasks/test_logistics_physical_observations.py` explicitly uses synthetic values. Presence/dwell does not prove delivery, custody or charged Wh. |
| 9 | Directed link properties, queues, packet delivery: network module | `containers/ns3/aero-ns3-provider.cc:865–980`; `containers/ns3/server.py:4204–4268`; `aero_bench/providers/ns3/provider.py:1016–1085`, `:1245–1308` | ns-3 UDP/Wi-Fi execution, calculated RSSI/SNR/path loss, queue counts/bytes and simulated delivery records implemented. `network.send` completion is queue acceptance, with delivery pending. | `tests/providers/test_ns3_service.py` substitutes a Backend; `tests/providers/test_ns3_provider.py` substitutes transport. No current ns-3 run here. Per-link facts, provider aggregates and message cohorts require separate mappings. |
| 10 | Vehicle/person mobility and traffic lights: traffic module | `containers/sumo/service.py:1637–1721`, `:1740–1768` | TraCI vehicle/person position, speed, angle, lane/road state and traffic-light state/program/switch data implemented. | SUMO unit and optional container integration tests exist; container tests depend on prerequisites. No current SUMO execution here; arbitrary human activity coverage is not implied. |
| 11 | Static scene/facility declarations: scenario compiler; constraints retain their declared issuer | `aero_bench/providers/world_scene/provider.py:67–84`; `aero_bench/world/resolved.py`; `aero_bench/tasks/logistics/facilities.py`, `aero_bench/tasks/logistics/facility_geometry.py`, `aero_bench/tasks/logistics/runtime_airspace.py` | World-scene mirrors the digest-bound resolved scenario and owns no motion. Static geometry/capability/rule declarations must remain distinguished from live occupancy/environment observations. | World/facility tests exist. Export excludes asset packs; no claim that every declared facility/environment/regulatory field has a live producer. |
| 12 | Cloud-edge compute load/queue/latency: future named compute module | `aero_bench/config/models.py:323–326` (`ResourceBudget`) | CPU millicores, memory MiB and GPU count are deployment budget declarations. A general runtime compute-load telemetry Provider or SimGrid integration was not located in this bounded audit. | No generic compute runtime source/test/live connection established. Deployment reservation is not CPU usage or task latency. |
| 13 | Responsible custody and atomic resource admission: future live authority; PR10 has fixture authority only | PR10 `ledger.py`, `contracts.py`, `README.md` at the separate pinned commit | Fixture-only versioned custody and atomic resource bundle operations exist in PR10. This branch's P02 host does not provide live custody. Contact and proximity grant no responsibility. | PR10 reports 42 standalone tests, or 64 with external Atlas; historical reported results, not rerun here. No live durable/authenticated authority established. |
| 14 | Independent agent goals/policy/relations and decisions: explicit decision-layer scope | Existing `aero_bench/agent/`, Gateway principal/grant contracts; new semantic boundary in `TERMINOLOGY.md` | Existing participant/command infrastructure does not by itself establish the proposed one-to-many agent/entity relation registry, scope arbitration or parcel/station agent configuration. | No new agent-authority policy, relation implementation or test is claimed by this documentation change. |

## Runtime envelope versus P02 mapping

`aero_bench/runtime/contracts.py` declares `StateSample`, `SceneContribution`, `SceneState`,
`SimulationTime`, `CommandRequest` and `CommandReceipt`. The schema's optional
battery fields are broader than the implemented PX4 source. Its `sample_kind`
values (`static`, `dynamic`) are motion classifications, not a complete semantic
entity-type system. Do not infer a parcel/custody integration from generic strings
or attributes alone.

[The host README](../../aero-bench/host/README.md#unsupported-live-sources-precise)
explicitly lists missing parcel samples, custody/handoff records, Atlas results,
epoch/generation/revision, availability counters/timestamps and model dimensions
at its consumed public boundary. It implements a real-motion replay loading path,
but the exported screenshots are demo/fixture evidence. No sealed replay is
included in this source export.

`SceneStateAssembler` and motion-stage contracts preserve authoritative motion
and ENU/NED consistency. A new mapper must not generate missing samples from
initial pose or render interpolation. BENCH permits tick 0 in the contract;
accept the recorded tick identities instead of assuming contiguous array indices.

## Couplings still needing explicit fields

| Producer → consumer | Required distinction or dependency |
| --- | --- |
| Agent decision → flight-command module | Desired goal, issued request and accepted target are separate records; each authority/scope is explicit. |
| Physics/payload → energy | Actual load/attachment, motion and contributor interval; declared cargo mass alone is insufficient. |
| Network + compute → energy | Accounted contributor power/energy with an explicit interval and no double counting; currently not established. |
| Physics + facilities → charging | Presence/contact/connection, admission and actual charging evidence are separate. |
| Physics + transfer evidence → custody authority | Physical attachment/contact and responsible holder are distinct; handover requires explicit authorized evidence. |
| Facts → Atlas | Exact field signatures, units/frames, lifecycle, availability and rule revision; missing values do not become fabricated zeros. |
| Atlas → agent decision | Permitted observations and rule evidence inform decisions; rule truth does not itself authorize a command. |

## Verification labels to retain

- **Declared:** a schema, configuration or design entry exists.
- **Implemented:** the cited source path performs the operation within its stated scope.
- **Tested:** only when a named test actually ran at a pinned revision; state
  fixture/unit/container/real-replay scope and outcome separately.
- **Real-host connected:** only with current host identity, matching backend
  versions, successful source connection and traceable observations/receipts.

This inventory currently supplies source inspection and test-source references.
It supplies no new test-passage or real-host-connection claims. It must not be
summed as full state, predicate or activity coverage.
