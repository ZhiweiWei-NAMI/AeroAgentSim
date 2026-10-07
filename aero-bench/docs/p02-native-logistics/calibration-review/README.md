# P02 native model pose calibration

The explicit signed offset is **−0.013 m** for the pinned `x500_mono_cam`
model's Gazebo root pose. It is derived from SDF frames and collision geometry.
The accepted inspection run's touchdown sample checks the calculation separately;
it does not set the offset or authorize a later parcel transfer.

`pinned-model-extract.json` records the image-contained source hashes and the
installed sdformat expansion. The expanded frame chain is:

1. `__model__` → `_merged__x500__model__`: identity.
2. `_merged__x500__model__` → `_merged__x500_base__model__`: +0.24 m up.
3. That frame → `base_link`: identity.
4. Foot collisions 3 and 4: centre −0.2195 m, thickness 0.015 m, zero rotation.

The level contact plane is therefore `0.24 − 0.2195 − 0.015/2 = +0.013 m`
above the outer model root. The profile's signed root-above-contact value is
its negative. The existing launch pad top is 0.150 m ENU up, so the expected
root up is 0.137 m. At sealed tick 300 / 150 s, the observed root up is
0.13699987109567124 m; the residual is approximately −1.289×10⁻⁷ m.

The provider reads the configured model from Gazebo
`/world/{world}/dynamic_pose/info` `Pose_V`. It retains the model's ENU pose;
MAVSDK AMSL/AGL fields and `base_link` position are different references.
The service bytes read from the pinned image match the service hash recorded
for the accepted inspection run. `calibration.json` binds that source, image,
model/SDF hashes, exact world/pad source, and the separate observed sample.

This calibration applies to the named model and root-pose convention at level
settled contact. It does not establish a universal airframe offset, a tilted
three-dimensional contact proof, gripper dynamics, or a logistics run result.
Changing model, provider convention, or pad source requires a new explicit
calibration binding. The new logistics compilation must retain the record's
digest and these identities; that compilation is not yet issued.

The code change accepts required finite signed offsets in the existing presence
profile and physical observation declaration. It keeps all presence, contact,
speed and current-tick contiguous dwell criteria. The focused tests also show
that zero calibration and a genuinely displaced root fail an unchanged
0.001 m test tolerance. No tolerance was enlarged to fit touchdown.

Reproduce with the existing digest-pinned flight image, without starting Gazebo,
PX4 or a mission:

```bash
python tools/p02_extract_pose_calibration.py \
  --world-sdf <exact-compilation-world-SDF> \
  --observed-receipt <existing-same-run-performance-receipt> \
  --output <new-output-directory>
```

The output contains small source facts and the existing sample. Original model,
city assets and full trajectories remain at their source paths. The image-side
`gz_frame_id` warnings are retained in the extraction receipt; sdformat returned
zero and resolved the merge frames. `manifest.json` hashes the two JSON receipts.
