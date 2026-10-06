# P01 B01 fixed source for parent review

This is the fixed `frozen_v1` CPU engineering source. The author finished all writes before the coordinator copied 31 text files (206,894 bytes) into a read-only local snapshot. Later implementation corrections must use another version. B02 and model training have not started.

## Execution and review

The original four suites contain 13 + 11 + 12 + 14 = 50 tests. Their different-author GLM reproductions are retained under `independent_review/`. The author's final source, including 32 provenance tests, passed 82/82 in a combined CPU run, exit 0, 76.17 seconds. The final combined JUnit and exact test names are included. These counts are distinct versions/test sets.

The written different-author initial review returns **CHANGES_REQUIRED**. Its actual code probes found missing illegal-unit rejection, two reversible-encoding marker collisions, split/group validation not wired into the supervision builder, and incomplete raw-field/operation semantics coverage. It also identifies two self-fulfilling tests and an unreachable guard. None of these are concealed by the passing suite count. The pilot contains no cells exhibiting the two encoding collisions; that is a scope-specific observation, not general correctness. The initial review was written before the final fixed-copy qualification; that follow-up will be published separately without changing these source bytes.

## Pilot and limits

Pilot identity is `L4-1_v1__seed00`, view `uav_view_000__u_inspect_l4_1_v1`, observer `u_inspect_l4_1_v1`. Raw streams counted: 901 truth frames, 78 roster entities, 180 world graph deltas, 10 events. Canonical counts are 41,472 frame/entity-state rows, 78 entity rows, 138,831 edge-operation rows, 10 events and 22 observation-index rows: 180,413 total. Counts do not prove all raw keys were preserved. The initial review itemizes dropped truth/roster/event/evidence fields and relation-operation semantics.

The middle tick450 relation points to raw world graph delta line90 and tuple `world_predicate_tuple:2020855f18484f091ae1992d`. It is a removal operation; its stored false value is the previous `from_value`, not a newly asserted false state. This distinction remains an initial-review finding.

RGB and LiDAR are indexed through 11 existing capture paths each. No RGB/LiDAR encoder or model forward runs in B01. Depth/segmentation are unavailable in this pilot. The measured reversible conversion applies to the selected canonical record set, not all original fields. JSONL/compact sizes are 124,716,471/87,668,893 bytes; the included review records the independent measurement and tokenizer-only checks.

Cutoff250 at declared10Hz precedes target300..350 step5 by5..10 seconds. Speed scaler fit is TRAIN-only, max tick250, 9,038 values, mean1.9817456184996578, std2.7378114247020586. Historical transported_q is TRUE future plus TRAIN-frozen Gaussian noise and is recorded as oracle-only lineage. Archive/model-visible helpers and source-family coupling helpers exist, but the initial review's builder-wiring and completeness findings must be resolved before claiming prediction-input acceptance.

## Reproduction inputs

`source/` preserves repository-relative source/config/report paths. Restore it to an existing AERO_WORLD checkout to run the four exact specification commands and the provenance suite using the installed qwen35 Python, CUDA disabled. The real-pilot import/roundtrip tests require the named server numerical logs, existing capture index metadata and canonical derivative at their recorded paths. Those private inputs, media, 125 MB derivative, weights and all local snapshot symlinks are deliberately absent from this public text packet. This packet is source-readable; it does not claim a standalone all-pilot test without those dependencies.

Implementation owner: ordinary GLM session `1f23ed1c-08e5-4996-bc85-8d595cd59318`. Different initial reviewer: ordinary GLM session `a38f2965-e4a2-435c-a7fd-153b9cb0a49d`. Both use `glm_sdk -> workbuddy/glm-5.3-flash` with provider-default effort; `route_receipts.json` contains whitelisted request-header excerpts only. The Sol coordinator owns dispatch, snapshot/publication and scope reconciliation, not the underlying B01 implementation.
