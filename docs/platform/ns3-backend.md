# ns-3 backend recovery and RPC profile

P4 delivers `containers/ns3/` only. The PX4 material embedded in the task is
background for the shared protocol; PX4/SUMO and the kernel were read only.
The service runs real ns-3.48 Wi-Fi/IPv4/UDP and has no AeroBench workload,
scenario identity, evidence, session-token or artifact dependencies.

## Recovered source and build provenance

All 33 matching local tags were inspected with `docker image inspect`, then
`docker create --label aeroagentsim.job=p4 --cpus 8 --memory 8g`, `docker cp`
and `docker rm`. No original-image container was started; no existing image
was retagged, removed, pushed or pruned. Identical aliases were still compared.
`containers/ns3/recovered/provenance.json` records every tag, immutable image
ID, creation date, recovered file size and SHA-256. `sources.tar.gz` contains
the recovered files by tag; `docker-history.txt` preserves the production
Dockerfile history. These files are excluded from the runtime build.

Selected production source: **`127.0.0.1:5000/aero-bench/ns3:osm-rpc-verification`**,
created 2026-09-06 12:00:19 +08:00. This is newer than the OSM scene/signal and
urban production variants. The 2026-09-30 diagnostic tags have the same latest
provider and diagnostic-specific Python/selfcheck edits; they were preserved
but were not chosen as the production base.

- Image ID: `sha256:7dbb3f6c442b0e09464b427551088e276a1393eaaf91029a539f465b278279c3`.
- Repository manifest digest used by `FROM`: `sha256:95cce3de770019767b7a62352002a4c9cf36c4dcde934353958fe559503461f8`.
- Original archive URL: `https://www.nsnam.org/releases/ns-3.48.tar.bz2`.
- Original archive SHA-256: `5700ceecef2c9bc862502914b2237fe798d4c7ae07652247050e300e49407c4d`.
- Original declared ns-3 commit: `d2add90b452d600cfb4859baed8e9ea633519447`.
- Original build: optimized, examples/tests disabled,
  `core,network,internet,mobility,propagation,traffic-control,wifi` enabled.

The original Dockerfile downloaded that exact archive, verified it with
`sha256sum --check --strict`, extracted it, and built the provider/selfcheck.
This build reuses the existing verified tree and toolchain as a build stage,
replaces only the provider, and constructs a fresh runtime rootfs containing
Python stdlib, the service, ns-3 shared libraries and their dynamic dependencies.
It carries the base's Ubuntu 24.04 userspace, without compiler, source tree,
AeroBench server, recovered archives or build tools. `bundle.py` discovers the
ELF dependency closure and fails on unresolved libraries.

An initial independent `ubuntu:24.04` stage failed while fetching Docker Hub
metadata (network timeout). The reuse build removes that dependency. An earlier
attempt to use the image configuration ID as a manifest digest was also rejected;
the final Dockerfile uses the actual `RepoDigests` manifest digest above.

Selected file hashes:

| Recovered path | SHA-256 |
|---|---|
| `/opt/ns-3.48/scratch/aero-ns3-provider.cc` | `8c0ccaaae64a3fb97ab5da98998af0673b4e839ef84ac796e235ca2c6312d4f6` |
| `/opt/ns-3.48/scratch/aero-ns3-selfcheck.cc` | `b12377dd7132c0b6f14651c17d99b0e88359335577c6ae7b0c9a47e609da6c67` |
| `/opt/aero-bench/ns3-server.py` | `da23282147c9c85df79a3ac95013ff4f7314983570dac724e6b7200a8198705a` |
| `/opt/aero-bench/workload_scenario.py` | `f9760864f0879b7d74c4f867db15cb76d68a34c972209fc876c3574dba5eb528` |
| `/opt/aero-bench/readiness_probe.py` | `1b7567f2e2eea296a71319bdb86188ebce4f992d8a45f652f0e7b5275f7ebf6f` |

Variant comparison (the full five-file, per-tag table is in the provenance JSON):

