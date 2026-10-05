# World vertical performance model — phase 1

Status: **design document, phase 1 only.** It defines the capacity equations
and names every parameter as a configuration or measurement input. Values
that come from real repository configuration are quoted with their source.
Measured values quote the exact command that produced them, so every number
in this document is either re-derivable from configuration or re-computable
by re-running a committed command. Quantities no benchmark has produced are
marked `UNAVAILABLE` and no number is written for them. No capacity claim in
this document is a benchmark result, and no number here may be presented as
engine evidence.

The authoritative tick is the unit of progress. The Harness advances one
tick only after every required Provider returns a valid `StepReceipt`
(`aero_bench/runtime/barrier.py`, `commit()`). Every ceiling below is a
consequence of that rule plus declared budgets.

---

## 1. Notation

| Symbol | Meaning | Source |
| --- | --- | --- |
| `Δphys` | Gazebo physics step size (ns per physics step) | PX4 image selfcheck only; formal PX4 Provider is BLOCKED |
| `k` | authoritative tick duration (ns) = `clock.step_ns` | config: `EnvironmentSpec.clock.step_ns` |
| `m` | physics steps per authoritative tick, `m = k / Δphys`, must be an integer | derived |
| `T_p` | per-tick latency of the slowest required Provider (s) | measured |
| `N_e` | number of simulated entities | config: `WorldPackage.entities` |
| `c_proj` | per-entity projection + canonical serialization cost (s/entity) | measured |
| `S(N_e)` | public snapshot bytes at `N_e` entities | measured |
| `R_tx` | effective transfer rate to the viewer (B/s) | measured |
| `F_cs` | Cesium frame budget (s/frame) | config: viewer target (e.g. 1/60) |
| `c_cs` | per-entity Cesium entity update cost (s/entity/frame) | measured |
| `M_e` | viewer memory per entity (B) | measured |
| `M_budget` | viewer memory budget (B) | config |
| `B_tile` | building/3D-tiles tile count | config: derived from `WorldPackage.buildings` |

## 2. Gazebo physics step → theoretical maximum authoritative tick frequency

The tick is authoritative only when physics has advanced `m` steps, so:

```text
f_tick_max_phys = 1 / (m · Δphys_sim_cost)
```

`Δphys_sim_cost` is the *wall-clock* cost of simulating one physics step
inside the pinned Gazebo image — not `Δphys` itself. `Δphys` (simulated
nanoseconds per step) only bounds the *simulated* rate:

```text
sim_time_rate = Δphys / k · (1 tick per m steps)   → always 1× sim speed by construction
```

The retained pinned PX4/Gazebo image self-check runs
`PHYSICS_STEP_NS = 4_000_000` (4 ms) for `PHYSICS_STEPS = 2_500` steps and
requires exactly 10 s of sim time (`containers/px4-gazebo/selfcheck.py:21-22`).
This is a real external-engine measurement input only. Current main has no
PX4 Provider registration, RPC service, or shared readiness path, so any
formal PX4 Provider run is **BLOCKED** and no PX4 barrier capacity is claimed.

```text
f_tick_max_phys = 1 / Δphys_wall_per_tick
Δphys_wall_per_tick = m · Δphys_wall_step          (Δphys_wall_step: UNAVAILABLE)
```

Benchmark that must fill the value: run the existing self-check
(`containers/px4-gazebo/selfcheck.py`, which verifies the `PHYSICS_STEP_NS`
and `PHYSICS_STEPS` constants above) inside the digest-pinned image under
the mandated workload controls, and time the run:

```text
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  aero-bench/px4-gazebo:<digest> <selfcheck entrypoint as built>
```

wall-clock seconds per batch → `Δphys_wall_step = wall_s / 2_500`. Result:
**UNAVAILABLE** (the retained image selfcheck is not a formal PX4 Provider
run; the formal path is **BLOCKED**).

## 3. Slowest required Provider → Barrier upper bound

The Provider Barrier waits for every required provider each tick, so the
commit rate is bounded by the slowest one:

```text
f_tick_max_barrier = 1 / max(T_ns3, T_sumo, T_world_scene, T_harness_overhead)
f_tick_max         = min(f_tick_max_phys, f_tick_max_barrier)
```

Each `T_p` is measured per provider over the RPC boundary used in phase 1
(JSON-line request/receipt canonicalization); the harness overhead term is
the cost of ledger append + receipt validation measured in-process.

