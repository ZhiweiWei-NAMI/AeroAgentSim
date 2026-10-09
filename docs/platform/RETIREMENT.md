# AeroBench retirement gates

Q4, 2026-10-08: **do not delete `aero-bench/` from main yet.** Runtime/build
independence for the tested platform path is demonstrated; full capability and
asset disposition is incomplete. Q4 changes no existing historical research measurements, native
engine semantics or owner's supported scope. The new source paths are explicit
configuration; the packaged map is a real, labelled historical selection.

## Checklist before deletion

| Gate | Current status | Evidence / action still required |
| --- | --- | --- |
| No runtime/build/default path into AeroBench or the old checkout | Passed for inspected tree | `authoring/inputs.py` packages OSM and requires configured AeroGraph; scenario source paths accept explicit environment references; asset builders use public upstreams. Tracked-file audit retained under Q4 scratch (312 matches before final documentation updates; no src/tools/scenario dependency match). Historical/wire-schema/provenance mentions remain intentionally. |
| Small input bytes, licence and attribution shipped with wheel | Passed for Studio map | Real Wujiaochang selection plus provenance/ODbL notice included by package-data; non-editable installed wheel used by isolated REST authoring. No synthetic map substituted. See [ASSETS.md](../../ASSETS.md). |
| Large required assets have independent public-source recipe | Partial / decision needed | HDRI/car direct fetch, BSD X500 GLB build and OSM ENU map build passed. Exact historical textured Huangpu pack producer/runtime/patch/config is absent here; choose D-assets in [CAPABILITIES.md](CAPABILITIES.md). Mesh-pack consumption is not production evidence. |
| Every distributed third-party input has rights and required notices/source availability | Partial / decision needed | Pinned model/style notices and verified Draco/Basis Apache notices retained. Existing residential JPEG and app-icon ownership lack licence provenance; the existing Beijing traffic sample lacks complete original source/route-generation inputs. The complete Wujiaochang fixture source is now packaged with ODbL metadata. Obtain rights/source archive or explicitly exclude/rebuild them. Do not copy undecided Holybro/Unity assets. |
| Git-independent clean install/build/run/author/replay with old projects unavailable | Passed for Q4 scope | Git archive of working contents, no `.git` or shared node_modules; fresh Python 3.11 venv, different HOME, `PYTHONNOUSERSITE=1`, bubblewrap hides old/main/platform/kernel/worktree roots. Configured AeroGraph is read-only and visible for source authoring; snapshot packs and WAL replay are independent of it. |
| Native backend Dockerfiles don't consume old private images/tree | Passed, recorded rebuild evidence | All three Dockerfiles use public digest-pinned bases. `containers/{px4-gazebo,sumo,ns3}/standalone/build.json` and verification records exist. Q4 inspected these records and ran host contracts; did not run/rebuild Docker. Release owner should retain those source locks/artifacts. |
| Every capability row has existing implementation/test evidence and an explicit remaining scope | Reconciled, disposition open | All named current implementation/test paths checked; missing p7a measurement citation removed. Studio subset upgraded only after REST/browser tests. Each remaining `pending` scope has a retirement-blocking column and owner options; no full port is inferred from file presence. |
| Scope decisions in CAPABILITIES recorded by owner | Decision needed | D-deploy/native/studio/assets/convert/packs/viewer/legacy options are explicit. Choose what must be delivered before deletion and what is retained independently or archived. Existing deliberate exclusions remain scoped; Q4 adds no silent drops. |
| Useful historical runs, data, conversion inputs and provenance survive deletion | Pending; blocks deletion | Inventory and archive needed for old public replay/suites/validation datasets and any still-missing source data underlying OSM-produced assets. Preserve originals with read-only import/provenance where required; don't rewrite old logs as native kernel WAL. No large binaries/credentials copied by Q4. |
| Engineering gates pass on changed code | Passed after correcting one test source path | Ruff check + format check and strict mypy clean. Initial broad suite had one failure/375 passes; corrected source-path test and all authoring/shared-fixture tests then passed (48). Live LLM passed once; Docker tests deselected. Exact results below. |
| Full City Studio/world alignment, sensor/calibration, native cancel/contact and viewer acceptance match accepted scope | Pending; blocks full parity | Partial OSM preview/drafts/run/replay are demonstrated. No genuine imagery, common physical scene compiler, distributed deployment, old-format conversion, matched-city superiority or hardware frame-budget claim was tested in Q4. Apply the owner's scope decisions. |
| Main-branch removal reviewed only after above blockers close | Not authorized / not performed | Orchestrator owns review/commit/removal. Q4 issued no commit/branch/reset/checkout and changed no protected source tree. |

## Reproduction and exact Q4 results

