# Parcel clock and partial-stage restore repair

The original GLM author session `c17d3614-e676-4394-acc4-32391809e123` repaired the native-reviewed clock/frontier boundaries. The coordinator reproduced the failures in the actual Python package, then reran the 61 baseline/native regressions, eight independent boundary regressions and 13 additional author regressions: **82/82 passed**. Source hashes stayed identical during that run. These are synthetic package tests, not flight or physical custody evidence.

Same tick now requires exactly the same `sim_time_ns` for facility observations. Cross-dimension RX/future times are rejected. An unseen second facility at exactly the restored global SimulationTime may be absorbed; same-pad repeats and tick jumps remain rejected. The declared principal-to-carrier mapping and contiguous current-tick dwell remain unchanged. The old source and failed tests are retained separately.

Review rounds: contract and evidence questions; independent native counterexamples reproduced in the real package; corrected source retested against unchanged assertions. Separate GLM integration review and native re-review remain pending. This packet does not declare runtime acceptance.

Apply `source/` over an existing BENCH checkout for package reproduction. The exact command, hashes, stdout and JUnit report are in `tests/`. RPC/provider/projection/verifier drafts and the in-progress closed-stage completeness change are excluded. No new flight, OCI build or compilation was launched.

A source-docstring cleanup remains: `check_seek_forward` lists a same-tick later-nanosecond shape even though the implementation rejects it. The executable contract and tests permit only an unseen facility at the exact frontier time or the next tick with increasing simulation time. The original author has this documentation correction queued; it does not change the tested rejection behavior.
