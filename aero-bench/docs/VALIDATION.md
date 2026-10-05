# Local validation records — 2026-08-28 through 2026-09-01

Records are evidence only for the exact source revision, image identities, and
run they name. Historical component checks do not inherit a later verdict.

## Current bounded release verdict

The exact Docker Inspection v1 reference slice is **GO**. One real
`formal_benchmark` execution passed preflight, runtime, exact-nine sealing,
independent verification, enriched Public Trace projection, cleanup, and
production Viewer ingestion. The formal run completed on 2026-08-31; the final
exact-trace visual acceptance completed on 2026-09-01.

This is not a repository-wide or platform-wide GO. Kubernetes, broader World
Scene integration, SUMO/Airspace benchmark composition, and unexecuted task
families remain deferred or unclaimed. The formal trace publishes authoritative
PX4/Gazebo WGS84 state and trajectory samples; it does not assign invented
positions to logical ns-3 endpoint identifiers.

## 2026-08-31 through 2026-09-01 bounded Inspection v1 record

### Source and image lock

Benchmark-owned runtime source revision:

```text
c63f3280581755bb4d0201aa9a2f8e13be81ee4d
```

The release-lock builder inspected each exact local repository digest and
validated its image ID plus component, implementation-kind, source, revision,
and version labels. All five managed images share that revision:

- Harness:
  `127.0.0.1:5000/aero-bench/harness@sha256:e8cb5df6a73d577d97a18e297a4ac92ccaecf4c2e2740d5480c2542c593b64b1`
- PX4/Gazebo:
  `127.0.0.1:5000/aero-bench/px4-gazebo@sha256:75cca865935e7d10aa770203a13ff19158b8dbf42c3111a45c5b5d3751b0767a`
- ns-3:
  `127.0.0.1:5000/aero-bench/ns3@sha256:f1437fb87ed2c09de91878a0b15cfe412818605daf28704a3438131f445bdee7`
- Inspection Business:
  `127.0.0.1:5000/aero-bench/inspection-business@sha256:929b7996c397c28ad6da4e826ee2d9126a0734d587eb3d5cbd0c65dc972fda5a`
- Inspection Verifier:
  `127.0.0.1:5000/aero-bench/inspection-verifier@sha256:0152671df4532d529550f8f8b4aaa4fc24e48977e38bfa6dc06c78b94ca131b0`

The separately recorded external validation participant is:

```text
127.0.0.1:5000/participants/inspection-validation@
sha256:a9b0795773ea7d096bc89bdadc4f3db2883f32a4fcf2e7565859c7348bf8fc95
```

Its component identity is `participant.agent`; its policy-source revision is
`475d405f72a053506044a37a3f375837bf682d9d8801ce0416286aaa79ccce4e`.
The policy is not committed as benchmark code. The benchmark-owned boundary is
`AgentSpec + AgentContext + GatewayClient`; see `docs/AGENT_SUBMISSIONS.md`.

The machine-readable record is
`releases/inspection-v1/release-lock.json`. It reuses the Verifier image as the
executor volume-keeper and declares that auxiliary role explicitly.

### Formal command and runner result

Command:

```bash
set -o pipefail
python -m aero_bench.runner.cli \
  releases/inspection-v1/suite.yaml \
  releases/inspection-v1/runner.local.yaml \
  | tee /tmp/aero-inspection-v1-run-summary.json
```

Result:

- process exit: `0`
- run count: `1`
- execution-complete count: `1`
- passed count: `1`
- preflight: ready, zero blocker codes
- execution scope: `formal_benchmark`
- executor: `docker_reference`
- failure classes: empty
- run ID:
  `a10ec4315d9c0ecaf28418f0005fe2778d0dc828f03b985dc114e85b5a0d529e`
- runner-summary SHA-256:
  `bd7f022bdde6b571d7c2ba53c49dab8c5c9497a2ac463cc9db02bf5a4c967720`
- Suite SHA-256:
  `0cecb190fdd8acfd0b8c22a94305dbea9e72b556557b30fb96d770c4343df291`