| File | Hash | Tags |
|---|---|---|
| `aero-ns3-provider.cc` | `8c0ccaaae64a3fb97ab5da98998af0673b4e839ef84ac796e235ca2c6312d4f6` | `selfcheck-fixed`, `delay-probe`, `20260930`, `osm-rpc-verification`, `osm-scene-verification`, `osm-signal-verification`, `urban-7885d4cf`, `urban-06af2081`, `urban-7d00e6`, `urban-98f7cd7b39e8`, `urban-89f27baab92c`, `urban-d2b0dba6`, `urban-841708`, `urban-d2aa4ec5`, `urban-2428bc3a`, `urban-1b7c69`, `urban-915b8b65c50f`, `urban-current`, `urban-9f8f94`, `urban-8c6402`, `urban-8d6cf40e`, `urban-c1e439f4b6a9`, `urban-56c39013907e`, `urban-01a9bc9c`, `urban-5fd06da1`, `urban-9088b51fe2d4`, `urban-7eeba566` |
| `aero-ns3-provider.cc` | `34276380802e24f2beb6cfe29752574215461e43ad70efdb282c86982b426e40` | `urban-87e17bf63b2a` |
| `aero-ns3-provider.cc` | `de628c6cfafac67fc4e647178ea173a6ee6c0cf7b7801b0d52ca147abb47b5e7` | `urban-4ba52ec73444`, `urban-a3ee22eb62d5`, `urban-7c5ce6446c36`, `urban-30944d405de4`, `urban-ac3babed3832` |
| `aero-ns3-selfcheck.cc` | `1905276367c61fe04445ed2c17f142c4790f1ad6821d4cb77ad52bea1060f89d` | `selfcheck-fixed` |
| `aero-ns3-selfcheck.cc` | `5f78f0b6e76599444468f5585d2ac0b1e30c50ceaceef9d8f025a036f0cb2381` | `delay-probe` |
| `aero-ns3-selfcheck.cc` | `d1922f30604f5b20619d05fc1af1798f899cedb9fc9700ce0ccf76d7ee4e8631` | `20260930` |
| `aero-ns3-selfcheck.cc` | `b12377dd7132c0b6f14651c17d99b0e88359335577c6ae7b0c9a47e609da6c67` | `osm-rpc-verification`, `osm-scene-verification`, `osm-signal-verification`, `urban-7885d4cf`, `urban-06af2081`, `urban-7d00e6`, `urban-98f7cd7b39e8`, `urban-89f27baab92c`, `urban-d2b0dba6`, `urban-841708`, `urban-d2aa4ec5`, `urban-2428bc3a`, `urban-1b7c69`, `urban-915b8b65c50f`, `urban-current`, `urban-9f8f94`, `urban-8c6402`, `urban-8d6cf40e`, `urban-c1e439f4b6a9`, `urban-56c39013907e`, `urban-01a9bc9c`, `urban-5fd06da1`, `urban-9088b51fe2d4`, `urban-7eeba566`, `urban-87e17bf63b2a`, `urban-4ba52ec73444`, `urban-a3ee22eb62d5`, `urban-7c5ce6446c36`, `urban-30944d405de4`, `urban-ac3babed3832` |
| `ns3-server.py` | `07cc1f3fc7e3af4dd1984c512cae41158a38f56db07b8189295e532faacf889c` | `selfcheck-fixed` |
| `ns3-server.py` | `a05b3b9b8ec47c8beda5a4232e4e26ee727deef85adc352bfb8d94f269c362e7` | `delay-probe`, `20260930` |
| `ns3-server.py` | `da23282147c9c85df79a3ac95013ff4f7314983570dac724e6b7200a8198705a` | `osm-rpc-verification` |
| `ns3-server.py` | `1cd54fe5de78a2101fbcfca03dc274b6e9f4c54a7807100f5eaeeb9f4237b0f9` | `osm-scene-verification`, `osm-signal-verification`, `urban-7885d4cf`, `urban-06af2081`, `urban-7d00e6`, `urban-98f7cd7b39e8`, `urban-89f27baab92c`, `urban-d2b0dba6`, `urban-841708`, `urban-d2aa4ec5`, `urban-2428bc3a`, `urban-1b7c69`, `urban-915b8b65c50f`, `urban-current`, `urban-9f8f94`, `urban-8c6402`, `urban-8d6cf40e`, `urban-c1e439f4b6a9`, `urban-56c39013907e`, `urban-01a9bc9c`, `urban-5fd06da1`, `urban-9088b51fe2d4`, `urban-7eeba566`, `urban-87e17bf63b2a`, `urban-4ba52ec73444`, `urban-a3ee22eb62d5`, `urban-7c5ce6446c36`, `urban-30944d405de4`, `urban-ac3babed3832` |
| `workload_scenario.py` | `846783c8963aed27e374c3e25b7faaa2349234ba9b5f80f28e785b03d368203e` | `selfcheck-fixed`, `delay-probe`, `20260930` |
| `workload_scenario.py` | `f9760864f0879b7d74c4f867db15cb76d68a34c972209fc876c3574dba5eb528` | `osm-rpc-verification`, `osm-scene-verification`, `osm-signal-verification` |
| `workload_scenario.py` | `3216434060546d65c47bf2c5e3f0a7fc75bad923ebe516a965ee668d4397d997` | `urban-7885d4cf`, `urban-06af2081`, `urban-7d00e6`, `urban-98f7cd7b39e8`, `urban-89f27baab92c`, `urban-d2b0dba6`, `urban-841708`, `urban-d2aa4ec5`, `urban-2428bc3a`, `urban-1b7c69`, `urban-915b8b65c50f`, `urban-current`, `urban-9f8f94`, `urban-8c6402`, `urban-8d6cf40e`, `urban-c1e439f4b6a9`, `urban-56c39013907e`, `urban-01a9bc9c`, `urban-5fd06da1`, `urban-9088b51fe2d4`, `urban-7eeba566`, `urban-87e17bf63b2a`, `urban-4ba52ec73444`, `urban-a3ee22eb62d5`, `urban-7c5ce6446c36`, `urban-30944d405de4`, `urban-ac3babed3832` |
| `readiness_probe.py` | `2c3f4616713b43132104215c3947d3a757a2029591a616631404a3978504787c` | `selfcheck-fixed`, `delay-probe`, `20260930` |
| `readiness_probe.py` | `1b7567f2e2eea296a71319bdb86188ebce4f992d8a45f652f0e7b5275f7ebf6f` | `osm-rpc-verification`, `osm-scene-verification`, `osm-signal-verification`, `urban-7885d4cf`, `urban-06af2081`, `urban-7d00e6`, `urban-98f7cd7b39e8`, `urban-89f27baab92c`, `urban-d2b0dba6`, `urban-841708`, `urban-d2aa4ec5`, `urban-2428bc3a`, `urban-1b7c69`, `urban-915b8b65c50f`, `urban-current`, `urban-9f8f94`, `urban-8c6402`, `urban-8d6cf40e`, `urban-c1e439f4b6a9`, `urban-56c39013907e`, `urban-01a9bc9c`, `urban-5fd06da1`, `urban-9088b51fe2d4`, `urban-7eeba566`, `urban-87e17bf63b2a`, `urban-4ba52ec73444`, `urban-a3ee22eb62d5`, `urban-7c5ce6446c36`, `urban-30944d405de4`, `urban-ac3babed3832` |

