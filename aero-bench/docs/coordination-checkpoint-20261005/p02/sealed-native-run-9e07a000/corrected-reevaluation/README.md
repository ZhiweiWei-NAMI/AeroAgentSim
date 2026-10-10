The same sealed native run 9e07a000... passed all 15 goals with complete coverage under the existing datum-aware verifier. No new mission ran. The original failed runner result, source world, compiled goals, vertices, 1e-6 metre tolerance and seal remain unchanged.

Read [result-summary.json](result-summary.json), [reevaluation-receipt.json](reevaluation-receipt.json) and [the full report](outputs/verifier/report.json) together. The report names the original run; the receipt binds the separate evaluation ID e6a653a3..., actual immutable executable image sha256:5d9c75ae... and source closure 97c9a5a5.... The original contract still declares the old verifier. This evaluation does not rewrite that declaration or its original error.

The deployed old image checked converted AMSL even for declared ENU planes. The existing [formal_v2_sealed.py](formal_v2_sealed.py) selects the declared vertical datum. The operational geofence's ENU plane spread is zero; its converted AMSL spread is 0.0071016643196344376 m. The unchanged strict check passes in the declared reference.

Stage3 finished in 417.351 seconds with exit0 and a passed report. The prior stage2 output-volume failure remains in its separate receipt. Original input/seal volumes were read-only; this evaluation used a new executor-style non-root tmpfs output volume. Existing services were unchanged.

This closes the coordinate-reference verification issue for this sealed mission. The post-run map/GIF shows the same run's poses through touchdown. It is a stepped browser review, not the unfinished live Control reconnect or complete configuration-to-UI-start acceptance. Raw private event ledgers, original model/map assets and sensor imagery are excluded.
