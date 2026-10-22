# Owner-mediated mission commands v1

**Available for the lowalt Tasks owner (M1).** External orchestration can reserve,
divert, resume and release a vehicle through kernel commands and receipts. It
must not access `owner.flights`, `Flight`, `Job`, or another owner's fields.
`MISSION_API_VERSION = 1` versions these message contracts, not the kernel Python
ABI or the separate scenario plugin API.

## Selecting and sending commands

The [public API](../../src/aeroagentsim/mission_api.py) provides frozen payload and
result dataclasses, `ResultCode`, pure `build_payload` / `validate_payload` and
`build_result` / `validate_result` helpers, and fresh `mission_descriptors()`.
The [lowalt overlay](../../src/aeroagentsim/packs/lowalt/overlay.py) includes the
four command descriptors and the status event. Tasks advertises the commands in
its kernel `Partition.commands`; no new entry-point family or owner callback is
needed. Send to the selected Tasks **partition ID**, not necessarily `tasks`.
An orchestration partition declares the four schemas in `emits` and the Tasks
partition in `message_targets`, then uses `ctx.submit(CommandRequest(...))`.

Generated lowalt documents and existing lowalt expansion install the inline
descriptors. A custom registry must explicitly include the complete descriptor
set to enable the capability. A registry containing none retains its historical
Tasks declaration; a partial mission descriptor set is rejected. No maintained
registry snapshot or overlay YAML has been changed: the existing inline overlay
and missing-message expansion are sufficient.

| Schema / Python payload | Extra request members | Terminal success means |
| --- | --- | --- |
| `aas.mission.reserve.v1` / `ReservePayload` | `expires_ns`; optional `expected_owner_revision` | The owner has saved the continuation, excluded the vehicle, and observed an actual successful downstream hold with committed zero velocity and reconciled interrupted motion. |
| `aas.mission.divert.v1` / `DivertPayload` | Token, expected revision, `target`, `frame` | The final downstream move succeeded **and** committed position is within the configured arrival radius of the target, with zero velocity. Planning, acceptance and intermediate waypoints are not completion. |
| `aas.mission.resume.v1` / `ResumePayload` | Token, expected revision | Outstanding mission motion has been stopped/reconciled and the saved continuation restored. This does **not** mean the original job has finished. The reservation remains active. |
| `aas.mission.release.v1` / `ReleasePayload` | Token, expected revision, `disposition` | Mission motion has been reconciled, the requested disposition applied, and the token retired. |

### Request and result shapes

Every request contains:

- `vehicle`: the full kernel `EntityRef`, serialized as `{"$ref": {"run_id": ...,
  "epoch": ..., "id": ..., "generation": ..., "type_id": ...}}`. A bare ID is
  insufficient; another generation, run, epoch or type is not the same vehicle.
- `mission_id`: a nonempty caller-defined mission identity, constant for a token.
- `request_id`: a nonempty identity unique for that command origin for the run.
- `deadline_ns`: a positive **absolute simulation nanosecond** deadline. No success
  is issued at or after the deadline; a new request needs a new request ID.

Reserve additionally requires `expires_ns >= deadline_ns`: an absolute lease
expiry independent of the reserve command's completion deadline. Post-reserve
requests require `reservation_token` and a nonnegative integer
`expected_owner_revision`. The token is owner-issued and scoped to the full
vehicle reference, mission and actual kernel command origin (source kind/ID).
It is a correlation/ownership capability, not a security boundary for untrusted
Python plugins.

Divert's `target` is a finite three-vector. **Lowalt v1 supports `frame="enu"`, in
metres in the scenario's bound lowalt frame; it performs no frame conversion.**
The domain-neutral helper does not assume ENU or import lowalt policy.
Release's `disposition` is exactly `resume_saved` or `idle`.

Every terminal mission result contains `vehicle`, `mission_id`, `request_id`,
`command_id`, `code` and `owner_revision`. Optional members are
`reservation_token`, `downstream_command_ids`, `downstream_receipt_refs` (records
with `record_index` and `item_index`) and diagnostic `detail`. Builders include
these optional members explicitly. Consume the typed code, not diagnostic prose.
The same shape is used for feedback and `aas.mission.status.v1` events on topic
`aas.mission.status`.

