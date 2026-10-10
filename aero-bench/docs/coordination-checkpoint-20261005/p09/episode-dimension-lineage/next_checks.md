# P09 episode_dimension_lineage - next bounded checks

Status: provisional extraction, not scientific acceptance. No hashing, no payload reads, no re-audit.

1. Consumer entrances (bounded): for each of the 8 dimensions confirm which adopted pipeline stage opens the cited log paths for a given episode. Missing: consumer-side access logs/manifests. Estimate: reading consumer configs only (KB-MB), no bank scan.
2. Adopted-version confirmation: zero adopted replacements confirmed (change table NO_CANDIDATE_ADOPTED 204/210; 6 L6-2 PLAN_ADOPTED_PENDING_COMBINED_RELEASE_FREEZE, release not materialized). Missing: a coordinator decision record mapping candidate -> adopted per episode. Estimate: decision metadata only.
3. Energy/thermal + localization/navigation + facility/task state: per-episode observation_family row presence (domain_state_observations.jsonl row scan, few MB/episode). Estimate: 210 x ~1-4 MB text reads if required; summary absent for these families, hence listed rather than executed.
4. v14 overlay: confirm per-episode capture_filtered_updates/render_host_config.json actually enters the next UE run (36 technical-ready; adoption undecided). Missing: UE-side acceptance record. v14 event_realization empty = NOT_RECOMPUTED (not zero).
5. Actual UE capture receipt: bind same run/pose/weather/clock per frame; 181 planned capture times (capture_window.json) do not prove 181 successful frames; planned_frames_36_episode_grid 6516 is planned-only until per-frame receipts exist.
6. L4-9 zero-event applicability: contract expects vehicle_intersection_conflict chain; event layer empty for 6 episodes. Check acceptance records only.
7. Byte/content equivalence of captured truth vs boundary: remains unresolved; closure requires capture-time per-artifact receipts, not scans.
8. L/P definition split: q-derived (p09.LP.q-derived/v1) vs mature TTL cohort (p09.LP.mature-ttl-cohort/v1) must never be merged; count actual available_time<=cutoff; no timely RX -> L undefined (never zero); incomplete compute tasks stay pending/right-censored without hindsight outcomes. Unknown: which historical rows satisfy each definition.
9. transported_q TRUE future + TRAIN-frozen Gaussian noise: ORACLE_ONLY; verify no consumer treats it as historical prediction input.
10. Split discipline: generalization claims require the ENTIRE base scenario family (39 base families behind 70 scenario_ids) held out across ALL seeds; per-episode or per-version splits are leakage.

Source manifest sizes (from intake, not re-measured): parent_complete_210/manifest.json 103,432 B; v14 assembly_manifest.json 231,123 B; v14 source_manifest.json 182,279 B; v14 existing_sensor_dependency_index.json 407,276 B; batch_receipt.json 157,202 B; v14 projection_receipt.json ~5.8 KB x 36 (parsed for file lists only). If any summary is absent, scanning that manifest is text-only and bounded (<0.5 MB each); no payload archives.
