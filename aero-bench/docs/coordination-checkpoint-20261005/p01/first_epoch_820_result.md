# Fixed first-epoch Qwen K1/K8 result

ORACLE/NOISY-FUTURE INFORMATION DIAGNOSTIC; not deployable forecast evaluation

Both arms completed820 cumulative B2 updates, covering the same1640 TRAIN windows once. Each fixed endpoint scored the same820 VALID windows once. The original538 results remain preserved.

| Updates | K | Predicate | F1 | RPS | Terminal Brier | Terminal BCE | Hybrid | Timing MAE(s) | Timing n | TP/FP/FN/TN | Known F10 |
|---:|---:|:---|---:|---:|---:|---:|---:|---:|---:|:---|---:|
|538|1|L|0.741935|0.031028|0.047963|0.217461|0.137883|0.971818|26|23/1/15/307|346|
|538|1|P|0.250000|0.041356|0.071510|0.378432|0.227222|1.170973|60|10/10/50/750|820|
|820|1|L|0.767677|0.026328|0.037350|0.098343|0.074279|0.992464|26|38/23/0/285|346|
|820|1|P|0.485830|0.047044|0.094302|0.451935|0.267761|1.130098|60|60/127/0/633|820|
|538|8|L|0.741935|0.023318|0.028955|0.133220|0.089304|0.951327|26|23/1/15/307|346|
|538|8|P|0.250000|0.036086|0.057108|0.315867|0.191684|1.189460|60|10/10/50/750|820|
|820|8|L|0.987013|0.016047|0.005212|0.023944|0.029219|1.004217|26|38/1/0/307|346|
|820|8|P|0.547009|0.035498|0.050421|0.343671|0.205675|1.200583|60|32/25/28/735|820|

E[T|T<=5s]=sum_{j=1..10}(0.5j*p_j)/sum_{j=1..10}p_j; MAE on exact supported first-flip labels, including F1 false negatives with positive finite within-window mass. Interval-censored and UNKNOWN labels excluded. Tail classT>5s has no assigned timestamp.

Observed-history linear8I: last two known raw states projected at0.5..5s, first opposite-rule bin c; +8*one_hot(c) per branch before softmax and equal Kmean. No donor prior.

128 original pilot windows-updates then410 normalized updates then282 normalized updates; one1640-window permutation completed once

No architecture, normalization, prior strength, noise, threshold, loss or optimizer policy was changed during this continuation. These oracle/noisy-future inputs do not establish causal forecaster quality or deployable performance.

| K | Train(s) | VALID(s) | Wall(s) | Peak allocated GiB | Mean new TRAIN Hybrid | First32 / Last32 batch mean |
|---:|---:|---:|---:|---:|---:|:---|
|1|277.46|81.48|364.08|4.232|0.158681|0.236541 / 0.113002|
|8|1587.45|486.65|2076.81|8.662|0.118528|0.198732 / 0.066095|

TRAIN statistics are actual per-batch objectives; the suffix contains different windows, so this is no convergence claim.

Actual continuation allocation: 0.6780 GPU-hours. Monetary cost is unavailable.

Evidence: `/mnt/data1/weizhiwei/AERO_WORLD_runtime/qwen35/outputs/first_epoch_820_seed0_v3/comparison.json`, each arm's `receipt.json`, `updates.jsonl`, `full_VALID_predictions.npz`, `last820.pt`.