## Network model and deliberate changes

The provider uses one `YansWifiChannel` shared by all nodes, one radio per node,
`AdhocWifiMac`, 802.11n at 5 GHz/20 MHz, fixed `HtMcs7` data and `HtMcs0` control,
real IPv4/ARP/UDP sockets, and ns-3's frame error/retry models. Aggregation is
disabled so individual packet identities can be associated with monitor traces.
The default transmit power is 20 dBm, receive sensitivity −90 dBm, receiver
noise figure 7 dB, path-loss exponent 3, and 1 m reference loss derived from the
selected channel frequency using free-space loss. These are explicit simulator
configuration defaults, returned by reset; no missing observation becomes zero.

The recovered provider installed a separate two-radio channel and token-bucket
shaper **per declared link**, with benchmark-supplied attenuation. The new model
uses a shared medium so contention/interference and one-radio half-duplex behavior
are real. This changes network results relative to AeroBench's isolated-link
model. There is no artificial declared-link bandwidth shaper, multi-interface
link topology, scenario-specific radio inventory or static global multi-hop
route compilation. This first profile models direct unicast on one ad-hoc subnet.
Wi-Fi peers beyond radio range cannot acquire a synthetic delivery.

Positions are ENU metres. Mobility uses `ConstantPositionMobilityModel` with
explicit timestamped updates (piecewise constant; **no inferred interpolation**).
A future pose is scheduled at its occurrence time rather than applied at the
start of an earlier integration interval. Inputs sharing a timestamp are ordered
by node inventory index; duplicate node/time updates are rejected.

