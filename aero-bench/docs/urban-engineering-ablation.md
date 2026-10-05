# Shanghai recovery: engineering baseline and ablations

The default is **engineering execution**, not formal benchmark acceptance:

- 120 logical seconds / 600 barriers; `--duration-seconds` changes the horizon.
- 200 ms logical steps / 4 ms physics steps, explicitly declared in the derived
  engineering SDF and checked by the existing runtime.
- Real PX4/Gazebo, ns-3 and SUMO, with both UAV policies and the groundstation rule.
- Identical Shanghai map, mission geometry, seeds and Provider graph across variants.
- A compact engineering mission at 20 m cruise height. UAV 02's engineering
  launch is `(300, -350)` ENU, outside the real building at the old `(320, -350)`
  point; World, PX4 spawn and Agent return goal use the same declared location.
- Existing sealing and indexed public replay; no generated viewer truth.

| Variant | Recovery communication | Recovery route |
|---|---|---|
| `baseline` | Real ns-3 alert/reply | A* from delivered constraints |
| `direct-recovery` | Same real alert/reply | Delivered goal directly; A* explicitly skipped |
| `no-recovery` | Heartbeats only | No incident alert, recovery hold or replanning |

`no-recovery` is an experimental control, not a successful recovery. A failed command,
exhausted retry budget or infeasible A* route is recorded as a policy failure; in
engineering mode observations and heartbeats continue to the declared horizon.
Malformed identities, unauthorized access and invalid recorded data remain errors.
At a shortened urban horizon (less than 600 seconds), a still-pending applied
command is recorded as `failed` with an explicit horizon-interruption reason and
its final physical-state proof before sealing. This is not a successful command or
recovery; proven physical completion still wins, and normal failures retain their
existing failure handling. The fixed 600-second formal path is unchanged.

## One entrypoint

Supply the real Shanghai scene/mesh, declared vertical-datum models, real SUMO
inputs, an immutable eight-workload image lock and Docker RunnerConfig. Engineering
accepts local Docker `sha256:<config-id>` identities; formal still requires registry
manifest references. Local image IDs are not presented as OCI manifest digests.

Local preparation (no registry push):

```bash
python tools/build_urban_engineering_images.py --output "$IMAGE_BUILD_DIR"
python tools/prepare_urban_engineering_traffic.py \
  --image "$SUMO_IMAGE_ID" --output "$SUMO_DIR"
```

The build writes `images-lock.json` with a source-snapshot revision. Captured source
and the digest-bound compressed build context are read-only; Docker receives the
original source permissions, before snapshot write protection. Change live source
and build into a fresh directory, never edit an older build snapshot. The traffic
preparer invokes real netconvert on the existing Shanghai road closure and authors
20 vehicle routes / 5 pedestrians; it does not create a road grid or recorded
positions. Sidewalk/crossing inference and traffic speeds are declared engineering
inputs. Existing flat vertical models may be supplied explicitly, without claiming
they are surveyed terrain or measured geoid data. A building-only source SDF has no
physical ground: engineering assembly must compile the explicitly supplied constant
terrain into a ground collision, not relabel a building as ground or disable contact
detection. Keep the original SDF and its building geometry unchanged; bind the derived
physics asset and record its source/model hashes and plane elevation/extent. The
`engineering_ground` input declaration is carried into experiment results and is
not a claim of measured Shanghai terrain.

Execute:

```bash
python tools/run_urban_recovery_ablation.py \
  --geoid "$GEOID" --terrain "$TERRAIN" --sumo-dir "$SUMO_DIR" \
  --images-lock "$IMAGES_LOCK" \
  --runner-config "$RUNNER_CONFIG" \
  --output "validation/urban-engineering-$(date -u +%Y%m%dT%H%M%SZ)"
```

Defaults for `--scene-dir` and `--mesh-pack` point to the existing Shanghai v3/v5
outputs. The image lock must cover the **current implementation**, not older
Inspection participant/verifier images. No images are silently built, relabeled
or substituted by the ablation command. Engineering reads the actual build revision
from a generated image lock, or accepts explicit `--runtime-source-revision` for
an externally prepared lock. The dirty worktree is not implicitly identified with
its old HEAD.

Use `--variant baseline` for one run, or `--variants baseline no-recovery` for a
subset. `--build-only` explicitly produces inputs without executing anything.
All outputs use a fresh directory; failed attempts and old sealed evidence are
never overwritten. The Runner's `output_root` is replaced per variant; the supplied
RunnerConfig itself is unchanged.

Start with `--variant baseline --duration-seconds 30` before the three 120-second
runs. `runtime_timeout_seconds` in RunnerConfig is a wall-clock budget, not the
logical simulation horizon; choose it from observed Gateway clock progress on the
actual machine. Readiness and verifier deadlines are separate. Healthy containers
alone do not establish clock advancement, and a timed-out attempt stays incomplete.

Outputs:

- `inputs/<variant>/`: independently resolved inputs and declared profile/strategy.
- `runs/<variant>/<run-id>/`: existing runtime seal and public replay publication.
- `<variant>.result.json`, `ablation-summary.json`, `RESULTS.md`: actual execution
  state, recorded duration, final UAV states, last recorded Agent summaries,
  sampled path lengths and network event counts. Missing data stays missing.

These are descriptive engineering results, **not formal mission scores**. A Runner
`invalid` verification in `executor_validation` can mean completed but unscored
execution; `error`, `blocked` and cancelled runs are not relabeled complete.

## Replay

Keep outputs under the repository (the default does). Start the existing viewer:

```bash
npm --prefix frontend run dev -- --host 127.0.0.1
```

Open replay view (`?view=replay`), refresh the workspace trace list and select the
variant's `runs/.../public/public-trace.json`. The same-origin workspace endpoint
serves public traces, replay manifests and digest-addressed assets, not arbitrary
input JSON/SDF files. Do **not** serve the entire experiment directory with a generic
HTTP file server: it also contains private runtime and verifier inputs.

Publication is reported as `published_not_browser_checked`. Actual browser loading
is a separate step; publishing a manifest does not claim visual acceptance.

With the viewer running, check one exact new recording (not an arbitrary old trace):

```bash
node tools/check_urban_engineering_replay.mjs \
  "validation/<experiment>/runs/<variant>/<run-id>/public/public-trace.json" \
  "validation/<experiment>/<variant>.browser-check.json" \
  "http://127.0.0.1:5197"
```

The report path must be new. This only checks sealed public loading, recorded
playback and seeking in Chromium; it does not score visuals or certify physics.

## Optional formal path

`tools/build_urban_recovery_demo.py --profile formal ...` retains the fixed
600-second / 3000-tick baseline and independent strict verifier. Engineering omits
unsupported native physics journal/plugin artifacts and does not require the
extra SUMO toolchain declaration or deep provenance audit. Formal scoring,
full native physics/toolchain audit and visual scoring are not prerequisites for
an engineering recording. Kubernetes remains deferred without a real cluster.

Current implementation smoke checks are not a real simulator run. Neither a
successful build nor this document establishes engineering execution completion,
formal benchmark success or a visual score.
