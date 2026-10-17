---
name: package-evidence
description: Encode a traceable image crop within a byte budget.
---

# package-evidence

Encode a traceable image crop within a byte budget.

Input: artifact_id, optional bbox_xyxy, budget_bytes.

Run through the already-running local worker:

    python -m aeroagentsim.companion.tools package-evidence --data-dir /data --request '{}'

The CLI queues the request and returns accepted. Poll with `python -m aeroagentsim.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
