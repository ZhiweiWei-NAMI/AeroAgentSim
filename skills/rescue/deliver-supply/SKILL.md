---
name: deliver-supply
description: Fly to a safe selected point, then command the physical release actuator.
---

# deliver-supply

Fly to a safe selected point, then command the physical release actuator.

Input: package_id, drop_site_id produced by select-drop-site.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools deliver-supply --data-dir /data --request '{}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
