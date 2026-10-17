---
name: hold-relay
description: Fly a public candidate, probe the path, and retain the relay commitment.
---

# hold-relay

Fly a public candidate, probe the path, and retain the relay commitment.

Input: candidates: public legal ENU positions; peers: endpoint IDs; optional message_ids to hold for.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools hold-relay --data-dir /data --request '{"candidates":[[10,30,30],[10,60,40]],"peers":["centre","u2"]}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
