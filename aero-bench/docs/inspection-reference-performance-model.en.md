# Deterministic inspection reference v1: model and experiments

This is a separate B1 execution profile, not acceptance of the three-target
model-agent release or the 414-building city. It uses the existing offline
18-building Gazebo world and real 20-vehicle/five-person SUMO demand. Targets
`target.01` and `target.05` are west-facing panels on the same west building
column. The inspection contract requires at least two targets and retains 15
goals. The user-approved landing-witness revision now uses verifier/policy v3;
numerical thresholds and physical simulation parameters remain unchanged.

## Flight and imaging assumptions

The launch body reference is ENU `(-260, -310, 0.137)` m. Panel centers are
`(-232.08, -260, 12)` and `(-232.08, 40, 12)` m. Camera mount offset is
`(0.12, 0.03, 0.242)` m. At 10 m standoff with ENU yaw zero, planned body
positions are `(-242.20, -260.03, 11.758)` and
`(-242.20, 39.97, 11.758)` m. The route stays west of building footprints and
outside the central no-fly prism. Cruise altitude is 20 m ENU up. Public
geometry defines navigation; only actual camera bytes determine detections.

The planned pre-observation ground track is 67.77 m, above the unchanged
50.1 m criterion. The complete horizontal route is 735.54 m, including return.
At an assumed 5 m/s cruise it takes about 147.1 seconds before vertical motion,
acceleration, dwell and settling. This is an estimate, not a measurement or a
flight guarantee. The executable mission bound uses the triangle inequality:
launch, two 12 m target neighborhoods, return; its configured 10 m/s speed
ceiling yields a necessary time bound. The builder calculates that bound from
the declared geometry. The environment allows 600 half-second barriers
(300 seconds). Every physical command must reach authoritative completion.

The 640 px / 80-degree horizontal camera has equivalent focal length
381.361 px. A 0.22 m patch at 12 m projects to 6.992 px, above the unchanged
four-pixel resolvability bound. This pinhole calculation omits blur and sampling
effects. The reference classifier requires a centered red panel and at least
six dark interior pixels. Clean pixels produce no detection. Missing or
unreadable panels produce an explicit error. Unit images test the classifier;
they are not formal camera evidence.

## Network assumptions

The operations radio is explicitly bound to the existing launch-pad entity,
not the distant operations building used by the urban release. Reports are
sent after returning and landing at the launch position. The radio
profile remains 802.11ax, 5.775 GHz, 80 MHz, 23 dBm transmit power and -92 dBm
receive sensitivity. The declared queue limit remains 6 Mbit/s.

The link's constant propagation delay is the ceiling of the maximum distance
from that pad to any declared spatial-extent corner, divided by 299792458 m/s.
The bound is below 4 microseconds. The builder records the exact distance and
integer delay in `source-lock.json` and uses the same delay in the strict
WorldPackage and link-feasibility inputs. It does not change the production
Provider's delay, loss, queue or delivery semantics.

The 6 Mbit/s shaper permits at most 750000 payload bytes/s before protocol
overhead. A maximum 60 KiB report requires at least 81.92 ms per transmission
at this queue rate before burst credit and overhead. Two work orders upload
the exact same aggregate report bytes with separate message identities.
Best-effort delivery is not guaranteed by this calculation. The participant
requires both Business states to reach `completed` after actual ns-3 delivery;
a queued command receipt does not satisfy that check.

## Evidence capacity

The initial planning estimate was 350000 event bytes per tick. For 600 ticks,
that estimate gives 210000000 bytes, plus 32 MiB fixed overhead and the bounded
image observation envelope. It is not a proven per-tick upper bound. The
initial reference profile declared 320 MiB for the event ledger and retained the
existing Provider camera/trajectory/scene artifact bounds. Input and artifact
volumes each allow 2 GiB.

R3 measured 224828675 ledger bytes and 34794 records through tick 522, leaving
110715645 bytes under the declared limit. Its largest observed tick was 513
at 636079 bytes, exceeding the initial estimate. Repeating that maximum over
600 ticks, plus tick-zero bytes, would require 381877669 bytes, above the
declared limit. This extrapolation is not a prediction or a worst-case proof;
R3 fits, but the initial estimate cannot guarantee capacity for every 600-tick
attempt. Measurements are in `reference-r3-event-capacity-measurement.json`.

## R2 failure and radio experiment

R2 returned to launch at about 20 m AGL and wrote a 1311-byte aggregate report
with two camera-bound detections. Both work orders remained incomplete during
the declared 60-second delivery wait; the participant exited at tick 526,
263 simulated seconds. No accepted seal or verifier result exists.

A diagnostic native selfcheck with the reference's 3765 ns propagation delay
delivered all three packets. That check used a 20 MHz channel at 2–4 m and does
not validate the reference's 80 MHz link at roughly 20 m separation.

The actual Provider selects constant `HeMcs11` data and `HeMcs0` control modes
for the declared 802.11ax profile. The 6 Mbit/s queue limit does not select a
low-rate PHY mode. For 5.775 GHz, 23 dBm transmit power and log-distance
exponent 3, the 1 m reference loss is about 47.68 dB. At 20 m with no obstruction,
received power is about -63.71 dBm. Thermal noise at 80 MHz plus the declared
7 dB noise figure is -87.97 dBm, giving about 24.26 dB SNR. The ideal Shannon
bound is about 645 Mbit/s; the declared 600 Mbit/s PHY ceiling is close to this
ideal limit. This calculation does not establish `HeMcs11` packet reliability.

