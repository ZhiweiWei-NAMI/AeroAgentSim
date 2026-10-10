# Agent Inspection Performance Model

This note defines the pre-run feasibility model for the Shanghai single-UAV
Agent inspection acceptance task. It distinguishes configuration facts,
mathematical bounds, measurements from other runs, engineering assumptions,
and values that still require measurement in the acceptance candidate. A
theoretical `success_upper_bound` of 1 only means that the declared necessary
conditions do not rule out success. It is not an experimental result.

## Source identities and time domains

The candidate retains the existing declared runtime sources:

- PX4 `v1.17.0-alpha1-1551-g381149fb01`, commit
  `381149fb012762f5e38c4a7fdc1b905b28038970`;
- Gazebo `8.11.0`, commit
  `1be3cc376fec778cc725b4eeea463245affa56d3`;
- MAVSDK `3.17.2`, commit
  `9e3ca17faa84aa868caea10a3bbdab7e53810ced`;
- ns-3 `3.48`, commit `d2add90b452d600cfb4859baed8e9ea633519447`;
- SUMO `1.27.1`, commit `7717f2379d9e314a0c81c5cec748444de06a2a91`.

The provider-barrier clock advances by 0.5 s and stops after 2,000 steps, so
the physical simulation envelope is 1,000 s. Required inspection observations
have a latest time of 500 s. Each order and the Formal v2 network policy use an
absolute upload deadline of 540 s. The task feasibility deadline is 600 s.
These are simulation-time values. Model inference happens while simulation is
paused and therefore consumes wall time without consuming these deadlines.

Several model requests and tools can run at the same paused Provider barrier.
The transport deliberately allows one function call per model response. Therefore
supporting a declared maximum of `N` tool calls requires `N` request/response
pairs plus one final response: at least `N + 1` model requests. The driver
increments `tool_calls` on each `item/tool/call` before execution, so a
manifest of `N + 1` counted calls against a max of `N` means the last attempt
was rejected and not executed. The previous 64-request limit could support at
most 63 tool calls followed by a final response. A later 96-call / 97-request
policy was consistent with that accounting but not with a completed mission.

Candidate09 attempt
`attempt.20260914t035639546233z.5688dca73de23702` is the measured session used
to size the current budget. It executed 96 tools, counted 97 attempted tool
calls, issued 97 model requests, and failed `driver.tool_budget.exhausted` on
the unexecuted 97th call (`wait_duration`). Wall time was 3,353.9 s of the
21,600 s session. Per-request Astra/xhigh inference was mean 6.0 s, median
5.4 s, p90 8.3 s, and max 24.5 s against the 180 s per-request cap. Six real
RGB observations were taken. Simulation time at stop was 403.0 s / tick 806.

That run had already submitted `artifact.report` and queued three original
`action_network_send` calls at tick 688. Every send receipt was `success=true`
with phase `completed` meaning "queue acceptance recorded; delivery is
pending". Subsequent `query_business_work_order` results remained `submitted`,
`completion_authority=business`, and `network_delivery_required=true`. The UAV
mailbox was empty because it is inbound. Four later sends were status-driven
retries after short waits, not failed send receipts. Artifacts submitted are
not authoritative orders completed.

The following remaining-call estimate is a planning scenario from that pose,
not a proven upper bound on all legal executions. It preserves camera code
and Formal v2 goals. Delivery confirmation: one `wait_duration`
plus three order queries, one extra wait/query cycle, and up to two further
sends if waiting shows the original queued messages did not deliver (about 4
through 12 calls). Return, land, stop, and disarm from about 790 m at roof-clear
altitude: two or three `flight.goto` legs around the no-fly prism, one extra
`wait_for_command` split on a long transit, land, 2 s stopped dwell, disarm,
and `finish` (about 16 through 20 calls). The estimated high case is 32 remaining calls. A
16-call planning margin allows additional waits but cannot guarantee completion.
The unflown return detour, descent and delivery must still fit the remaining
197 simulated seconds; extra tool calls do not extend that deadline. The policy is therefore 144
tool calls and 145 model requests.

The 180 s per-request timeout is unchanged. Saturating 145 * 180 = 26,100 s
would exceed the 21,600 s session wall; that saturation was not observed and
is not the session-sizing method. The request count, per-request timeout,
and aggregate session timeout are independent ceilings; this configuration
does not promise that every request can consume its full timeout. Measured mean inference plus the recorded
tool/sim mix projects about 5,000 s for a completed attempt, well inside
21,600 s. Session wall, runner runtime 22,800 s (1,200 s above the session),
readiness 480 s, independent verifier 600 s, and PX4 heartbeat 21,600 s plus
the existing 30 s margin (21,630 s) stay as previously declared. This changes
neither flight dynamics, simulated time, nor task deadlines.

