---
name: probe-route
description: Submit actual radio probes and return pending while the world advances.
---

# probe-route

Submit actual radio probes and return pending while the world advances.

Input: destination, optional route. Result waits for actual radio round trip.

Run through the already-running local worker:

    python -m aerorescue.companion.tools probe-route --data-dir /data --request '{"destination": "centre", "route": ["u2", "u3", "centre"]}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
