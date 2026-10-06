# L6 shared reliable control proposal

Status: proposed, not implemented or activated. No new traces or accepted runs.

One policy covers both owners, both L6-4 variants and all three seeds.
Stage deadline 5.2 s; overall deadline 29.8 s; retries every 100 ms,
at most 52 attempts per stage. Per-attempt expiry is the earliest of
send+2 s, fixed stage deadline, fixed overall deadline, and endpoint lifetime.
Heartbeat TTL remains 0.2 s. Retries never slide logical validity.

The stage basis is authored ARP 3x1 s + existing command delivery 2 s +
two strict-before 100 ms boundaries. ARP configuration still needs an explicit
backend pin; this budget is not a measured guarantee or a seed-conditioned fit.
Overall adds four stage budgets, the existing longest landing timer 8.8 s,
and 0.2 s boundaries. Exact endpoint and all-actor suffix support must close.

READY -> GRANT -> EXECUTION_REPORT -> LAND uses native UDP correlated replies.
REPORT_ACK is informational; valid LAND also acknowledges the report.
LAND_ACK(APPLIED) proves dispatch only, not touchdown. Duplicate DATA may replay
a cached reply, never repeat physical motion. Timeout preserves existing local
hold and reports failed/unconfirmed; it does not invent autonomous landing or
cancel already-admitted motion.

Compact UTF-8 JSON must carry actual scope/deadlines/payload over UDP. Proposed
cap is 1024 bytes; serialized sizes remain to be measured. The existing 64-byte
dummy command size cannot stand in for a larger ledger-only payload. The stated
655360 bit/s application ceiling assumes at most four DATA exchanges plus four
coalesced replies at 10 Hz; link and MAC overhead remain additional.

Native review should resolve the ARP pin, payload encoding/size, immutable
stage/overall deadline conventions, and endpoint/timer support before any run.
Original failed candidates remain intact. This file does not certify landing,
final adoption, sensor mapping or the final collection list. The user will
collect once after all modifications, input synchronization and acceptance.