Adapter-level canonicalization cost of one `StepReceipt` (one provider
event), measured in-process on this host (Python 3.10.14, pydantic 2.11.3,
Intel Xeon Gold 6248R; median of 5 000 timed calls after 2 000 warm-up
calls). The measured operation is exactly the snippet:

```text
python - <<'EOF'
import statistics, time
from aero_bench.runtime.contracts import ProviderEvent, SimulationTime, StepReceipt
from aero_bench.serialization import canonical_json_bytes

receipt = StepReceipt(
    run_id="a" * 64, provider_id="network",
    reached=SimulationTime(tick=1234, sim_time_ns=123_400_000_000),
    state_digest="b" * 64,
    events=(ProviderEvent(provider_id="network", event_id="state.1234",
                          time=SimulationTime(tick=1234, sim_time_ns=123_400_000_000),
                          payload_schema_id="ns3.state.v1"),),
)
def one():
    StepReceipt.model_validate_json(canonical_json_bytes(receipt.model_dump(mode="json")))
for _ in range(2000): one()
t = []
for _ in range(5000):
    t0 = time.perf_counter(); one(); t.append(time.perf_counter() - t0)
print(statistics.median(t) * 1e6)
EOF

c_receipt ≈ 18 µs/receipt   (median 17.9; in-process, one provider, no container I/O)
```

This is a *component* measurement, not a `T_p`. Full per-provider `T_p`
values including container RPC and engine stepping: **UNAVAILABLE**
(benchmark: run the phase-2+ executor smoke with N providers and record
`step-N` durations from the sealed `event.log`).

## 4. Entity count × per-entity projection/update cost

The public projector must, per tick, convert every entity position from
the authoritative ENU state to WGS84 and serialize the snapshot segment:

```text
T_project(N_e) = N_e · c_proj
```

Measured in-process on this host (same environment as §3; median of 5 000
timed calls after 2 000 warm-up calls). The measured operation is exactly
the snippet: per entity, one `EnuPosition` validation, one
`enu_to_geodetic` conversion, canonical JSON, and SHA-256:

```text
python - <<'EOF'
import hashlib, statistics, time
from aero_bench.world.frames import (ENU_FRAME_ID, WGS84_FRAME_ID, EnuPosition,
                                     LocalFrameOrigin, enu_to_geodetic)
from aero_bench.serialization import canonical_json_bytes

origin = LocalFrameOrigin(frame_id=WGS84_FRAME_ID, longitude_deg=116.397,
                          latitude_deg=39.916, altitude_m=50.0)
samples = [(0.0, 0.0, 100.0), (1523.7, -844.2, 212.5), (-3110.9, 5003.3, 95.0)]
def one(east, north, up):
    geo = enu_to_geodetic(EnuPosition(frame_id=ENU_FRAME_ID, east_m=east,
                                      north_m=north, up_m=up), origin)
    hashlib.sha256(canonical_json_bytes(geo.model_dump(mode="json"))).hexdigest()
for _ in range(2000): one(*samples[0])
t = []
for i in range(5000):
    t0 = time.perf_counter(); one(*samples[i % 3]); t.append(time.perf_counter() - t0)
print(statistics.median(t) * 1e6)
EOF

c_proj ≈ 29 µs/entity        (median 29.1; measured with the snippet above)
```

Budget form: projection must fit inside the tick period,

```text
N_e ≤ k / c_proj             (projector stage alone)
```

Worked arithmetic with the configured reference axis `k = 100 ms`
(`tests/support.py:584`) and the measured `c_proj`: `N_e ≤ 0.1 s / 29 µs ≈
3 400` entities *for the projector stage alone*. This is an adapter-cost
bound computed from the formula above, not an engine capacity claim, and
total capacity remains bounded by §2 and §3 (both UNAVAILABLE).

## 5. Public snapshot bytes and transfer-rate ceiling

Canonical public-trace snapshot size is linear in entities with a
per-tick constant envelope:

```text
S(N_e) = S_base + N_e · s_entity
```

`S_base` and `s_entity`: **UNAVAILABLE** — the repo contains no committed
public-snapshot document schema or benchmark that produces these bytes, so
no size is claimed (benchmark: seal a phase-2+ run with a real provider,
then regress `S(N_e)` over the sealed snapshots at `N_e ∈ {1, 10, 100,
1000}` and fit the two parameters).

Transfer ceiling:

