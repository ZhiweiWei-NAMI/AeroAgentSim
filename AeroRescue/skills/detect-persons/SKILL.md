---
name: detect-persons
description: Run the installed ONNX person detector on a local frame.
---

# detect-persons

Optional configuration `perception.detector_tile_size: 640` keeps the full-image view and adds overlapping tiles with NMS at the unchanged 0.5 confidence threshold. It is disabled by default. A 1280×960 image uses 7 inference views; measured median CPU wall time was about 2.52 seconds in the recorded mock fixed-frame evaluation.

Run the installed ONNX person detector on a local frame.

Input: frame_id. Analysis time is injected by the worker.

Run through the already-running local worker:

    python -m aerorescue.companion.tools detect-persons --data-dir /data --request '{"frame_id": "u2-frame-0001"}'

The CLI queues the request and returns accepted. Poll with `python -m aerorescue.companion.tools --data-dir /data --status REQUEST_ID`. Request references must come from this run. A pending operation yields; it does not mean completion. Missing models, sensors, estimates, routes or receipts remain unavailable. The example illustrates the invocation shape; substitute actual required fields and IDs, and retain real tool-trace output. No sample here claims an executed rescue.