The pre-experiment hypothesis was that this fixed high-order data mode loses
the airborne reports despite successful lower-mode control exchange. The native
experiment kept the exact profile, rate, delay, seed and report bytes,
and compared the measured airborne position with a proposed landed position at
the same pad. At the model's 1 m distance floor, predicted SNR is about 63.29 dB;
the queue limit remains the tighter capacity bound. A 1311-byte report requires
at least 1.748 ms of serialization per transmission before burst credit and
protocol overhead.

The planned response to a supporting experiment was to change the explicit
reference mission to return, land, upload, then disarm and finish, retaining
native radio semantics and verifier criteria. The observed R2 return time leaves about
97 seconds in the 300-second horizon. A proposed 20 m descent at 0.7 m/s takes
about 28.6 seconds before settling, upload and terminal dwell; this is a planning
assumption to verify in the next actual flight. Update the reference world's
mission dependencies and participant together; do not change the model-agent
release or silently switch the Wi-Fi rate.

The native comparison supported the hypothesis. With the failed attempt's
actual 1311-byte payload, seed and complete radio profile, the measured ENU
`(-260.022425, -310.002256, 20.004143)` position delivered zero of two messages.
The proposed launch-body position `(-260, -310, 0.137)` delivered both at
203010365355 ns and 204000085885 ns. Both cases had zero computed obstruction
loss. This is a diagnostic native-link result, not a formal flight or Business
acceptance. Evidence: `reference-native-radio-comparison.log`; the reproducible
tool is `tools/probe_inspection_reference_network.py`.

The reference participant and world's explicit dependency order now use
inspection → return → land → upload → disarm/finish. R3 measured the actual
landing flags, deliveries and sealed evidence with this strategy. Radio profile,
propagation delay, queue rate, loss semantics, horizon and all verifier
thresholds remain unchanged.

## R3 measurements and landing failure

R3's participant completed at tick 522, 261 simulated seconds. All runtime
workloads exited zero and 13 declared artifacts sealed. Two messages carrying
the exact 1311-byte aggregate report arrived at 256510360090 ns and
256510386355 ns. The source lock and original runner summary remain unchanged.
The declared OCI verifier crashed; repaired host code is diagnostic only.

After repairing delivery ordering and frame-event authority, the diagnostic
evaluates all 15 criteria. Fourteen pass, including report outcome and F1=1.0.
Landing fails. The measured transition is:

| Tick / seconds | AGL (m) | Vertical velocity (m/s) | Pad contact | Landed / in air |
| --- | --- | --- | --- | --- |
| 503 / 251.5 | 0.309672 | -0.699410 | false | false / true |
| 504 / 252.0 | 0.149800 | +0.013042 | true | false / true |
| 507 / 253.5 | 0.149855 | +0.018400 | true | true / false |

The R3 v2 rule required a new landed transition with ground contact, negative
vertical velocity of magnitude at most 0.35 m/s, AGL within its 0.5 m limit,
and launch-pad containment. MAVSDK's landed/in-air flags lag native contact
by 1.5 seconds in this attempt. Its telemetry supplies velocity, while Gazebo
supplies pose and contact. The prior descent exceeds the speed limit; the
landed transition has positive velocity. Neither qualifies under that rule.

No telemetry is replaced with a finite-difference estimate or zero, and no
verifier threshold or simulator parameter is changed. The land-before-upload
strategy solved this attempt's report-delivery failure but did not satisfy
landing acceptance. A new parameter experiment needs a source-specific landing
model and must retain this failed result. The exact diagnostic and transition
samples are in `reference-r3-interactive-verifier-diagnostic.log` under the
backend evidence directory. This host result does not mint a formal verdict.

## Approved v3 landing revision and next-run model

The user approved separating native descent/contact from MAVSDK's grounded
confirmation. Verifier configuration and policy now require schema v3; the
verifier OCI implementation is `0.4.0-inspection-verifier.2`. Schema v2 is not
read as the new rule. The 15 goals and all numerical thresholds stay unchanged.

The previous airborne sample must report downward velocity and a higher native
altitude than first contact. Native contact must then remain continuous on the
selected pad through the first grounded landed confirmation. Every grounded
sample must satisfy the existing containment, permitted-contact, 0.5 m AGL
and 0.35 m/s vertical-speed checks. The speed limit remains a grounded
confirmation limit, as in the original rule; neither rule establishes an
unmeasured impact-speed limit. Existing stopped dwell and disarm checks remain.
No flight-controller parameter changes are needed for this measurement repair.

R3 is not reclassified. R5 rebuilt every declared image from the frozen source
closure and used a new Run ID and independent OCI verifier. Host diagnosis and
mechanical fixtures did not supply that verdict.

Before increasing the ledger budget, the capacity model uses the measured R3
peak of 636079 bytes per tick over the unchanged 600-tick horizon, plus
230269 tick-zero bytes, 32 MiB fixed overhead and twelve 2 MiB image envelopes.
That planning envelope is 440597925 bytes, below the R5 512 MiB ledger
limit by 96272987 bytes. The peak-based envelope is an estimate, not a proven
worst-case bound. The 2 GiB artifact volume must still pass preflight with all
declared artifact maxima; a failed bound must stop materialization. Flight,
radio, traffic and simulation-clock parameters remain unchanged.

## R5 measured result

The fresh run completed 524 ticks, 262 simulated seconds, and passed all 15
independent goals with F1=1.0. Its sealed event ledger occupies 225682396 bytes,
leaving 311188516 bytes under the 512 MiB limit in this attempt. This is a
measurement of R5, not a universal capacity bound. All 13 declared runtime
artifacts sealed, and the read-only replay audit passed with 42 published files
and 106070604 bytes. Exact identities and logs are in
[the backend validation report](backend-track-b-validation.en.md).