The revision is the owner's **per-vehicle mission-control revision**, initially
zero, incremented for each accepted mission command and lease expiry. It is not
a revision of every task or physical fact. Rejections do not advance it. Initial
feedback reports the accepted revision; terminal results report the current one.
Use the returned revision for the next command. Omitting the initial reserve's
expected revision permits reserving without a previous mission result; supplying
it enables compare-and-set against earlier mission transitions.

For example, inside an orchestration engine with the declared routes:

```python
from aerokernel import CommandRequest
from aeroagentsim.mission_api import RESERVE_SCHEMA, ReservePayload, build_payload

ctx.submit(CommandRequest(
    RESERVE_SCHEMA, tasks_partition, ctx.now,
    build_payload(ReservePayload(
        vehicle=vehicle_ref, mission_id="inspection-7", request_id="reserve-7",
        deadline_ns=ctx.now.ns + 10_000_000_000,
        expires_ns=ctx.now.ns + 120_000_000_000,
    )),
))
```

`ctx.submit` returns a local causal reference, **not** a command ID. Learn the
actual ID from routed `submitted` receipts. Retain the token and revision from
real feedback/results. Kernel action history can include feedback and cancel
decisions; select the actual terminal receipt rather than assuming its last
entry is a result. In particular, `code="ok"` in feedback is not a successful
command: only the `succeeded` receipt carries that promise.

## Continuations, exclusion and release

Tasks retains the original job, phase and unfinished work obligations privately.
Reservation pauses an attached job; it does not pick up, deliver, consume payload
or complete work. Resume replans from the **current authoritative position** to
the original job's current endpoint, preserving unfinished original work
waypoints. It never resurrects old motion commands, stop receipts or an obsolete
`paused_route` snapshot. Actual subsequent task receipts drive pickup, work and
completion as before.

The vehicle is excluded as soon as reserve is accepted, including while its hold
is pending. It stays excluded through diversion, arrival, safety pauses, cleanup,
and explicit resume until release or completed lease cleanup. This includes
allocation snapshots, asynchronous stale allocation replies, priority preemption
and cooperation selection. Explicit resume may execute the original job under
the reservation, but it does not make the vehicle available for a different job.

- **`release(resume_saved)`** restores the saved continuation and retires the
  token. If resume already restored it, release only retires the token: it does
  not rewind or stop the original task's new, non-mission motion.
- **`release(idle)`** reconciles the stop, fails an abandoned saved job with reason
  `mission release idle`, clears its vehicle assignment and leaves the vehicle
  idle/allocatable. It is rejected as `protected` while any payload is aboard;
  idle does not authorize discarding cargo. After explicit resume, it is rejected
  as `safety_paused` while a safety trip/hold is active, preserving that trip and
  the token; retry with a fresh request after safety clears. A subsequent
  allocation is possible after the disposition has committed, not as part of
  release success.
- A fresh resume/release may supersede an executing divert using the accepted
  revision. The divert gets a terminal `failed` receipt with `canceled`; the
  replacement still waits for the real hold and reconciliation before success.
  A second divert or any operation racing an unfinished reserve is `busy`.
  Repeating resume after restoration is also `busy`.

All four commands leave `lowalt.payload_kg` unchanged. Once restored, the original
task's ordinary pickup/delivery lifecycle may change payload independently.
An external delivery/consume contract and the embedded Rescue migration are
**planned for later phases**, not capabilities granted by mission v1.

## Safety, failure and expiry

Reserve rejects protected commitments: passenger jobs, configured cooperation
protected kinds or replacement-required jobs, a previously suspended job,
cooperation reservations, non-task control purposes and the embedded
Rescue helper's entire authored participant roster (including pending plans).
This M1 isolation lets the embedded helper operate unchanged; it is not an
external Rescue engine or a replacement for that helper. Reserve/divert also
check committed energy dispatch thresholds and payload capacity. The motion
owner remains responsible for its real trajectory/energy budget and can reject
or fail a downstream command.