Image observations remain 12. For the conservative 493.1 s physical-flight
estimate, the non-model allocation inside 21,600 s is still dominated by the
measured 3,354 s prefix plus remaining flight waits, not by 180 s * N. These
are budget arithmetic plus one measured failed attempt, not a sealed success.

Each model response has an 8,192 accepted-output-token ceiling. The inspected
transport checks server-reported usage before exposing any response or tool
call. The current Codex endpoint does not accept request-side max_output_tokens,
as confirmed by a real probe. A second real probe confirmed successful opaque
reasoning-state replay across two requests; the required summary array is sent
empty and reasoning text remains absent from recorded interactions.

The declared Agent runtime is the function-only session bridge. A separate
`astra.driver` workload runs the digest-pinned Codex CLI, uses that bridge for
Agent tools, and uses a fixed-origin inspected HTTPS transport for model
requests. Its configuration fixes model `gpt-6-astra`, reasoning effort
`low`, Codex CLI version `0.153.4`, and the native executable SHA256. The
driver must seal a private 1 MiB session manifest and private 64 MiB interaction
log. These records let the verifier bind the declared policy, model requests,
model tool proposals, trusted bridge executions, and final Agent artifacts.

## Declared geometry

The WGS84 origin is latitude 31.2304 degrees, longitude 121.4737 degrees and
ellipsoid height 50 m. The constant geoid correction is 30 m and the constant
terrain AMSL height is 20 m, so ENU up zero is also AGL zero in this scene.

The selected launch reference pose is `(-260, -310, 0.137)` m ENU. The three
required panel centers are computed from the declared building centers,
dimensions, and west-face offsets:

| Required target | Parent building | Panel center ENU (m) | Nominal vehicle body pose at 10 m standoff (m) |
| --- | --- | --- | --- |
| `target.01` | `building.01` | `(-232.08, -260, 12)` | `(-242.20, -260.03, 11.758)` |
| `target.06` | `building.12` | `(169.92, 40, 12)` | `(159.80, 39.97, 11.758)` |
| `target.08` | `building.18` | `(169.92, 240, 12)` | `(159.80, 239.97, 11.758)` |

The nominal body poses include the declared camera mount `(0.12, 0.03,
0.242)` m and assume ENU yaw zero, so the camera looks east at the panels'
west-facing surfaces. A qualifying camera pose is 8 m through 12 m from the
panel, within 12 degrees of its surface normal.

The geofence footprint is `[-480, 480] x [-480, 480]` m ENU and its vertical
range is 20 m through 180 m AMSL. The central no-fly prism is `[-90, 90] x
[120, 260]` m ENU and 20 m through 160 m AMSL. Declared building heights range
from 24 m through 44 m AGL.

## Route-distance lower bound

Let `L` be the launch point, `T_i` a panel center, and `Q_i` any qualifying
camera point. Since `|Q_i - T_i| <= 12 m`, the triangle inequality gives, for
each route leg:

```text
|Q_i - Q_j| >= max(0, |T_i - T_j| - 24)
|L - Q_i|   >= max(0, |L - T_i| - 12)
```

Applying this to the declared mission order launch, target 01, target 06,
target 08, launch gives:

| Leg | Center distance (m) | Proven lower bound (m) |
| --- | ---: | ---: |
| launch to target 01 | 58.482965 | 46.482965 |
| target 01 to target 06 | 501.601435 | 477.601435 |
| target 06 to target 08 | 200.000000 | 176.000000 |
| target 08 to launch | 698.191906 | 686.191906 |
| total | | **1,386.276306** |

This 1,386.276306 m value is the task's formal `shortest_path_m` lower bound.
It does not claim that a 1,386 m collision-free route exists. The historical
1,500 m number had no derivation and was not a measured route.

The corresponding nominal straight-line route through the exact 10 m body
poses is 1,447.883492 m, but it intersects buildings 01, 08 and 11 and the
central no-fly footprint. It is not feasible.

As an independent constructive check, a two-dimensional visibility graph was
formed from all 18 building footprints and the no-fly footprint, each inflated
by 1.0 m. With the nominal body poses, the shortest path in that graph for the
declared order is 1,472.414359 m. Its segment lengths are:

```text
53.045649, 29.501202, 305.668121, 176.614102, 200.000000,
259.894904, 84.314886, 199.809910, 81.024688, 82.540899 m
```

The visibility-graph route is an engineering feasibility witness, not an
Agent route or a formal acceptance result. It treats the vehicle as a point
outside the 1 m inflated obstacles, uses the fixed low inspection altitude,
and has not been flown in the acceptance candidate. The Agent receives public
airspace and height constraints but no target order, waypoint list, or route
answer.

## Speed, acceleration, takeoff, and dwell

`flight.goto` calls MAVSDK `Action.goto_location` without setting a cruise
speed. `Px4GazeboConfig` does not pin PX4 cruise speed, horizontal acceleration,
vertical speed, or their parameter files. Consequently, the existing 10 m/s
`max_speed_mps` is retained only as a loose analytical ceiling for the formal
necessary-condition calculation. It is not a measured or commanded cruise
speed.

One retained engineering run from 2026-09-08 used the same PX4, Gazebo and
MAVSDK commits. Its `uav.02` trajectory reached 5.1163 m/s horizontal speed and
the largest 0.5 s sampled change in speed was 1.4180 m/s2. The run is retained
at:

```text
validation/urban-inspection-flight-run-20260908T075509Z/runs/baseline/
5c0a19b53de286c1721d8bebbaa5c54c1d2176c992a78ddfbe467d972e4992ce/
runtime-seal/flight/trajectory.json
```

That run used a different world, policy and two UAVs. Its values are evidence
for an engineering estimate, not measurements of this candidate.

For a route segment of length `d`, assumed speed `v`, and symmetric assumed
acceleration `a`, a stop-to-stop trapezoidal estimate is:

```text
t(d) = 2 sqrt(d/a)              if d < v^2/a
t(d) = d/v + v/a                otherwise
```

Using the ten 1 m-clearance segments above with `v = 5 m/s` and `a = 1.4
m/s2` gives 330.197 s, versus 294.483 s with instantaneous acceleration. A
separate 11.621 m takeoff and landing height change at an assumed 2 m/s and 1
m/s2 adds 15.621 s. Adding the three mandatory 2 s observation dwells, one
1 s settling allowance per route segment, and the 2 s stopped dwell gives an
engineering total of approximately 363.8 s. The final required observation is
estimated near 190 s, before the 500 s observation limit. Upload and reporting
then have roughly 176 s before the 540 s deadline, and the full flight has
roughly 236 s before 600 s.

A more conservative policy can transit with its body reference at 46 m AGL,
two metres above the highest declared roof, and descend on the outward side of
each panel. Using direct high-altitude horizontal legs plus a 1 m-inflated
southeast detour around the no-fly prism gives 1,448.107 m horizontal travel
and 297.178 m vertical travel. With the same horizontal assumptions, 2 m/s and
1 m/s2 vertical assumptions, required dwell, and one second of settle allowance
after each of thirteen motion legs, the estimate is 493.1 s through stopped
dwell. The final observation is estimated near 296.2 s. This construction is
within the 500 s observation, 540 s upload and 600 s task limits, but it too
remains unmeasured.

These margins are estimates. Candidate acceptance still requires measured
command completion, GNSS availability, actual acceleration, wind response,
route execution, landing, and battery behavior. No task or flight parameter is
to be relaxed in response to a failed run without updating this model first.

## Image geometry

The camera is declared and container-patched to 640 x 480 pixels with an 80 x
60 degree field of view. The effective pinhole focal lengths are:

```text
fx = 640 / (2 tan(80 deg / 2)) = 381.361150 px
fy = 480 / (2 tan(60 deg / 2)) = 415.692194 px
```

Each dark patch is 0.24 m wide and 0.22 m high. At the worst allowed 12 m
standoff it projects to approximately 7.627 x 7.621 pixels. A conservative
scalar calculation uses the smaller 0.22 m dimension with `fx`, yielding
6.9916 pixels. This remains above the declared four-pixel minimum.

The task bound represents `fx` using a 3 micrometer modeling pixel pitch and an
equivalent focal length of 1.144083 mm. This is mathematically equivalent to
the declared horizontal FOV. The historical 20 mm focal length with a 3
micrometer pitch implied 6,666.7 focal pixels and 146.7 defect pixels at 10 m,
which was inconsistent with the actual 640-pixel, 80-degree camera.

The acceptance profile uses three separate, self-contained Gazebo SDF assets:
an upper dark patch, a clean panel, and a lower dark patch. Each observation
binds one asset, and the PX4 provider inserts that exact SDF as the target model
before camera capture. Verifier-private truth contains two location-based
defect identities and no record for the clean target. Defect identities do not
encode target or order suffixes.