```text
f_tick_max_tx  = R_tx / S(N_e)          (snapshots/s)
live_latency   ≥ S(N_e) / R_tx
```

The only declared link numbers in the repo are the `NetworkLink`
`data_rate_bps = 1_000_000` and `propagation_delay_ns = 100` of the ns-3
provider test fixture (`tests/providers/test_ns3_provider.py:65-66`). If
the trace shared that class of link, the ceiling would be

```text
R_tx ≤ data_rate_bps / 8 = 10^6 / 8 B/s        (recomputable from the fixture)
```

The actual effective `R_tx` of the viewer transport (WebSocket/TLS in a
real deployment): **UNAVAILABLE** (benchmark: `iperf3` or equivalent
between the harness and viewer endpoints on the actual network role).

## 6. Cesium frame budget, entity/tile count, and memory budget

Per rendered frame the viewer must redraw all entities and streaming tiles
within the frame budget:

```text
N_e_max_frame = F_cs / c_cs              (entity update path)
frame_ok      ⇔ N_e · c_cs + T_tiles ≤ F_cs
```

`c_cs` (per-entity JS entity update cost in the pinned CesiumJS build) and
`T_tiles` (tile traversal/draw cost for `B_tile` tiles): **UNAVAILABLE** —
phase 1 ships no viewer measurement. Benchmark: run the existing frontend
(`npm run build && npm run dev`) against a generated public-trace document
with `N_e ∈ {100, 1 000, 10 000}` and record mean frame time; no such run
exists yet and no entity-count capacity is claimed.

Memory:

```text
M_total ≈ M_base + N_e · M_e + B_tile · M_tile ≤ M_budget
```

`M_base`, `M_e`, `M_tile` measured: **UNAVAILABLE** (benchmark: browser
heap snapshots at each `N_e` above). The repository records one real
delivery-size datum: the production Cesium application chunk is ≈4.14 MB
(≈1.11 MB gzip) (`docs/VALIDATION.md`), which bounds `M_base` from below
for download-size, not runtime memory.

## 7. Live update and Replay read-throughput ceilings

Live (per-tick snapshots pushed to the viewer):

```text
f_live ≤ min( f_tick_max,          §2/§3 production ceiling (UNAVAILABLE)
              R_tx / S(N_e),       §5 transfer ceiling (R_tx UNAVAILABLE)
              F_cs / (N_e · c_cs) )  §6 render ceiling (c_cs UNAVAILABLE)
```

Replay (read-through of a sealed trace document of total bytes `S_total`
over the run's `T_ticks` ticks):

```text
f_replay ≤ R_read / S_avg,   S_avg = S_total / T_ticks
```

`R_read` is the read throughput of the artifact store serving the sealed
trace: **UNAVAILABLE** (benchmark: sequential read of a sealed run
directory; no sealed-trace read run exists, so no number is claimed).

## 8. Summary of measured vs unavailable inputs

| Parameter | Value | Status |
| --- | --- | --- |
| `Δphys` | 4 ms (4 000 000 ns) | image selfcheck only; formal PX4 Provider **BLOCKED** (`containers/px4-gazebo/selfcheck.py:21`) |
| `k` | matrix axes 100 ms / 200 ms | configured (`tests/support.py:584`) |
| `c_receipt` | ≈ 18 µs/receipt (median) | measured, §3 snippet, in-process adapter cost |
| `c_proj` | ≈ 29 µs/entity (median) | measured, §4 snippet, in-process adapter cost |
| `Δphys_wall_step` | — | UNAVAILABLE (§2 benchmark) |
| `T_p` per provider | — | UNAVAILABLE (§3 benchmark) |
| `S_base`, `s_entity` | — | UNAVAILABLE (§5 benchmark) |
| `R_tx` viewer transport | — | UNAVAILABLE (§5 benchmark) |
| `c_cs`, `T_tiles`, `M_base`, `M_e`, `M_tile` | — | UNAVAILABLE (§6 benchmark) |
| `R_read` sealed-trace read | — | UNAVAILABLE (§7 benchmark) |

The in-process measurements (§3, §4) are host- and interpreter-bound
adapter costs, reproducible with their committed snippets. They are inputs
to the equations, not end-to-end capacity results. All end-to-end ceilings
stay **UNAVAILABLE** until the phase-2+ executor runs produce sealed timing
evidence; this document claims no vertical capacity and declares no
benchmark GO.
