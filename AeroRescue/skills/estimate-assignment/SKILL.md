---
name: estimate-assignment
description: Estimate feasible current and predecessor-target route legs from local state.
---

# estimate-assignment

Estimate feasible current and predecessor-target route legs from local state.

Input: actions: received action list with id, role, target, duration_s and after. Full search routes and service power are included; preserve the received action geometry so centre can use the bid. Resources come from this UAV telemetry/configured energy model, never request values.

When the scenario enables an onboard energy budget, `soc` is that explicitly declared model's remaining fraction. `px4_soc` remains the original PX4 reading. Missing or discontinuous source telemetry leaves the configured budget unknown; it does not fall back to a healthy battery value.

Run through the already-running local worker:

    python -m aerorescue.companion.tools estimate-assignment --data-dir /data --request '{"actions":[{"id":"inspect-area","role":"inspect","target":[60,60,40],"duration_s":30,"after":[]}]}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
