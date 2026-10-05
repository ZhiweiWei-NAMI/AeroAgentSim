# AERO-BENCH source and configuration export

This folder is a **curated first-party snapshot of AERO-BENCH** — its Python
benchmark runtime, Viewer source, contracts, tests, container build recipes,
architecture/validation docs, and the P02 parcel host-mount example — placed in
the `aero-bench/` monorepo folder of **ZhiweiWei-NAMI/AeroAgentSim**.

It is a **copy, not a git merge**: it carries **no AERO_BENCH commit history**.
The exact source identity and the complete file/byte list are recorded in
[`EXPORT_MANIFEST.json`](EXPORT_MANIFEST.json).

> **Snapshot status.** Everything in this folder is a point-in-time snapshot of
> a private working repository. It is not a supported release, it implies no
> support commitment, and it does not represent the current state of any
> upstream project.

## What is included

| Area | Path | Contents |
| --- | --- | --- |
| Benchmark runtime | `aero_bench/` | Python package: config/resolver, gateway, runner, executors (Docker reference + deferred Kubernetes contract), providers' contracts, verifier, trace, tasks |
| Tests | `tests/` | Focused pytest suites for runtime, executor, providers, tasks, frontend-facing contracts |
| Viewer source | `frontend/` | TypeScript + Three.js viewer (`src/`), build/validation scripts, shared helpers, e2e specs and public-trace test fixtures, package/TS/Vite/Vitest configs |
| Contracts | `schemas/generated/` | Generated JSON Schemas + Control API OpenAPI document |
| Container recipes | `containers/` | Dockerfiles, entrypoints, selfchecks, requirement locks for harness, PX4/Gazebo, ns-3, SUMO, business, verifier and world-scene workloads |
| Tools | `tools/` | Suite/world builders, stack launcher, validators, audit and packaging scripts |
| Docs | `docs/` | Architecture, validation, runtime and performance-model documents |
| Host example | `host/` | P02 isolated BENCH host-mount example (Apache-2.0, attributed reuse) with demo screenshots |
| Root | `README.md`, `AGENTS.md`, `pyproject.toml` | Upstream README, repository rules, Python packaging |

The upstream `README.md` (kept verbatim at [`README.md`](README.md)) documents
the formal architecture, executor profiles, run instructions and scope
boundaries. `docs/ARCHITECTURE.md` and `docs/VALIDATION.md` are the deeper
references.

## What is excluded, and why

Recorded in full in `EXPORT_MANIFEST.json` (`exclusions`). Headlines:

- `frontend/public/` models, building renders and OSM packs, `frontend/design/`
  renders, `frontend/assets/incoming/` inventories — raw datasets, maps and
  model assets **without known public redistribution rights** (the project's
  own asset ledger records `public_redistribution_authorized: false`).
- `releases/` — resolved-run configs, verifier-private truth, sealed evidence
  and image locks; not first-party source/config, and publishing them would
  misrepresent the private run as publicly reproducible.
- `validation/` run evidence, logs, reports, screenshots, checkpoints,
  container working trees — logs, evidence and datasets, excluded by policy.
- Session/workflow records (handoff, coordination, DSH/WorkBuddy session docs)
  — session-specific, reference local workspace paths.
- `.claude/`, `.agents/`, `.codex/`, `_receipts/`, `_tickets/`, archives,
  caches, `node_modules/`, build outputs — assistant/session installation and
  build/cache outputs.

Files that needed edits for publication are listed under `sanitizations` in
the manifest. The list describes removed private configuration by category
without repeating its values. Links to omitted documents are identified.

## Licensing

- The AERO_BENCH material in this folder is published **as-is by its author,
  who expressly authorized this public export**. No license file is invented
  for it and no third-party rights are claimed. Third-party dependencies it
  builds on (Python packages, npm packages, upstream simulation projects)
  keep their own licenses and are not redistributed here.
- `host/` is different: the P02 host-mount example **is** Apache-2.0.
  `host/LICENSE` is the unmodified license of the P02 parcel prototype checkout
  it reuses (`validation/p02-parcel-host/source @
  15f473a4dc0ed4f80e3000b0acef6e875c617393`), and every reused module carries
  per-file attribution headers. Existing public prototype sources from PR9/PR10
  keep their original copyright and licenses.

## Environment prerequisites

- **Python ≥ 3.10** with the three runtime dependencies declared in
  `pyproject.toml` (`jsonschema`, `pydantic==2.11.3`, `PyYAML`); `pytest` for
  the test suite.
