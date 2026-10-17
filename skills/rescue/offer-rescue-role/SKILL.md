---
name: offer-rescue-role
description: Send a local capability and cost bid through radio.
---

# offer-rescue-role

Send a local capability and cost bid through radio.

Input: destination, optional route. Uses the last actual estimate-assignment result.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools offer-rescue-role --data-dir /data --request '{"destination":"centre"}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