## Formal necessary-condition ceiling

The existing Inspection feasibility contract evaluates ideal necessary
conditions, not the engineering estimates above. With the corrected profile it
computes:

```text
mission minimum = 1386.276306 / 10 + 40 + 6 = 184.627631 s <= 600 s
upload minimum  = 0.020 + 1024 * 8 / 6000000 = 0.021365 s <= 540 s
projected pixels = 0.22 * 0.001144083449 / (12 * 0.000003)
                 = 6.991621 px >= 4 px
recall ceiling = 2 visible defects / 2 total defects = 1
detection F1 ceiling = 1
success upper bound = 1
```

The 10 m/s speed and 40 s fixed-time inputs are retained from the prior task;
they are not candidate measurements. They make this calculation a permissive
feasibility gate. The separate 5 m/s engineering estimates are the planning
case, and sealed flight evidence is the acceptance result.

## Network load

The declared link rate is 6,000,000 bit/s with 20 ms propagation delay. The
ideal lower bound for a payload of `B` bytes is:

```text
t_upload(B) = 0.020 + 8 B / 6,000,000 seconds
```

This gives 21.365 ms for the 1 KiB minimum payload, 22.017 ms for the previously
measured 1,513-byte report, and 101.920 ms for the maximum 60 KiB report
artifact. These are ideal link bounds. ns-3 queuing, application framing,
delivery state transitions, communications-shadow attenuation, and the exact
new report size remain to be measured. The formal deadline remains 540 s.

## Wall-clock risk and acceptance measurements

The retained 2026-09-08 engineering event ledger advanced 120 s of simulation
in approximately 3,673.86 s wall time, about 30.6 wall seconds per simulated
second. It used a materially larger, two-UAV scene, so extrapolating it to this
candidate would be invalid. It nevertheless shows that wall time is a first
class risk and that the 1,000 s simulation envelope is not a wall-time promise.
Candidate09 already advanced 403 s of simulation in 3,353.9 s wall, about 8.32
wall seconds per simulated second including paused inference. That rate, applied
to the remaining 197 s before the 600 s task deadline, projects about 1,640 s
more wall, or about 5,000 s total, inside the 21,600 s session. The older
8.396 wall-seconds-per-sim-second gate against a 4,140 s leftover assumed every
model request consumed the full 180 s cap. That leftover no longer exists under
145 * 180 arithmetic, and the session is instead sized from the measured Astra
latency. If a later attempt's measured mean inference approaches the 180 s cap,
the session, PX4 heartbeat, and runner bounds must be raised together before
another formal attempt; the simulator or mission deadlines must not be relaxed
to manufacture a pass.

The event ledger bound includes 350,000 bytes for each of 2,000 barriers, 32
MiB fixed overhead, and 2 MiB for each of at most 12 image observations. This
is 723.57 MiB, below the declared 768 MiB event artifact limit. The fifteen
declared sealed artifacts have a combined maximum size of 1,587.059 MiB. This
includes the 256 MiB exact PNG archive, 1 MiB model session manifest, and 64
MiB model interaction log. The builder proves that this complete inventory
plus the bounded seal manifest fits both 2,048 MiB runtime and verifier-input
volumes. The old 1,024 MiB runtime volume could not satisfy the declaration
even though a typical run writes much less data.

Before the candidate can be accepted, the sealed evidence must supply these
currently unmeasured values:

- actual route length and elapsed simulation time through final disarm;
- maximum and cruise horizontal speed, vertical speed, acceleration, and
  deceleration from flight telemetry;
- GNSS fix availability, satellite count, uncertainty, and any missing-data
  intervals;
- per-target PNG dimensions, bytes, digest, source timestamp, pose binding,
  and visible patch or clean surface;
- actual report payload size, ns-3 delivery latency, and business completion
  time;
- sealed Astra model identity, request count, per-request wall latency, token
  usage, tool-proposal/execution chain, total paused inference wall time,
  Provider-barrier wall time, and complete runner wall time.

Only sealed authoritative evidence and the independent verifier determine the
final result.

## User-selected reasoning effort

On 2026-09-14 the user selected `gpt-6-astra` / `low` for future simulated
Agent sessions to reduce inference cost. Candidate09/10 measurements above
remain historical `xhigh` observations; they do not establish low-effort
latency, cost or task success. New candidates must resolve the low-effort
policy and schema together. Existing frozen candidates remain unchanged.