The propagation law is the recovered log-distance model:
`loss_db = reference_loss_db + 10*exponent*log10(max(1, distance_m)) + scene_loss_db`.
The initial scene profile accepts ENU axis-aligned volumes. Each volume with a
positive-length ray intersection contributes its configured `loss_db` once;
losses add across overlapping volumes. A ray merely grazing a face contributes
no loss. Scene intersection runs in the ns-3 propagation model, against native
positions. The original Python server accepted extruded polygons and building
workload semantics; polygon volumes and material inference are outside this slim
profile. There is no placeholder default building attenuation.

Packet `size` is the actual UDP application datagram size sent through ns-3.
`payload_ref` is an opaque host reference returned on delivery; the service does
not dereference it or claim to transport its referenced content. ns-3 packets
model the given byte count, with a non-wire `PacketTag` tracking identity. There
is no content-sensitive traffic or byte-content replay in this profile. IP may
fragment large datagrams; reception is recorded only after UDP reassembly.

A command receipt is **accepted**, never delivered. Terminal delivery requires
an observed UDP receive callback. A packet that is not delivered before its
explicit application lifetime gets `delivery_timeout` from a native scheduled
expiry event. That is an application deadline outcome, not an invented PHY
cause: it can include ARP failure, retries, collision, range, queueing or missing
fragments. A native socket send failure is `udp_send_failed`. Late UDP arrivals
after expiry do not overturn an already reported terminal application drop.
Link counts accumulate actual send/receive/expiry outcomes in both directions;
`predicted_rssi_dbm` is a model prediction at the returned boundary. Optional
**delivery** `rssi_dbm`/`snr_db` are read from `MonitorSnifferRx` at the destination;
for fragmented datagrams they describe the last observed received fragment,
not a fabricated whole-datagram measurement. Unavailable values are omitted.

## TCP RPC profile

Requests are exactly one UTF-8 JSON object per LF-terminated frame:

```json
{"protocol":"aeroagentsim.ns3/v1","major":1,"minor":0,"id":1,"op":"hello","payload":{}}
```

Responses echo `protocol`, `major`, `minor`, `id` and contain exactly one `result`
or `error{code,message,state}`. Request IDs are connection-local positive,
strictly increasing integer tokens, preserved without binary64 conversion.
The current sole supported version is 1.0; unsupported protocol, major or minor
fails. Every field set is explicit; unknown fields do not disappear silently.
LF is the only physical delimiter (CRLF rejected). Duplicate keys, nonfinite
numbers including exponent overflow, invalid UTF-8/surrogates, incomplete
frames, frames above 8 MiB **including LF**, nesting beyond 128 and integer tokens
above 4096 digits fail. The configured native time range is `0..2^63−1` ns.

One TCP connection owns the native world. Additional clients are disconnected.
One request executes at a time; clients must wait for the response before sending
the next request. Operations are serialized and are never automatically retried.
Lifecycle is `NEW → HELLO → RESET → READY → CLOSED`, with one reset. A framing
fault closes without fabricating an identity. A trusted operation fault returns
`INVALID_REQUEST`, `TIMEOUT` or `NATIVE_FAILURE`, enters `TAINTED`, kills the
native process, and permits only `close`. Invalid **send commands** are normal
`rejected` receipts before mutation and keep READY. Native failures never become
an accepted receipt. Native EOF, mismatched frontier, incomplete batches and
response-budget failures publish no successful partial result.