Weather holds, energy recall/charging, separation moves and active no-fly changes
have priority. Interrupted mission commands fail observably as `safety_paused`
and the status event reports the pause; the token and saved continuation remain.
A safety release event does **not** automatically retry a failed divert. Use a
fresh request and current revision after safety clears. Mission cleanup must not
replace an ongoing charging/avoidance trip; it waits for the ordinary safety
lifecycle before restoring the continuation. Existing
`lowalt.airspace.flight_control` receipts keep their original, weaker semantics:
they acknowledge emission of control, not committed quiescence or mission
arrival.

An execution-failed reserve can return a token for recovery; failure is not
proof that the vehicle is free. A divert deadline stops that diversion and
retains the reservation for explicit recovery. A reserve deadline or lease
expiry initiates fail-closed cleanup:
observe/report expiry, reconcile actual motion, restore the saved continuation,
then retire the token. An already resumed continuation is not rewound on expiry.
A failed hold never silently frees a possibly moving vehicle: it stays excluded
and reports failure/expiry. Before lease expiry, a fresh resume/release can retry
reconciliation; afterward, successful owner/safety hold or charging recovery is
needed before automatic cleanup can finish (expired tokens cannot authorize new
missions). Expiry during charging waits for the charging lifecycle. Status events
identify the original reserve command/request and token, including expiry without an
active command; command receipts remain authoritative for command outcomes.

The four descriptors advertise `cancel_support=False`: generic kernel cancel
requests are not a physical-stop API. Use resume/release supersession for an
executing divert. V1 has no independent renew, revoke, payload-consume or arbitrary
cancel command. `revoked` is a reserved outcome code for a future owner revocation
policy, not a claim that lowalt exposes such an operation today.

### Deterministic result codes

| Codes | Meaning |
| --- | --- |
| `ok` | The command-specific committed success condition above holds (or nonterminal progress, when carried only in feedback). |
| `invalid_payload` | A wire-valid request violates a semantic shape constraint, such as lease expiry preceding reserve deadline. Syntactically invalid records are rejected by kernel schema admission before owner dispatch. |
| `unknown_vehicle`, `stale_entity` | Vehicle outside the authored Tasks fleet, or mismatched full identity. |
| `already_reserved`, `not_reserved`, `unknown_token`, `stale_token` | Conflicting reserve; no reservation; token/origin/mission mismatch; previously retired token. |
| `stale_revision`, `expired` | Compare-and-set mismatch; expired request deadline or lease. Retired expired tokens continue to report `expired`. |
| `duplicate_request`, `request_conflict` | Same origin/request ID and same normalized command; or same ID with different schema/payload. Neither reruns physical effects or reports cached success. |
| `busy`, `protected`, `safety_paused` | Conflicting outstanding operation/already restored resume; protected commitment or loaded idle disposition; safety interruption/ineligibility. |
| `insufficient_energy`, `capacity_exceeded`, `unsupported_frame` | Owner-side eligibility checks failed. A downstream energy rejection is instead observable as `motion_failed`. |
| `route_unavailable`, `motion_failed`, `arrival_unconfirmed` | No safe route (including an exhausted planner search); downstream stop/move failed/rejected/canceled; final success receipt lacked authoritative arrival. |
| `canceled`, `revoked` | Divert superseded by resume/release; reserved for later explicit owner revocation policy, respectively. |

Checks are ordered: semantic decoding; origin/request-ID deduplication; full
vehicle identity and request deadline; reservation/token/lease checks; revision;
then capability/conflict/safety policy. Double reserve reports `already_reserved`
before checking its optional revision. A retired token reports `stale_token` (or
`expired`) before revision checks. A never-issued token with no reservation is
`not_reserved`; a mismatched token while a reservation exists is `unknown_token`.
Failed/rejected commands must not be retried under the same request ID.

## Evidence and verification

The [kernel-driven tests](../../tests/packs/test_mission_control.py) use a
non-rescue caller, real receipts and committed reads; orchestration never obtains
a live owner. They cover lifecycle/restoration, idle, exclusion, invalid/stale
requests, safety and journal-only replay. Replay consumes recorded messages,
receipts and state without constructing mission engines or contacting providers.
These are generated acceptance tests, **not a claim of a passing test run**:
builds and tests were intentionally not executed for this change.
