---
name: flight-command
description: Submit an arm/takeoff/goto/hold/land primitive and yield until its actual operation completes.
---

# flight-command

Submit an arm/takeoff/goto/hold/land primitive and yield until its actual operation completes.

Input: action: arm/takeoff/goto/hold/land/disarm; params. goto uses position_enu_m and yaw_deg.

Run through the already-running local worker:

    python -m aerorescue.companion.tools flight-command --data-dir /data --request '{"action": "hold"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