Operation timeouts (seconds): `hello=10`, `reset=180`, `advance=30`, `command=30`,
`close=20`; drain=10; idle=3600. Native read=25, native shutdown=5. The world is
paused during idle decisions. Timeouts taint/abort; neither server nor client
retries a stateful call. Diagnostics go to stderr/Docker logs. There are no wall
clock readings in RPC results.

### Operations

- **hello `{}`** returns capabilities: ns-3 version, lockstep/exact-stop/hold,
  no early return, 1 ns quantum, supported action and radio/model limits, frame
  and timeout budgets.
- **reset** requires `seed` (nonzero uint32) and 2–128 `nodes` with unique `id`
  (1–256 UTF-8 bytes) and `position_enu:[east,north,up]`. Optional `channel`
  accepts `number` (36–64 or 100–144 in steps of 4), `width_mhz` (20 only),
  `tx_power_dbm` [−30,60], `rx_sensitivity_dbm` [−120,−20], `noise_figure_db` [0,30].
  Optional `propagation` accepts `exponent` [1,8] and `reference_loss_db` [0,200].
  Optional `scene_volumes` contains up to 1024 `{min_enu,max_enu,loss_db}` objects
  with positive extents and `loss_db` [0,1000]. Positions must be finite and within
  ±1e9 m. Null is invalid wherever a real value or optional object/array is given.
  Response returns `reached_sim_ns:0` and the complete effective configuration.
- **command** requires `{"action":"send","params":{"packet_id":"p1","src":"a",
  "dst":"b","size":512,"payload_ref":"blob:external"}}`. IDs/references are
  nonempty and ≤256 UTF-8 bytes. Source/destination must be distinct reset nodes;
  size is 1–61440. Optional `lifetime_ns` is 1–60e9 (default 1e9); expiry must fit
  native int64 time. Packet IDs are unique for the connection, at most 1,000,000
  accepted IDs and 4096 pending packets. Acceptance returns `packet_id`, `sim_ns`
  and `status:"accepted"`; rejection returns `status:"rejected"` and `reason`.
  A send is queued at the current native boundary and executes when advance runs.
- **advance** requires `to_sim_ns` strictly above the current frontier. Optional
  `mobility` (≤8192 updates) contains `{node,sim_ns,position:[east,north,up]}`;
  all timestamps must lie between the current frontier and target inclusive.
  Validate the whole input before native mutation. Response contains
  `reached_sim_ns`, `deliveries`, `drops`, `link_stats`, `pending_packets`.
  Deliveries contain `packet_id,src,dst,sent_ns,received_ns,available_sim_ns,size,
  payload_ref` and optional observed `rssi_dbm,snr_db`. Drops contain
  `packet_id,src,dst,sim_ns,available_sim_ns,reason`. Link records contain
  `src,dst,sim_ns,available_sim_ns,distance_m,path_loss_db,predicted_rssi_dbm,
  sent,delivered,dropped` for every unordered pair, with cumulative bidirectional
  counts. Empty delivery/drop arrays are genuine native outcomes.
- **close `{}`** destroys the native simulator, waits for process exit, and
  returns `closed:true`. Native cleanup is idempotent. A fresh connection starts
  a fresh process/world rather than resuming a prior frontier.

Example reset and advance payloads:

```json
{"seed":713,"nodes":[{"id":"a","position_enu":[0,0,0]},{"id":"b","position_enu":[10,0,0]}],"scene_volumes":[{"min_enu":[4,-2,-2],"max_enu":[6,2,2],"loss_db":12}]}
{"to_sim_ns":100000000,"mobility":[{"node":"b","sim_ns":50000000,"position":[100,0,0]}]}
```

### Time and future kernel integration

`sent_ns`, `received_ns` and drop `sim_ns` are occurrence times on the native
ns-3 clock. `available_sim_ns` is the granted advance boundary at which the host
can consume that outcome. A packet received at 3 ms during an advance to 100 ms
is available at 100 ms; it cannot retroactively affect a different engine that
has already reached 100 ms. ns-3's stop event orders by native event insertion;
an event exactly tied with the target may be processed on the next advance
with its original occurrence time. Its availability reflects that later batch.

