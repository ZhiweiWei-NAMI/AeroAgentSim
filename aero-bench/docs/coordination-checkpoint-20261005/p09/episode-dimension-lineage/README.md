# design/p09/episode_dimension_lineage

Ownership: P09 lineage extraction leaf (author session continuation). Parent owns native research and cross-review.

Contents: episode_identity_210.csv, dimension_lineage_1680.csv (210 x 8 dimensions with source_family/split_group/available_time/same_run_capture_binding supplement columns), source_version_catalog.json, existing_evidence_index.json, coverage_summary.json, next_checks.md, extract_lineage.py (this generator), author_checkpoint.json, author_result.json.

Rules honored: read-only originals (original 210, v14, ARM, evidence); named manifests only; path-existence checks only; no payload archives or RGB/LiDAR; no hashing (known version labels only); no simulation/training/field statistics; no delegation.

Consumer tiers (P09 parent directive): configured_pointer / actual_run_consumption / actual_capture_binding are DISTINCT; every current_consumer cell carries a [tier=...] token; configured pointers, path existence and global plans are never promoted into actual-consumer-confirmed. Persisted UE example config points to aw_data/rebuild_l1_1_radius10_20260918/... — pointer only, not proven actual run/capture consumption.

Supplement labels carried: (1) v14 package label vs per-dimension producer_revision (mapped from projection receipts; 36x8 inherited package never proves all eight producers changed); (2) model-visible vs archive-only view (full event_script/scene_setup, future fault/weather schedules, terminal/hidden control, semantic episode names stay archive-only unless known at cutoff; known-plan experiments carry separate labels); (3) transported_q TRUE future + TRAIN-frozen Gaussian noise = ORACLE_ONLY, never historical input; (4) split_group = base scenario family (scenario_id minus _vN) shared by ORIGINAL scenario+seed and all versions/overlays/load branches/windows — generalization claims need the entire family across all seeds held out; (5) distinct L/P definition ids (p09.LP.q-derived/v1 vs p09.LP.mature-ttl-cohort/v1); count actual available_time<=cutoff; no timely RX -> L undefined, never zero; incomplete compute tasks stay pending/right-censored; (6) actual capture must bind same run/pose/weather/clock; 181 planned capture times do not prove 181 successful frames; NOT_RECOMPUTED is not zero events.

Desktop-owned files in this directory (intake_observations.json, route_receipt.json, coordinator_public.txt, assignment.txt) are not written by this leaf.
