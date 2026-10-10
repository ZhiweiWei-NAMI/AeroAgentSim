# Independent review: bounded Observer graph prototype

Review date: 2026-10-06. Scope: `/workspace/shared/p01-observer-prototype-20261006` against the reviewed tensor contract. Only the new prototype was inspected and exercised; no original 820 experiment was run or modified, no package was installed, and no model was downloaded.

## Decision

The NumPy reference is a runnable, random-weight **contract prototype**, not actual Qwen execution, multimodal training, or a validated predictor. After author fixes and independent rechecks, it passes this narrow NumPy contract scope. No remaining reproduced NumPy blocker was found. PyTorch source parses, but every PyTorch execution/gradient claim remains unverified because PyTorch is absent. Exact frozen clamp/mask parity is also unverified and must not be reported as passed.

## Concrete findings and author responses

1. **Fixed and runtime-rechecked: Unix timestamp cutoff tolerance.** Default `np.isclose` relative tolerance allowed a 21-tick history shifted two seconds past a cutoff of 1,800,000,000 to pass. Observation event/tick matching had the same weakness. The author changed comparisons to `rtol=0`, an explicit small absolute tolerance, and a direct history-cutoff bound. Independent cutoff and observation-tick regressions pass.
2. **Fixed in source; runtime blocked: future Torch timestamp precision.** Forming absolute timestamps in float32, or casting them to model dtype before subtracting cutoff, erased all ten 0.5-second offsets at Unix-scale time. Source now creates absolute rollout times in float64 and subtracts cutoff before casting relative time. A NumPy float32/float64 analog reproduces and resolves the numerical defect; the actual Torch regression is skipped, not passed.
3. **Fixed by explicit scope narrowing: schema-dependent geometry.** Geometry assumed the first two fields were position while the input contract initially accepted arbitrary field schemas. History now rejects every schema except ordered `(x, y)` with common units/frame, and rejects unsupported field domains. This is a correct fail-closed bounded implementation, not generalized physical-state support.
4. **Fixed and runtime-rechecked: duplicate association overlap.** Two associations covering the same `(observation_id, entity_id, token_index)` with scores 1.0 and 0.2 were accepted. Reversing association order changed a graph edge weight by 0.8, because the dense adjacency used last-write-wins. The author chose explicit rejection of duplicate `(observation, entity, token)` coverage, while preserving one observation token shared by distinct entities. The independent regression now passes; the pre-fix failing output is preserved.
5. **Fixed in source; runtime blocked: Torch CDF-label bounds.** The NumPy loss rejects known targets outside `[0,1]`; the Torch loss initially checked finiteness only. A timing-only out-of-range target could silently become an invalid training label. The author aligned validation and added the skipped-until-Torch regression. Source reinspection confirms the finite `[0,1]` guard.

## What was actually exercised

- End-to-end NumPy smoke: history 21 samples/10 seconds, rollout `[8,10,3,2]`, logits `[8,2,11]`, final probabilities `[2,11]`.
- Scene tokens stored once per Observation; adding a second entity association does not clone scene nodes. Owner and local token references are explicit.
- Strict numeric/observation/association support and availability cutoff rejection; invalid numeric storage does not become valid evidence.
- Structural entity reindexing preserves graph features, edge types/weights, and named-query truth binding.
- Ten sample-specific steps use each branch's own prior state; sampled geometry is rebuilt from the actual branch state.
- Nonzero cross-field/entity covariance from shared low-rank noise, positive-definite covariance, and the correct observed-subvector NLL marginal.
- The same query is evaluated on all K paths. RULEMC equals the average of per-path exact rule one-hots. Unknown paths are not dropped or relabeled bucket 11.
- First flip from initially false and initially true, flip-and-return on the sampled grid, censoring, unknown initial truth, and changed values observed after a gap.
- Softmax of each path's 11 logits before a single K probability mean. An asymmetric fixture distinguishes it from averaging logits.
- Known-CDF MSE on j=1..9 plus 0.5 times known terminal BCE on F10, independently normalized masks, empty-mask zero, and unknown NaN targets ignored. Reported RPS includes j=1..10.
- Separate NumPy Stage1/Stage2 parameter storage.

Final recheck: **14 independent NumPy tests passed; 26 authored NumPy tests passed; 11 Torch tests skipped; zero observed failures.** Python compilation and AST parsing passed. `verification.json` records SHA-256 hashes for the reviewed source and test files. At the initial checkpoint, an additional overlap test exposed finding 4; its failing result was preserved before the fix.

## Static PyTorch review

The source uses a single historical backbone encoding followed by a recurrent graph transition decoder, a correlated low-rank Gaussian with `std.square()` as its covariance diagonal, per-branch feedback, independent Stage2 parameter objects, per-path softmax followed by K-mean, and a frozen-Stage1 helper. No future ground-truth labels are arguments to the forward rollout or risk head; labels enter the loss separately. The local-only Qwen adapter is never used by the NumPy smoke and has no automatic download path. Its intended `inputs_embeds` / hidden-state interface is consistent with the [official Transformers Qwen3.5 documentation](https://huggingface.co/docs/transformers/model_doc/qwen3_5); that does not validate the adapter against an installed model.

The authored Torch suite contains forward, shape, gradient-through-frozen-backbone, Stage2-freeze, parameter separation, low-rank marginal, hybrid parity, and absolute-time checks. Their skip output is evidence of the missing dependency, not evidence that these behaviors work. Compilation/AST parsing cannot establish runtime shape or gradient correctness.

## Explicit limits before scientific use

- Single scene, fixed entity set, continuous 2D positions, one distance predicate; no birth/death, controls, discrete state, general predicate library, or sparse/subgraph compression.
- Synthetic image/point/log embedding tokens; no raw sensor encoder, pretrained multimodal extraction, LoRA run, actual Qwen forward, optimization, GPU profile, or quality/calibration result.
- Correlated **unimodal low-rank Gaussian**, not the optional scene-mode mixture.
- Initial truth is conditional on the supplied valid G0; uncertainty metadata does not create an initial belief sampler.
- Graph/query reindexing is semantically correct, but the causal backbone does not guarantee permutation-invariant predictions.
- Discrete 0.5-second event semantics only; a flip and recovery wholly between observed ticks is not detectable.
- Default parameters are independent. An optional copied encoder is deep-copied before use; its runtime storage/gradient test remains blocked.
- Frozen legacy mask/clamp and edge-case reductions have not been imported or matched. Formula-level agreement is insufficient to claim exact legacy regression parity.

## Reproduction

Run from the prototype directory:

- `python smoke.py`
- `python independent/test_independent.py`
- `python -m unittest discover -s tests -v`
- `python -m compileall -q observer_proto tests independent`

Artifacts: `smoke-review.json`, `numpy-adversarial-tests.txt`, `author-suite-recheck.txt`, `verification.json`, and `numpy-adversarial-pre-overlap-fix.txt`. These files contain test evidence, not model performance estimates.