- RunnerConfig SHA-256:
  `ec08531632a1751254b8c2868c1b2b34626d41cea61c72816269a5dc718189b5`

Real runtime behavior included the PX4/Gazebo paused-barrier lifecycle, staged
MAVSDK hold, real `COMMAND_ACK`, real camera observation, participant image
pixel analysis, ns-3 report transmission and delivery, Business state
transitions, and terminal two-phase artifact finalization. All Provider,
Harness, and participant containers exited successfully.

### Seal and exact-nine inventory

- Event-chain root:
  `2bf0e5050bc9f0de722dbd5e835026e3a07dcf6aa4f087660c2108b46ccb2ddd`
- Seal-manifest digest:
  `42be57cf473a619bc17663d38c68bd71f8b5805db1fd1a34ee997a86dc0674a0`

| Artifact | SHA-256 | Bytes |
|---|---|---:|
| `artifact.truth` | `0fa3caa0f313e03f0b0d2bd089933aba7490a33e5e484354e319e3d5fd8f5841` | 129 |
| `artifact.business` | `596f87838db3e0620b5a566195c50f232a72cfb7997f9dcd98955ca8c3c4bff1` | 5,222 |
| `artifact.observation` | `df2dd9fd7cfc35a635bfb0e5ee624a4e977244cf4b515d0f09ea58e9d2d996e9` | 21,973 |
| `artifact.trajectory` | `4ed47efe60784d3e05b43f97a5c150b5f442ea7b88a7295801f23121e5844b6e` | 3,308 |
| `artifact.event-log` | `e1a4ec4e8b70d0ca22336009b4804ca1839e72af85438a9af24870d4b3f53ded` | 92,322 |
| `artifact.bounds` | `912d9a7e495e75bf7d1fb4a601b35cadb1a8586fd8f743c422949c4bda0a17ad` | 589 |
| `artifact.delivery` | `0ea2b2c733a1a4003ccc2f486847b626951bee2282e28ec6188f1ac0b3e67d25` | 364 |
| `artifact.detection` | `74ceb38631acda9fbdff389fea761bd5b92bc4abbddd342ff14e0f33450f16b8` | 244 |
| `artifact.report` | `201ecf225db1fa203e627e85db482a28081597f149a8f76f354e431f42a2bbcd` | 499 |

The seal contains no tenth runtime artifact. World Scene is outside this exact
case; its inclusion would require a different declared inventory.

### Independent Verifier and Public Trace

Verifier result:

- status: `passed`
- goal: `inspection.success`
- metric: `inspection.success_rate = 1.0 ratio`
- coverage complete: `true`
- verification output-manifest digest:
  `9a1c02427a3f393014c8c387b6550847096477887cab073b96ff9ee205d1d6da`

Public projection result:

- schema: `aero-bench.public-trace/v2`
- projector: `aero-bench.public-projector/v1`
- SHA-256:
  `79e74c42a04ba709b60e567cad52f4235fc9084a612fb79da7bae864a7d1837c`
- authoritative content: two WGS84 entities, one trajectory with three samples,
  six Business/network events, six PX4 command receipts, one ns-3 delivery link,
  and terminal `completed / ready / delivered / 1.0` status
- verifier metric evidence selectors: `bounds`, `business`, `deliveries`,
  `detections`, `reports`, and `trajectory`
- every selector is bound to the corresponding sealed public artifact digest

Earlier real runs exposed and drove corrections to public selector coverage,
post-seal reference resolution, logical network endpoint semantics,
sub-barrier event time semantics, published command-receipt suffixes, and
terminal Business status. They are not used as release evidence; the run above
is the sole formal release result.

### Docker cleanup

After the runner returned, all three queries with label filter
`org.aero-bench.owned=true` were empty:

- containers: `[]`
- networks: `[]`
- volumes: `[]`

The preserved machine-readable result is
`releases/inspection-v1/validation/docker-residue-check.json`.

### Production Viewer

