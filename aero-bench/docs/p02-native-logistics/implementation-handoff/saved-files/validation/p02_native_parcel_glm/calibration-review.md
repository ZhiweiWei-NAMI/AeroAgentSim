# P02 Native Parcel Calibration — Independent Read-Only Source Review

Scope: signed pose-reference calibration patch — `aero_bench/tasks/logistics/facility_presence.py`,
`aero_bench/tasks/logistics/physical_observations.py`, `tools/p02_extract_pose_calibration.py`,
`tests/tasks/test_logistics_signed_pose_reference.py`, `docs/p02-native-logistics/calibration-review/`.
Read-only review; no implementation altered, no Docker/flight executed, native-parcel runtime excluded.

## 1. Signed convention — PASS

Both validators changed from `_nonnegative` to `_finite` (`reference_height_finite`,
`declared_offset_finite`), and the redundant `profile.pose_reference_above_contact_m < 0`
rejection inside `FacilityPresenceAssessment` consistency was removed — the only behavioral
change, and the necessary one: a nested model root below the pad surface is now representable.
`_finite` still rejects `NaN`, `inf`, `bool` and `None` (test parametrizes all four) and the
field stays required with no default (`test_missing_calibration_has_no_half_height_default`),
so the half-body-height default was not introduced. Kernel semantics
(`sample.y == pad.y + pose_reference_above_contact_m ± vertical_tolerance_m`) are unchanged and
correctly admit a negative offset.

## 2. Source-model / SDF / provider / hash binding — PASS

Independently recomputed from `pinned-model-extract.json`: both foot collisions give identical
contact bottoms `0.24 − 0.2195 − 0.015/2 = +0.01299999999999999`, signed offset `−0.013 m`;
pad top `0.075 + 0.15/2 = 0.150 m`; expected root up `0.137 m`. Frame-chain assertions
(`x500_mono_cam` → `x500` → `x500_base` +0.24 m; base_link identity; zero-rotation feet) are
enforced in the extractor, and the expanded SDF digest is recorded. `manifest.json` digests
match both packet files; the bound external world SDF
(`689e06e3…`) and same-run receipt (`a3dc475e…`) were verified by digest against the
`/mnt/data2/...` source paths — no content inspected. The provider service SHA-256 read from
the pinned image matches the record. Applicability is explicitly scoped ("this pinned model and
root-pose convention, level settled contact; not a universal UAV offset").

## 3. Math vs. separate observed touchdown sample — PASS

The offset is derived purely from SDF geometry; the tick-300 / 150 s sample from accepted run
`9e07a000…` is used only as an independent check (record `role` states it is not used to fit
the calibration or authorize new dwell). Measured root up `0.13699987109567124` vs expected
`0.137` → residual `−1.289×10⁻⁷ m` (recomputed exactly). The sample state is consistent with a
resting aircraft (`landed=True, in_air=False, ground_contact=True, collision_contact=False`),
and the observed ENU yaw is `0.129°` — near level, so the level-contact assumption is not
violated by the check sample.

## 4. No tolerance relaxation — PASS

`git diff` over both source files shows no change to `vertical_tolerance_m`,
`horizontal_uncertainty_m`, `max_stationary_speed_m_s`, the check order/`_CHECK_CODES`,
the 3D speed gate, or the collision-contact refusal. The focused tests demonstrate the
negative point: zero calibration and a genuinely displaced root (y = 0.14, 3 mm off) both fail
against the unchanged 0.001 m tolerance. The record's `unchanged` list (presence tolerance,
speed/contact criteria, current-tick contiguous dwell, original sealed run) matches the diff.

## 5. Targeted tests — PASS

`tests/tasks/test_logistics_signed_pose_reference.py`: **7 passed** (1.27 s) — positive
calibration accepted, signed/zero/NaN/inf/bool/None matrix, no-half-height default,
closed-stage adapter keeps signed calibration, contact gate intact, wrong zero calibration
rejected with `contact_height_ok=False`.

## Result: scoped PASS

No defects found. The calibration (−0.013 m) is source-derived, model- and convention-specific,
digest-bound, and independently cross-checked without relaxing any criterion. Minor note, no
action required: the record stores the offset at full float precision
(`−0.01299999999999999`) while prose rounds to −0.013 m; the value consumed by code is the
record field, so the rounding is documentation-only.