This is a backend-specific transport profile aligned with aerokernel DESIGN
§6 framing, IDs, lifecycle, strict integers and timeouts, not a full
`aerokernel.rpc` Engine endpoint. A future host adapter owns the manifest,
partition/invocation token, cuts, frontiers, registry bindings and digest pinning;
it translates committed mobility into latched native inputs and maps outcomes
onto kernel messages. It must choose communication horizons/lag policies that
respect returned availability. The backend reports exact native `Simulator::Now()`
at the requested target, pauses between advances and has no early return.
Logical-only hold is implementable by the host without native integration;
there is no same-target `advance` or full kernel `react/horizon` endpoint here.

## Validation

Measured build/smoke results are recorded below after the real execution. The
host-only suite checks transport framing, exact integers, schema/lifecycle faults,
exclusive world ownership, timeout/no-retry behavior, command rejection before
mutation, mobility time bounds and mismatched native frontiers. Mock runtime
checks establish transport behavior; they do not stand in for radio verification.

Two concurrent WorkBuddy GLM sessions supplied a source/model review and contract
case suggestions (`workbuddy/glm-5.3-flash`, output budget 131072, no effort
parameter). Their artifacts were inspected. The review's channel isolation,
fragmentation, missing drop outcomes, timeout and boundary-tie findings were
checked against source. Suggested `inject/collect`, hex payload, pipeline rejection
and pagination behavior did not belong to the requested profile and were excluded.
### Real results (2026-10-08)

Final built image: **`aeroagentsim/ns3:dev-p4b`**,
`sha256:50bc201107afb8875d3879fd94a0c41c97f94c079c3738a7d9f87bab945ae57c`.
Docker reports **72,795,811 bytes (72.8 MB)**, compared with the recovered
production image's **671,562,016 bytes (671.6 MB)**: an 89.2% reduction.
Both builds completed with 8 CPU quota/8 GiB limits; compiler output had no
warnings/errors. Runtime containers used `--cpus 8 --memory 8g`, the required
job label and loopback-only dynamically allocated TCP ports.

The scenario has n0 fixed at the origin; n1–n4 start at 2/4/6/8 m and move outward
at 2/4/6/8 m/s using 100 ms timestamped pose samples. Four 512-byte UDP datagrams
are submitted each 100 ms, each with a 500 ms application lifetime. At 59.9 s
the destinations are at 121.8/243.6/365.4/487.2 m. No configured range cutoff is
used: losses emerge from propagation, sensitivity, error models, ARP and retries.
A final advance to 61 s drains terminal outcomes; RTF below counts this 61 s
native horizon and includes all host command/advance exchanges.

| Metric | Run A | Same-seed run B |
|---|---:|---:|
| TCP connect + hello + reset | 41.08 ms | 42.83 ms |
| 61 s simulation/commands wall time | 4.1831 s | 4.1081 s |
| RTF | 14.5827 | 14.8486 |
| 100 ms advance median latency | 4.3647 ms | 4.2656 ms |
| 100 ms advance p95 latency | 6.6216 ms | 6.6834 ms |
| 100 ms advance maximum latency | 12.3349 ms | 12.1491 ms |
| Accepted datagrams | 2400 | 2400 |
| Observed UDP deliveries | 430 | 430 |
| Native application timeout drops | 1970 | 1970 |
| Pending after drain | 0 | 0 |
| Deliveries with observed RSSI/SNR | 430 | 430 |

Docker launch to TCP readiness was separately measured at **0.5322 s** with the
local image already available. These are local single-host measurements with
5 nodes and light offered load; they do not establish fleet scaling or calibrated
real-world coverage. The native provider uses one process/thread; the 8 CPU
limit is an upper bound, not an assertion that the simulator uses all 8 CPUs.

| Send-time interval | Sent | Delivered | Delivery rate |
|---|---:|---:|---:|
| 0–10 s | 400 | 312 | 78% |
| 10–20 s | 400 | 104 | 26% |
| 20–30 s | 400 | 14 | 3.5% |
| 30–40 s | 400 | 0 | 0% |
| 40–50 s | 400 | 0 | 0% |
| 50–60 s | 400 | 0 | 0% |

