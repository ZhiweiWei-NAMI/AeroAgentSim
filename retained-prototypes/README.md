# Retained prototype snapshots

These complete, unchanged Git trees retain the four retired backup branches. They are recovery/reference snapshots, not active simulator integrations or tested releases. Each source commit is also a parent of the archive consolidation commit, preserving its full reachable history after the old branch reference is removed.

The pre-existing AirFogSim archive files are unchanged. Each directory below contains the complete source branch root, so some files appear in more than one snapshot.

- `cloud-prototypes/`: `backup/cloud-prototypes-20261005` at `080a2e211575920f3c20a7a7b23c1f40a73aef61`; source tree `e4f5a26585cda2f2166164da5b561b790735a228`.
- `observer-worldmodel/`: `backup/p01-observer-prototype-20261006` at `e73e0f1e9d6e9ad1543f4f2fdb4a648a10319b36`; source tree `fcace57c93d51ac5cdca12aadc4a24768c95cc70`.
- `p08-graph/`: `backup/p08-graph-20261005` at `7ca57707930fc45fdc1d48f32c6470beabf33bb8`; source tree `e0d8a830caa684ad60fc020f7da68babfcd42d58`.
- `p08-star-ui/`: `backup/p08-star-ui-code-20261005` at `4f9adf6e8b851d3a7b1086fba3d297f1620607a2`; source tree `2877cce244b2a9e3e17fe0efcc1a0bf7ffc2d09d`.

To recover a source checkout, use its recorded commit SHA with Git; its commit remains reachable from `archive/airfogsim`. Do not copy these historical roots wholesale over `main` or `low_altitude_sim`.
