# Observer graph → joint rollout → query risk: bounded prototype

Date: 2026-10-06. This is a new standalone prototype. It does not modify the old 820 experiment or any checkpoint. It has no data scans, network code, server launcher, automatic installer or optimizer/training loop.

## What is actually verified here

The NumPy reference runs end to end on three fixed entities, two unbounded position fields, three Observer-owned synthetic image/point/log token sets, and two distance queries. It uses **random tiny weights**, not Qwen and not trained dynamics. The PyTorch implementation is supplied separately with explicit CPU gradient tests. This executor has NumPy but **no torch, torch_geometric or transformers**. Locally, gradient/Qwen checks are blocked, not passed. PyG is not needed by this dense tiny implementation.

Current local report: 26 NumPy contract tests pass; 11 PyTorch tests are explicitly skipped. `reports/unit-tests.json` is authoritative if subsequent review adds cases. `reports/numpy-smoke.json` records the tiny smoke. There is no prediction-accuracy result.

## Run without installing anything

From this directory:

- `OPENBLAS_NUM_THREADS=1 python smoke.py`
- `OPENBLAS_NUM_THREADS=1 python run_tests.py`
- `python -m py_compile observer_proto/*.py tests/*.py smoke.py torch_smoke.py run_tests.py`

In an **existing, authorized PyTorch CPU environment**, with this source copied to a new prototype directory:

- `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python run_tests.py --require-torch`
- `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python torch_smoke.py`

The `--require-torch` option exits 2 if PyTorch is absent. `torch_smoke.py` makes one synthetic Stage1 likelihood backward and one frozen-Stage1 Stage2 hybrid backward. It performs no optimizer step, no GPU work, and no actual Qwen load. The NumPy and PyTorch tiny networks are separately instantiated contract examples, not bitwise-equivalent implementations.

## Chain and exact boundaries

1. `History.validate` checks 21 samples including both endpoints, 20 half-second intervals, per-field masks, source, uncertainty, arrival time, preprocessing support end, Observer ownership, calibration references, pose and association references.
2. Each Observation owns its embedding tokens once. The Observer→Observation edges and selected local token→entity association edges are separate from physical and same-entity temporal edges. A scene token is not cloned into every entity. Association quality weights the edge. Overlapping duplicate `(observation, entity, token)` evidence is rejected rather than overwritten or double-counted; distinct entities may reference a shared token. `potential_coverage` cannot masquerade as measured association.
3. A typed GraphTransformer fuses the history. A projector creates continuous backbone tokens. The tiny backbone can be replaced through the `inputs_embeds` interface. External entity cross-attention reads all historical hidden states. Absolute entity IDs bind records and queries; there are no learned ID embeddings.
4. Stage1 encodes history **once**. A shared graph transition decoder advances each of eight paths for ten steps. Every path consumes its own preceding graph/state. It rebuilds proximity edges from its own positions. This is explicitly different from rerunning Qwen at every tick.
5. Each next-state law is a single **scene-joint low-rank Gaussian**, covariance `diag(std²) + F Fᵀ`. Rank noise is shared across all entity/field coordinates within that path/tick; diagonal noise is additional. Autoregressive feedback produces temporal dependence. There is no unimplemented claim of a multimodal mixture head. The masked state NLL is the marginal on observed subdimensions.
6. Stage2 uses its own scratch graph encoder, shared across all queries, then a query-conditioned readout and raw operand bypass containing G0, future pair coordinates, distance, threshold margin, validity and time. Output is `[K,Q,11]`. The bypass does not require coordinates to be reconstructed from a pooled latent.
7. Each path is softmaxed first, and only then averaged over K. The hybrid objective operates on this final probability distribution.
8. Exact `distance_lt` RuleMC runs on the **same G0 and same sampled trajectories**. Unknown first-flip bins use `-1`, never the no-flip bin. If any path's rule bin is unknown, the query's RuleMC probability row is NaN with `valid=False`; no silent reweighting removes unknown paths.

The public tensor contract includes B. This bounded implementation deliberately accepts **one scene per call**, with K vectorized; production ragged/multi-scene batching is not implemented.

## Supported / unimplemented matrix