`npm --prefix frontend run typecheck`, `npm --prefix frontend run build`, and
`npm --prefix frontend test` passed on 2026-09-01; Vitest reported 109 passing
tests. `npm --prefix frontend run test:e2e` reported four passing production
preview tests. Vite emitted only the known large Cesium chunk warning.

An actual headless Chromium session opened the production preview, selected the
exact emitted Public Trace, rendered one Cesium canvas, and required these
values and behaviors:

- The formal-benchmark label and the integrity-verified status label
- `uav.1` and `target.1` at authoritative WGS84 positions
- terminal `completed`, `ready`, `delivered`, and `100.0%`
- one trajectory with three recorded samples
- `link.message.report.1` with `54.23 ms` latency and `499 B` payload
- logical endpoints `agent.uplink` and `business.receiver` as non-clickable
  labels, without invented geospatial positions
- `inspection.success_rate` and sealed `artifact.bounds`,
  `artifact.detection`, and `artifact.report` references

The browser reported zero console errors, page errors, and failed requests.
Screenshot SHA-256:
`9688fc974f242dd2da04fa971c208795f6e9b73901e6b6b8361eb4cf8bd6412b`.
The screenshot and machine-readable browser record are in
`releases/inspection-v1/validation/`.

A broad post-flow Python pytest/ruff sweep was intentionally not run at this
stage. The user requested that broad testing be deferred until after the real
end-to-end flow. The focused frontend checks above and historical records below
remain scoped honestly; historical checks are not relabelled as current formal
evidence.

## Historical records

The `a74cd691`, `32cd2bf6`, `4a56585`, and `d973f3c` sections below are retained
for audit history. Their then-current NO-GO statements describe those named
revisions and do not override the bounded 2026-08-31 formal run and 2026-09-01
Viewer acceptance above.

## 2026-08-28 record (`a74cd6913c99f79bddc5142fb3aa54282ef8529a` unless stated otherwise)

### Host boundary

- User: `uid=1007(weizhiwei)`, group `docker` present; no root action was used.
- Python: `3.10.14`.
- Docker: client `20.10.12`, client API `1.41`, server `26.0.0`.
- kubectl client: `v1.36.0`; `kubectl config current-context` returns `current-context is not set`.
- Kubernetes Namespace, RBAC, CNI policy enforcement, StorageClass/PVC, registry pull and GPU device plugin: not testable without a real context.

Source archives checked before implementation:

- `AERO_BENCH_HANDOFF_20260828.tar.gz`: 74,862 bytes; SHA-256 `3064d82b0a9b721c71a1229ddd200036e559a15e53fbf605b801507decaea181`.
- `AERO_BENCH_ARCHITECTURE_KIT_20260828.zip`: 1,415,937 bytes; SHA-256 `b4fae4f48f35683c316be566b91e35562bbbb3daa4184a6c6681540ac6b8b78a`.
- Both internal `MANIFEST.sha256` files passed `sha256sum -c`; the architecture kit was read as a design baseline and was not overlaid onto the real repository.

### Python tests and static checks (2026-08-28)

