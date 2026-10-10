These are six **candidate** results, not an adopted release or recapture instruction.

The same declared station-egress workload uses two 4 Mb/s, 1200-byte UDP flows,
2.4 ms periods staggered by 1.2 ms on [24,46) seconds. RF, heartbeat TTL and rules
remain unchanged. Offered workload and receiver-observed heartbeat deadline
misses are separate facts. Recovery may coincide with bulk completion and the
preloaded rendezvous; these results do not establish channel-switch-only recovery.

Five chains complete. L6-4_v1 seed00 fails: its main owner's ready-report attempts
at 46.1/46.2/46.3 seconds have no native RX; exact tagged-endpoint ARP-cache drops
occur at 50.1 seconds, after the 48.1-second application deadline. The callback
does not expose a further ARP failure reason. No main alternate command, ACK or
landing follows. Its secondary aircraft lands; the main aircraft remains airborne.
Thresholds, TTL, load and original failed results were not altered to force success.

Each case retains the executed contract, full small receipt/admission/action
records, and selected original native packet/diagnostic, guard and pose rows.
`selection.json` lists original paths, full source hashes, 1-based row references
and raw-line hashes. Projected record values are unchanged; unrelated bulk packets
and trajectory rows were omitted. No original RGB/LiDAR, full map or credentials
are included. Internal `adopted/` directory names come from the existing numerical
API and do not change this candidate-only status.

Seed02 V1 reuses the independently reviewed v1 numerical run. The separate
metadata-only proof contains nine exact v1-to-v2 wording/version changes; it does
not claim that v2 was numerically rerun. Other five cases executed frozen v2 inputs.
The original peer-load no-event result, published210 and ARM remain unchanged.