All 180 named capability implementation/test paths exist. Raw logs, large
builds/runs, screenshots and script artifacts are under
`/tmp/aas-q/q4/`; none are added to Git. Paths below are that job's scratch.
AeroGraph configuration points to the actual read-only source, never demo data.
The kernel was copied from tracked source into scratch before initial installation
so setuptools could not write to the protected sibling checkout. Final platform
archive refresh was separated from kernel copying: a later, concurrently changed
kernel HEAD contained an absolute test symlink, which tar's data filter rejected.
The installed non-editable kernel and all executed verification remained unchanged;
no absolute link was followed. The platform archive itself had already succeeded.

GLM proof: overlapping sessions `session-d40aab96-8d42-44ba-ae4a-f04f3dd5e2a4`
(assets, exit 0) and `session-e502bbe5-159e-4fc0-8393-ea3dd2b77047`
(capability observations; interrupted with exit 130 after repeated exploration)
used `workbuddy/glm-5.3-flash`, 131072 output budget and no effort parameter.
The bounded capability summary completed with exit 0 in
`session-c5ef87db-91c8-4279-a0e6-a41dab181f54`. Both reports were reviewed;
temporary live-test metrics and stale source notes were corrected during
integration. Reports, original audit stream and session proof are under
`/tmp/aas-q/q4/glm/` and `glm-proof.json`; configuration/credentials are excluded.
These static audits do not substitute for the executed gates below.

The working-tree archive used a scratch index **and scratch object directory**,
with the original object directory read-only as an alternate. The real Git index
was not staged. `git read-tree HEAD`, `git add -u` plus the explicitly named new
files, `git write-tree`, and `git archive <tree>` were run with those scratch
variables. The tar was extracted at `clean/`; `.git`, ignored outputs, `.glm` and
shared `frontend/node_modules` were not included.

Every isolated command used this wrapper (the full executable is `isolate`):

```bash
bwrap --die-with-parent --ro-bind / / --dev /dev --proc /proc \
  --bind /tmp/aas-q/q4 /tmp/aas-q/q4 \
  --tmpfs /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim \
  --tmpfs /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform \
  --tmpfs /mnt/data2/weizhiwei/aeroagentsim/aerokernel \
  --tmpfs /mnt/data2/weizhiwei/aeroagentsim/wt-q4 \
  --setenv HOME /tmp/aas-q/q4/home --setenv PYTHONNOUSERSITE 1 \
  --setenv TMPDIR /tmp/aas-q/q4/tmp --unsetenv PYTHONPATH \
  --setenv AEROAGENTSIM_AEROGRAPH_ROOT /mnt/data2/weizhiwei/AeroGraph \
  --chdir /tmp/aas-q/q4/clean -- <command>
```

The wrapper also appends `127.0.0.1,localhost` to `NO_PROXY`/`no_proxy` for local
HTTP. `test ! -e` checks inside it confirmed `.git` and all hidden source paths
were absent. Imports resolved to the fresh venv's installed platform/kernel,
including its packaged OSM; no user-site/editable platform installation was used.
Initial harness attempts missing `/dev` or writable TMPDIR were corrected;
those failures are preserved, not counted as passes.

