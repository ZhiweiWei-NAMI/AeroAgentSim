---
name: read-evidence
description: Read a local artifact or radio-delivered copy.
---

# read-evidence

Read a local artifact or radio-delivered copy.

Input: artifact_id registered in this local store. Add frame_id to select a real observation, or message_id to select one radio-received copy (mutually exclusive). These references must match the artifact. A message_id or frame_id can also select its RGB artifact without artifact_id. Content hashes can be shared by multiple observations; artifact_id alone does not identify an observation time or sender.

Run through the already-running local worker:

    python -m aerorescue.companion.tools read-evidence --data-dir /data --request '{"artifact_id":"ARTIFACT_ID"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
