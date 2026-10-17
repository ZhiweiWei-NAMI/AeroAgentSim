---
name: read-evidence
description: Read a local artifact or radio-delivered copy.
---

# read-evidence

Read a local artifact or radio-delivered copy.

Input: artifact_id registered in this local store (including received copies).

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools read-evidence --data-dir /data --request '{"artifact_id":"ARTIFACT_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