The first **1 second**, while all destinations were still nearby, delivered
40/40 packets (100%). Both run transcripts are exactly **2,035,863 bytes** and
were compared with Python byte equality and `cmp`, with no fields removed or
normalized. Both SHA-256 values are
`9ebfe5a4ae7c6333aabe81010e0f7a45dbb726a6a4927627bf43a79e2b91baa0`.
This establishes same-image/seed/input reproducibility, not cross-version,
cross-architecture or heterogeneous-engine numerical determinism.

Additional real probes on the final image:

- At 10 m, a 512-byte datagram arrived at **1,404,099 ns**, with RSSI
  **−56.7344 dBm**, SNR **37.2316 dB**; it became available at the 600 ms boundary.
- A crossing 50 dB volume changed path loss from **76.7344 to 126.7344 dB** and
  resulted in a native 500 ms `delivery_timeout` (available at 600 ms).
- Moving the destination from 10 m to 1000 m **at 50 ms** preserved the earlier
  1,404,099 ns delivery, while the returned boundary distance was 1000 m. The
  future pose was not applied before its stated time.
- A cold-ARP first **61,440-byte** datagram timed out at 10 m. After a real
  64-byte warmup datagram established the neighbor, the 61,440-byte datagram
  arrived intact after **12,780,947 ns**, with the full application size reported.
  Cold-ARP fragmentation loss is consistent with the inspected ns-3 default
  pending queue (3 packets); the probe does not claim a traced causal PHY reason.
- A send with an unknown source was rejected without tainting. An invalid zero
  advance returned `INVALID_REQUEST`/`TAINTED`; a subsequent close successfully
  cleaned the killed native process and returned `closed:true`.

Honest failure record: the first radio smoke failed an incorrect requirement
that **all of the first 10 s** deliver ≥90%, although moving destinations crossed
radio limits during that interval. The final smoke applies that near-distance
assertion to the first 1 s and still requires far-distance drops. The first
max-datagram probe also incorrectly assumed cold-ARP success; the separate cold
and genuinely warmed probes above preserve the actual results. No synthetic
success, retry of a failed stateful call or radio-model adjustment was used to
make either check pass. The initial Docker Hub and incorrect digest failures
are recorded in the build section above.

Artifacts saved in the permitted delivery tree for review:

- `containers/ns3/verification/metrics.json`: both measured runs.
- `verification/startup.json`: Docker launch/readiness measurement.
- `verification/manifest.json`: image ID, transcript byte counts/hashes and checks.
- `verification/run-a.jsonl.gz`: lossless compressed RPC transcript (run B is
  byte-identical; full uncompressed repeats remain in `/tmp/aas-p4/smoke/`).
- `verification/probe-results.json` and `fault-probe.json`: real probe outcomes.

The 72 host-only tests and Ruff checks passed with Python 3.11 from the required
virtual environment. Runtime Python uses only stdlib. A first test run generated
5 Hypothesis constant-cache files outside the permitted delivery tree; their
headers confirmed they belonged exclusively to this task, and they were removed.
Subsequent test runs use `HYPOTHESIS_STORAGE_DIRECTORY=/tmp/aas-p4/hypothesis`,
`PYTHONDONTWRITEBYTECODE=1`, disabled pytest cache and a scratch Ruff cache.
Created runtime containers were removed after verification. No git commit,
branch/reset, protected-repository build or image cleanup operation was performed.

## Standalone build (P9)

`containers/ns3/Dockerfile` now uses public, digest-pinned bases and verified
source/dependency inputs. Build with `containers/ns3/build.sh`; the output tag
is `aeroagentsim/ns3:standalone`. No AeroBench image or checkout is required.
The existing backend service/native model is unchanged.

The final image is `sha256:ca03e1d2f4ca8e9a2f78ed41ef8192b374248226073f4394727d8705109de036`, **72,841,777 bytes**.
Its existing real smoke client passed on this image; the three standalone Docker
adapter/replay tests also passed. Input hashes, command timings, RTF/repeatability,
build-network failure history and artifact links are recorded in
[containers.md](containers.md). Verification artifacts are in
`containers/ns3/standalone/`; the original dev-image measurements above remain
historical and are not substituted for standalone results.