| Component | Status |
|---|---|
| Ordered `(x,y)` unbounded continuous state with explicit unit/frame/normalization/std floor | Supported; other field names/order/domains reject explicitly |
| 21 historical samples, 10 future samples, K=8 default, 11 bins | Supported |
| Missing numeric values, stable ID reindexing, measured/fused/predicted source, uncertainty | Supported; last-state unknown fields remain invalid throughout rollout |
| Observer-owned image/point/log **embedding references + supplied synthetic/precomputed tokens** | Supported contract; no raw sensor ingestion/encoder performance claim |
| Pose, calibration reference, arrival time and preprocessing temporal-support checks | Supported; calibration transformation itself unimplemented |
| Local evidence association, physical proximity and entity temporal edge types | Supported; tracking/ROI extraction/point clustering are supplied inputs, not implemented |
| Typed dense graph encoder and Stage2 GraphTransformer | Source supplied; NumPy counterpart runnable; trainable PyTorch checks need torch |
| Scene-correlated low-rank law, exact observed-subvector NLL, sample-specific feedback | Supported source/reference |
| Actual HF Qwen3.5 text-backbone loader, local-files-only | Implemented adapter, **not invoked or runtime-verified here** |
| Qwen LoRA attachment, adapter target enumeration, Qwen resource/performance profile | Unimplemented; no claim from tiny-backbone success |
| Independent Stage2 weights, optional copied transition-encoder initialization | Implemented; copying uses `deepcopy`, `assert_untied` checks aliases |
| Same-trajectory/G0 exact RuleMC for `distance_lt` | Supported; not a substitute for all 91 production predicates |
| Mean-softmax-then-hybrid formula and conservative censoring fixtures | Verified local reference |
| Exact legacy mask/clamp/empty-mask function equality | **Unverified** until frozen function source is supplied |
| Birth/death, discrete/positive/angular/bounded fields, unknown-action/control conditioning | Unimplemented; no generic 91-field support claim |
| Future image/point-cloud/Observation latent decoders | Unimplemented |
| Sparse PyG/ragged batches, graph partition/overlap reconciliation, token compression | Unimplemented; dense attention is only for bounded fixtures |
| Full node permutation invariance of a causal Qwen sequence | Not guaranteed. Graph/query/RuleMC ID consistency is tested, not learned sequence invariance |
| Real-data held-out evaluation, calibration, long training, throughput/VRAM conclusions | Not run |

## Loss contract and censoring

Zero-based bins `0..9` mean first flip at `0.5..5s`; bin `10` means no flip during the complete five-second sampled window. It is not “never”, “unknown” or a continuous-time event detector. Initial true predicates can flip to false. The initial truth is conditioned on the supplied valid numeric G0; the prototype does not sample a calibrated initial belief from measurement uncertainty. Stateful operators such as hold-duration, sliding-window and hysteresis are explicitly unsupported: G0/q0 alone cannot initialize them. They require their own versioned, cutoff-available initial rule memory or required history; the prototype rejects them and never invents zero elapsed duration. Equality for the fixture is strictly distance `< threshold`.

For `p_bar = mean_K(softmax(logits))`, CDF is cumulative bins `0..9`:

- timing = mean of known squared CDF errors at 0.5 through 4.5 seconds (nine positions)
- terminal = mean of known BCE at the full event probability `F(5s)` (not bin-10 probability)
- loss = timing + **0.5 × terminal**

Each term uses its own known-item denominator. Reported RPS separately includes all ten CDF positions. Unknown G0 invalidates the query. A known changed predicate certifies an event has occurred by that tick, even after an unknown gap; the exact first-flip bin may still be unidentified. Without a seen flip, survival is known only over a complete observed prefix. Incomplete recordings do not become “no flip”. Tick-internal flip-and-recovery is deliberately not detected.

The local prototype uses epsilon `1e-7` and zero for an empty component. These are **explicit prototype choices**, not claims of parity with unavailable legacy code. The verified frozen source JSON establishes the formula and coefficient, not every implementation edge case:

https://github.com/ZhiweiWei-NAMI/AeroAgentSim/blob/a4f49e8b1aa43cfa22552fea7cd092eac569fdac/aero-bench/docs/coordination-checkpoint-20261005/p01/first_epoch_820_result.json

Replace/compare the mask/clamp implementation when the original function is supplied. The NumPy-vs-PyTorch tests are designed to check this package's formula conformance only, but have not run here because PyTorch is absent.

## Real Qwen adapter contract

`load_local_qwen_backbone(local_model_path, revision, dtype=None, freeze=True)` requires an existing local directory containing `config.json`, plus caller-supplied revision provenance. Local paths are not downloaded or modified. `local_files_only=True` and `trust_remote_code=False` are mandatory. A compatible existing Transformers release must expose `Qwen3_5Model`; its `language_model` is wrapped, and its word-vocabulary logits are never used as state probabilities.

The caller must actually provide the desired pinned local snapshot; a `revision` string does not independently verify a local directory's contents. Record snapshot hashes before a real experiment. Initial loading includes the native vision wrapper and can transiently require its memory. The loader then releases the wrapper and retains only the text model. It does not prove a raw-image or point-cloud pipeline works.

Official interface reference checked on 2026-10-06:

- https://huggingface.co/docs/transformers/main/model_doc/qwen3_5
- https://github.com/huggingface/transformers/blob/main/src/transformers/models/qwen3_5/modeling_qwen3_5.py

Keep the projector/adapter dtype and device consistent when integrating. Frozen Qwen parameters must **not** imply enclosing its entire forward in `no_grad`: input gradients are necessary for projector training. The CPU test specifically checks that property with the synthetic backbone.

## Minimal next executable check

Run `run_tests.py --require-torch` and `torch_smoke.py` in the already authorized runtime. This closes gradient/shape verification without loading Qwen or changing the old experiment. Then recover the frozen mask/clamp function and add exact regression equality. Only after that evidence should an actual existing-Qwen forward profile or new data integration be reported as completed.