Commands:

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
ruff check .
ruff format --check .
python -m compileall -q aero_bench tests tools
```

Final local results for that historical commit:

- This historical run is retained for the `a74cd691` review scope only; it is
  not a current-release test result. The later historical main result is recorded
  in the 2026-08-30 section below.
- `ruff check`: passed.
- `ruff format --check`: 55 files already formatted.
- `compileall`: passed.
- Coverage includes pinned source-Suite replay, formal/mechanical scope separation, OCI identity labels, complete Provider-barrier evidence, resource ownership, full producer/root artifact enumeration, verifier staging failure, business completion, Provider-config-bound theory, non-vacuous PASS and public-trace consistency.

### Historical frontend record (`a74cd691`, 2026-08-28)

Commands:

```text
cd frontend
npm test -- --run
npm run typecheck
npm run build
npm audit --audit-level=low
```

Results:

- This historical frontend run is retained for the `a74cd691` review scope
  only. The later 2026-08-30 frontend results and unaccepted browser attempt are
  recorded below; the current release Viewer result is recorded above.

### Real Docker Reference Executor smoke (2026-08-28)

This was an actual local Docker execution using only local digest-pinned images:

```text
python -m tools.run_docker_reference_smoke
```

- Runtime image: `docker.m.daocloud.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc`.
- Verifier/volume-keeper image: `docker.m.daocloud.io/library/busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662`.
- Execution scope: `executor_validation`; it is mechanically valid and benchmark-ineligible.
- Run ID: `cdfb9298d81877d16e2137aa6e24702759efb5334d174725ab8feaa5f996ce5d`.
- Preflight: ready.
- Runtime artifacts sealed: 9.
- Runtime manifest digest: `a5df7a2cd73fc3cebc151a4dedca3b00b96d1c2bea5781253e5f097efb724557`.
- Replayed EventLedger root: `817e6ca4e84c4270163ec18ecd2445409fafb6815eb65eac8ed74529f9799167`.
- Verification artifacts sealed: 1.
- Verification manifest digest: `a4c0fa49da3dd8a20543521379a283f9038929d85bb1d0763c540608bfc3f820`.
- Host seal directory mode: `0700`; all sealed files: `0600`.
- Post-cleanup containers, volumes and networks with the run label: all empty.
- Fixture feasibility: mission minimum `13.0 s`, upload minimum `0.018000000000000002 s`, projected defect pixels `8.0`, Recall upper bound `1.0`, detection F1 upper bound `1.0`, success upper bound `1.0`.

The command builds its pinned `inspection.basic` fixture in a temporary directory, prints all reproducible identities/digests, validates full artifact inventories and zero labelled Docker residue, and removes the temporary artifacts. The fixture adapters are explicitly mechanical artifact writers rather than PX4/ns-3 names. It did not run PX4, Gazebo, MAVSDK, ns-3, SUMO or the production Inspection Verifier image, so it is not a benchmark-success result. Its `executor_validation` scope makes it invalid as benchmark evidence by construction, at any commit.

### Historical Sol review record (`a74cd691`, 2026-08-28)

The read-only reviewer was exactly `gpt-5.6-sol` with reasoning effort `max`.

- Round 1 reviewed `899170098addd3725589c87da332cc328189217c` and found four P1 and two P2 issues.
- Round 2 reviewed `869d15637aba51d057dcff553fcf1356ad43f81a`; the original six counterexamples were closed, and three narrower P1 issues were found.
- Round 3 reviewed `a74cd6913c99f79bddc5142fb3aa54282ef8529a`; every prior counterexample was rejected and no remaining P0, P1 or correctness-relevant P2 was found at that historical commit.

This historical record was later superseded by the 2026-08-30 backup review and
is not a current-HEAD verdict. The immutable review trail remains in
`docs/SOL_REVIEW.md`; the 2026-08-31 formal release was not reviewed by Sol.

### Kubernetes (2026-08-28)

`KubernetesExecutor.preflight` is deliberately fail-closed and always includes `kubernetes.execution.deferred`. It additionally rejects a fake `kubectl` that cannot return a valid client-version JSON document. No cluster submission was attempted.

The Materializer unit tests cover standard namespaced objects, non-root/read-only-rootfs/dropped-capability/seccomp settings, explicit resource requests/limits, Job active deadlines, role-restricted DNS, deny-by-default policies and private asset staging. The cluster-side trusted seal publisher is not implemented; the sealed-input PVC has no validated writer and therefore the Kubernetes verification graph is not runnable.

### External Provider availability (2026-08-28)

- PX4: missing.
- modern Gazebo (`gz sim`): missing. Gazebo Classic 11.15.1 exists but is not an accepted substitute.
- `mavsdk_server` and Python `mavsdk`: missing.
- ns-3 CLI/build identity: missing.
- (Historical 2026-08-28) SUMO 1.15.0, `sumo-gui`, Python TraCI and sumolib
  were present; no accepted pinned Provider image/commit/evidence had yet been
  recorded at that time.
- NVIDIA driver reports 6× GeForce RTX 3090, driver 535.183.01; no Kubernetes GPU boundary is available.

The 2026-08-29 record below supersedes the availability picture for
PX4/Gazebo/MAVSDK, ns-3 and SUMO: digest-pinned external-runtime images now
exist and their image-level selfchecks have run. At that record's commit, only
the ns-3 and SUMO images exposed formal Provider services; PX4/Gazebo remained
selfcheck-only. The current bounded release status is recorded at the top.

## 2026-08-29 component records (`32cd2bf6d5d05a2eb1d9474881c511399536ab27`)

The SUMO component result below was run at this commit. It is retained as
component evidence; the current mainline test count is recorded separately in
the 2026-08-30 section.

### Provider images and image-level selfchecks

Digest-pinned production-implementation images were built locally from the
pinned Dockerfiles in `containers/` (external identities recorded in
`containers/README.md`: PX4 SITL `381149fb…`, Gazebo Sim `8.11.0`, MAVSDK
`3.17.2`, ns-3 `3.48`, SUMO `1.27.1`). Their in-image selfchecks were run on
this host on 2026-08-29:

- `px4-gazebo`: paused Gazebo world, PX4 SITL plus the official
  `mavsdk_server`, exactly 2,500 physics steps of 4 ms, Gazebo verified at
  exactly 10 s, PX4 telemetry read through MAVSDK
  (`containers/px4-gazebo/selfcheck.py`).
- `ns3`: a real UDP payload delivered across a configured point-to-point
  device (the `aero-ns3-selfcheck` scratch program).
- `sumo`: generated road network with real TraCI vehicle motion; the stricter
  contract-level container validation is the next subsection.

The PX4/Gazebo result was a real external-engine image selfcheck only. At
`32cd2bf6`, there was no PX4 Provider registration, RPC service, or shared
readiness path; the formal PX4 Provider path was **BLOCKED**, and that selfcheck
could not produce a Provider-barrier ledger or benchmark evidence.

Boundary at that commit: these were image-level selfchecks of the containerized
external runtimes. No Harness/Provider-barrier run with those images had
produced a formal ledger, sealed artifacts, or a production Inspection Verifier
result. They were not benchmark evidence, and the benchmark remained NO-GO at
`32cd2bf6`.

### Strict SUMO/TraCI container validation — 2026-08-29

The ordinary pytest integration test is marked `sumo_integration` and skips
when Docker or its image is unavailable. A skip is not SUMO evidence. The
explicit entry point below is the only integration command that can report a
successful SUMO container validation; it requires a locally available
digest-pinned image and reports `BLOCKED` with a non-zero exit status when a
prerequisite is missing:

```text
AERO_BENCH_SUMO_IMAGE=localhost:5000/aero-bench/sumo@sha256:9ed8fb72901abe39c7bdbae725c11fccdf173737113c931dbb2c8c0450a0ca9b \
  PYTHONDONTWRITEBYTECODE=1 python tools/validate_sumo_formal.py