| Command (inside wrapper unless indicated) | Actual result |
| --- | --- |
| Mandated workspace Python `-m venv /tmp/aas-q/q4/venv`; `venv/bin/pip install ../aerokernel '.[server,dev]'` in clean copy | Passed, non-editable platform/kernel installation. Build metadata stayed in scratch copies. |
| Final `venv/bin/pip install --no-deps --force-reinstall .` after source archival | Passed; installed small extract and full 979,135-byte ODbL source verified by digest and original snapshot timestamp. |
| `cd frontend; npm ci --offline --cache /home/weizhiwei/.npm --no-audit --no-fund` | Failed `ENOTCACHED`, missing `ws-8.22.0` tarball; no dependency change. |
| `cd frontend; npm ci --prefer-offline --cache /tmp/aas-q/q4/npm-cache --no-audit --no-fund` | Passed, 407 packages; existing cache copied to writable scratch and missing locked bytes fetched publicly. |
| `cd frontend; npm run build` | Passed production Vite build. |
| `cd frontend; npm run typecheck` | Passed. |
| `cd frontend; npm test` with writable scratch TMPDIR | 16 files, 68 tests passed. First attempt lacked writable TMPDIR and failed before collecting tests; corrected environment rerun passed. |
| `venv/bin/aeroagentsim run scenarios/p1-slice.yaml --out /tmp/aas-q/q4/runs` | Completed 22 s simulation in 8.572 s wall time; RTF 2.567. |
| `venv/bin/aeroagentsim run scenarios/packs/logistics-small.yaml --out /tmp/aas-q/q4/runs` | Completed 180 s simulation in 36.825 s wall time. |
| `venv/bin/aeroagentsim run scenarios/packs/inspection-small.yaml --out /tmp/aas-q/q4/runs` | Completed 20 s simulation in 0.939 s wall time. |
| With `AEROAGENTSIM_STUDIO_ROOT=/tmp/aas-q/q4/studio`: `venv/bin/aeroagentsim serve --out /tmp/aas-q/q4/server-runs --scenario-root /tmp/aas-q/q4/clean --frontend frontend/dist --port 8044` | Started; HTTP catalog/create/region/place/validate/export/run/header/commits/frozen-GeoJSON succeeded. Packaged source yielded three building features and one road; authored run completed, 85 commits. Temporary service stopped after checks. |
| `venv/bin/python /tmp/aas-q/q4/studio-smoke.py` | Passed. First client attempt used proxy for localhost (502); corrected loopback bypass. Then corrected verifier's GeoJSON MIME handling (`application/geo+json`) and reran successfully. No service fallback was added. |
| `cd frontend; npm exec playwright test -- --config src/studio/playwright.p7b.config.ts`, PATH includes fresh venv | First attempt had no browser under fresh HOME. With explicit `PLAYWRIGHT_CHROMIUM_EXECUTABLE` pointing to existing read-only cached Chromium outside the hidden trees: 1 test passed in 46.0 s. Real UI selected map area, authored entities/facility, validated, ran and opened recorded replay; screenshots in scratch clean copy. |
| `venv/bin/aeroagentsim replay <each of the four produced run directories>` | Passed: kinematic cut 1645 / 22 s; logistics 6417 / 180 s; inspection 678 / 20 s; Studio 85 / 2 s. All `incomplete:false`. |
| `venv/bin/python tools/sync_assets.py --models car --out /tmp/aas-q/q4/public-assets --work-dir /tmp/aas-q/q4/asset-work` | Passed: CC0 notice, pinned public HDRI/car glTF/bin; 4 inventory entries. |
| `venv/bin/python tools/sync_assets.py --models x500 --optimize none --out /tmp/aas-q/q4/x500-assets --work-dir /tmp/aas-q/q4/asset-work` | Passed: pinned public SDF/meshes, both BSD notices, actual converted GLB; 5 inventory entries. Meshopt/Draco optimization not run. Converter installs stayed in scratch. |
| `venv/bin/python tools/build_map.py --osm src/aeroagentsim/authoring/assets/wujiaochang.osm.xml --out /tmp/aas-q/q4/map --alt-m 0 --level-height-m 3.5 --public-url /assets/city/city.geojson` | Passed, 4 real features with missing-height diagnostic preserved. |
| `tools/build_map.py --fetch-bounds 121.5024 31.3002 121.5035 31.3009 --out /tmp/aas-q/q4/fetched-map --alt-m 0 --level-height-m 3.5 --public-url /assets/fetched/city.geojson` | Initial Overpass POST returned 406; script now uses explicit bounded public OSM map API GET. Actual OSM fetch/compile passed, 8 features, source XML/hash/attribution and missing-height diagnostics retained. This is a fresh upstream selection, not the packaged historical snapshot. |

Workspace Python checks used
`PYTHONPATH=src MYPYPATH=../aerokernel /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python`.
All source/Studio tests also used explicit `AEROAGENTSIM_AEROGRAPH_ROOT`.

| Exact gate | Result |
| --- | --- |
| `python -m pytest -q -p no:cacheprovider -m "not docker" tests/platform tests/adapters tests/agents tests/packs tests/authoring tests/integrations/aerograph` | Initial 375 passed, 1 failed, 4 deselected, 637.25 s. Failure was a test's direct `Path("${...}")` read; fixed to use the same explicit source resolver as execution. Live LLM passed; its new metrics saved only in scratch and historical tracked metrics restored. |
| `python -m pytest -q -p no:cacheprovider -m "not docker" tests/platform/test_native_threshold.py tests/platform/test_sample_missing.py tests/authoring` after fix | 48 passed in 84.26 s. All changed authoring/source-resolution regressions and the previously failing differential test pass. No unresolved test failure remains; a second full live-model run was not performed. |
| `python -m ruff check <all 16 touched Python files>` and `python -m ruff format --check <same files>` | Clean. File list in scratch `python-files.txt`; exact expanded command/result retained. |
| `python -m mypy --strict <same 16 files>` | Success, no issues in 16 source files. No ignores added. |
| Capability implementation/test path reconciliation | All named current paths exist; historical missing p7a measurement citation removed. Q4 evidence is distinguished from inherited recorded native/legacy evidence. |

Final archive and wheel reinstall include the complete Wujiaochang source; its
installed SHA-256 and timestamp were checked. Production build/typecheck were
repeated after adding distributed decoder notices and still passed.

No Docker workload/build, GPU benchmark, fresh native `netconvert` conversion,
mesh compression, old-format importer, sensor calibration, or protected-tree
modification was performed. These are limits on evidence, not synthesized successes.