- **Node.js ≥ 22** and npm for the Viewer (`frontend/package.json`).
- **Docker** with a user-accessible daemon — required only for the formal
  executor path and the container-integration tests. All container images are
  **built from source by the checked-in Dockerfiles**; no prebuilt image or
  registry artifact is distributed with this export, and there are no
  credentials, tokens or private endpoints anywhere in the tree.
- Source inspection needs no network access. Install dependencies before
  running tests; integration tests can require images or assets not included
  here. Container builds fetch the upstream sources declared in each Dockerfile.

## Quick start

### 1. Python runtime and tests

```bash
cd aero-bench
python -m pip install -e .          # runtime deps: jsonschema, pydantic, PyYAML
python -m pip install pytest
python -m pytest tests -x -q        # unit suites (no Docker needed)
```

The container-integration tests (marked `sumo_integration` and related)
require the digest-pinned images built from `containers/`; they skip or fail
with an explicit blocker when the images are absent — by design, never a
silent pass.

### 2. Viewer

```bash
cd aero-bench/frontend
npm install        # installs ajv, three, vite, vitest, playwright, typescript
npm test           # vitest suites
npm run build      # typecheck + production build
npm run dev        # dev server
```

Note: the full viewer experience renders the configured Shanghai OSM2World
city, whose OSM packs and model assets are **not** part of this export (see
exclusions). The viewer loads and validates sealed `aero-bench.public-trace/v3`
replays; the two small test fixtures under `frontend/e2e/fixtures/` demonstrate
the accepted trace shape. Asset-dependent features (model catalog, city
presentation) will report missing assets until a compatible asset pack is
supplied locally.

### 3. Integrated stack (authoring + control + viewer)

`tools/run_stack.py` starts all three services on free loopback ports, prints
readiness URLs and a credentials file path, and supports `--check` smoke
validation:

```bash
python tools/run_stack.py --help
```

Full usage, including sealed-run replay and Studio compilation modes, is in the
upstream [`README.md`](README.md) ("Run the integrated stack").

### 4. Container build recipes

Each `containers/<name>/Dockerfile` is a self-contained build recipe that
fetches and hash-checks its upstream sources at build time. They are provided
for transparency and local rebuilds; **no image builds or runs were performed
for this export**.

### 5. P02 parcel host-mount example (`host/`)

An isolated example that mounts the P02 parcel view inside BENCH integration
boundaries with one host-owned replay clock. The demo server uses Node ≥ 22
built-ins; tests additionally require the Viewer's installed jsdom dependency:

```bash
cd aero-bench/host
npm start           # http://localhost:4407 (loopback only)
npm test            # resolves jsdom from frontend/node_modules; install Viewer deps first
```

`host/README.md` documents the full host API, evidence rules and the
demo-vs-real-motion boundary. **The host clock is supplied by BENCH** (the host
replay cursor); the demo page's transport controls drive only the local view
cursor, never a simulation.

## Screenshots (demo/fixture only)

All images below are **authored demo/fixture screenshots of the P02 host-mount
example**. They are **not** real BENCH runtime evidence, **not** Atlas rule
evaluation evidence, and **not** a visual-acceptance claim of any kind. The
machine-readable capture record is
[`host/screenshots/capture.json`](host/screenshots/capture.json), which records
`realBenchMotion: false` with the reason.

| Screenshot | Shows |
| --- | --- |
| [host-demo-zh.png](host/screenshots/host-demo-zh.png) | Demo feed, tick 44, zh — demo parcel/custody/rule evidence (rule truth 真, parcel holding, custodian `uav.delivery.alpha`) |
| [host-demo-en.png](host/screenshots/host-demo-en.png) | Same demo state, en (True / Holding position) |

The upstream P02 source packages (PR9/PR10 prototype checkout and the
predicate-binding prototype) are **not** redistributed here; `host/` carries
the attribution and license requirements instead.

## Relationship to AERO_BENCH

- Upstream repository: private working repository **AERO_BENCH** (no public
  remote is declared in this export).
- Snapshot source: branch `repair/inspection-v1-r5`, commit
  `ea295073cdd310a31fa2e29317a3571bc716bb87` (2026-10-03), with the untracked
  working-tree provenance recorded in the manifest.
- This export was produced by copying selected first-party files only; the
  original repository, its history and all existing branches are untouched.

See [`EXPORT_MANIFEST.json`](EXPORT_MANIFEST.json) for the current file list,
per-file byte sizes, total export size and exclusion reasons. The source Git
commit identifies the snapshot; the uploaded branch retains its commit history.