```

Observed result:

- `status=real-traci-rpc-ok`; Docker ran the digest-pinned SUMO 1.27.1
  container through `prepare`, `reset`, two exact `step_to` calls, `snapshot`
  and `shutdown` JSON-line RPC operations.
- Requested image: `localhost:5000/aero-bench/sumo@sha256:9ed8fb72901abe39c7bdbae725c11fccdf173737113c931dbb2c8c0450a0ca9b`.
- Docker image ID: `sha256:446b7a1a23db90ef7775a3e4c229dcb78159cc1049ca12a7328fce01d10b1cb7`.
- This proves the SUMO/TraCI provider container round-trip only; it is not a
  complete benchmark-success result.

## 2026-08-30 intermediate main record (`4a565851312d51ddaf6127e4d3bf9fc5e7ad8a2a`)

This component/container and frontend record predates the final trace fixes.
The final current-main Python result and Sol review are recorded below.

### Python suite and static checks

Commands:

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
ruff check .
ruff format --check .
```

Observed at that intermediate main:

- Python: `568 passed, 3 skipped in 179.41s`.
- `ruff check`: passed.
- `ruff format --check`: passed.

The skipped cases and passing module tests are not formal benchmark evidence.

### Viewer checks

Commands:

```text
cd frontend
npm test -- --run
npm run typecheck
npm run build
```

