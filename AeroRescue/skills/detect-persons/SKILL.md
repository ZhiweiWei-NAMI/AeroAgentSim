---
name: detect-persons
description: Run the installed ONNX person detector on a local frame.
---

# detect-persons

Run the installed ONNX person detector on a local frame.

Input: frame_id. Analysis time is injected by the worker.

Run through the already-running local worker:

    python -m aerorescue.companion.tools detect-persons --data-dir /data --request '{"frame_id": "u2-frame-0001"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