Observed results:

- Vitest: 11 files, 85 tests passed.
- TypeScript typecheck: passed.
- Production build: passed; the Cesium chunk is approximately 1.126 MB gzip
  and remains above the bundler warning threshold.
- A real Firefox/Playwright attempt was not accepted: system Firefox 136
  headless exited 11. There is no browser E2E PASS.

### Inspection Business provider container

The main image is
`localhost:5000/aero-bench/inspection-business@sha256:d715097560ab72755525b10c4d5b48701b2ede27d3f22b378e4c2bc3967491ae`,
with revision `480340361512925fe7571e311b15272fd5e35c62`. It is verified as a
non-root image with no exposed port and the shared Docker readiness probe.

The executor-equivalent selfcheck command is:

```text
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  localhost:5000/aero-bench/inspection-business@sha256:d715097560ab72755525b10c4d5b48701b2ede27d3f22b378e4c2bc3967491ae \
  selfcheck
```

It passed. The digest-pinned container lifecycle command is:

```text
AERO_BENCH_INSPECTION_BUSINESS_IMAGE=localhost:5000/aero-bench/inspection-business@sha256:d715097560ab72755525b10c4d5b48701b2ede27d3f22b378e4c2bc3967491ae \
AERO_BENCH_INSPECTION_BUSINESS_RUNTIME_IMAGE=localhost:5000/aero-bench/inspection-business@sha256:d715097560ab72755525b10c4d5b48701b2ede27d3f22b378e4c2bc3967491ae \
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider \
  tests/providers/test_inspection_business_container_integration.py
```

Observed result: `1 passed in 12.17s`. This is Business component/container
evidence, not a complete Provider-barrier or benchmark run.

### Formal benchmark gate at the intermediate main

At this intermediate main snapshot, the formal Docker benchmark remained
**NO-GO**. It had no valid Suite/RunnerConfig for a formal run, no world assets,
no Observation `observe` Provider, no production Agent image, no production
Inspection Verifier image, and no PX4 Provider. Existing NS3, SUMO,
WorldPackage, and Business results were component or container evidence only.
`kubernetes_cluster` remained deferred.

## 2026-08-30 final main record (`d973f3ca8bdf1c4e51afe1353821085f5ace201d`)

### Python suite and P1 fixes

The final full Python command was:

```text
PYTHONDONTWRITEBYTECODE=1 pytest -q -p no:cacheprovider
```

Observed result: `584 passed, 3 skipped in 183.79s`.

The final main includes these accepted P1 fixes:

- `26cebc8`: verifier output metrics reject non-finite values and non-JSON
  numeric representations.
- `bbc279b`: partial aborted provider lifecycles retain the evidence already
  observed instead of being projected as completed.
- `d973f3c`: each tick has one consistent `sim_time_ns` across provider
  receipts and trace records.

These checks and fixes are repository validation evidence only. They do not
create a formal benchmark result.

### Final Sol backup review

The final backup read-only review was exactly `gpt-5.6-sol` at reasoning effort
`max`, targeting `d973f3c`. It reported P0=0 and P1=0. Its repository/runtime
GO is limited to the audited Docker/Business/Trace/Verifier paths. Research and
benchmark truth remains **NO-GO** because the formal Provider, Agent,
Observation, production Inspection Verifier and PX4 chain is incomplete.

No earlier stalled Sol process is counted as a result. No secrets, raw session
text or hidden reasoning are retained.

### Process and residue check (2026-08-30)

No pytest, Vitest, Vite, zcode, Grok, dsh headless or Playwright process from this task remains. A pre-existing long-lived `dsh web` session for `the separate AERO_WORLD workspace` was not touched because it is outside this repository task.
