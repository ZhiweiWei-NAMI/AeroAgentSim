# Traffic accident: authored domain scenario

The integrated scene uses the shared behaviour runtime, real physical owners and explicit authored stub decisions. The default profile declares 1 Hz physics/publication and a 66,666,667 ns shared message lag. The original 15 Hz domain comparison remains a separate test profile. Actual headless Chromium renders committed poses with primitive geometry; this is simulation-camera output, not the licensed city viewer or native imagery.

```bash
python -m aeroagentsim.services.cli run scenarios/demos/traffic-accident/scenario.yaml \
  --out /tmp/aas-demo/runs
```

Set `AEROAGENTSIM_AEROGRAPH_ROOT=/path/to/AeroGraph` when running from source
against a full AeroGraph checkout; snapshot-only environments do not need it. The
capture renderer uses the Playwright Chromium installed via `npx playwright
install chromium` (or the executable given by `AEROAGENTSIM_CHROMIUM`); no
browser path is hard-coded in the scenario.

Default: 63 road vehicles, 8 UAVs and one nonspatial coordinator. The end-to-end
gate produces Bravo's incident PNG; it does not record the historical 72 views.
Medical Alpha's task is noninterruptible. Rules recheck current region/energy/task
eligibility and reserve one capture assignment; recorded model proposals alone
cannot stop Alpha, authorize an occupied bypass or complete a photograph.

## Import and inspect now

From the worktree root:

```bash
python tools/demos/import_traffic_accident.py /absolute/read-only/demo \
  /tmp/aas-demo/imported
```

The importer writes deterministic `inputs.json` and `city-manifest.json`; it never
imports the old Runtime. Four source files are hashed before extraction and checked
again before publication. Road offsets are packed in 7 m increments with 200 tries;
exhaustion reports conflicts. Counts above the explicit road demand need new routes.
Zero background counts are valid. Large assets are hashed/referenced, not copied.
No old absolute path survives in runtime inputs. The committed scenario is an
explicit frozen 60/6 configuration; changing imported counts requires editing its
entities/bindings/configuration before compiling a new run.

Road and bypass coordinates are `[east,north,up]=[X,-Z,Y]` in a local right-handed
frame with flat-road up=0. Arc lengths preserve every connector vertex. A route
wrap is a declared discontinuity, not an invented connector. UAV route inputs
retain the old medical waypoints and orbit radius/phase/altitude parameters.
Native/geodetic alignment and the full city licences remain unresolved. See root
[ASSETS.md](../../../ASSETS.md) and integration notes for licensed pipelines.

`prompts/manifest.json` names C's canonical graph symbols and source line citations.
`fixtures/decisions.json` selects authored scripted replies explicitly. There is no
automatic live-model fallback. `fixtures/legacy-trace.json` contains 18 small real
historical snapshots and the full source trajectory hash; it cannot reconstruct
old command receipts, missing clocks or capture causes.

## Console/API workflow

```bash
python -m aeroagentsim.services.cli serve --out /tmp/aas-demo/runs \
   --scenario-root . --frontend frontend/dist In Studio import `scenario.yaml`
   together with its pinned registry/behaviour/input closure; review proposed local
   overlays, active field owners, calm weather and the selected kinematic profile.
2. Validate the frozen package, Q6 dialect/roles/clocks, ownership, positive sampled
   feedback lag and installed capabilities. Select scripted decisions for the first
   no-LLM run. Native profiles show their required network/transform/service/model
   inputs and missing capabilities; configuration validation is not a native smoke.
3. Create a run via the existing console/API. The same run opens in Runs and
   AgentConsole. Inspect inactive incident→participant edges and automatic road/UAV
   routines. Coordinator/task/model records remain nonspatial graph nodes.
4. Operate with effective pause/resume and the named accident injection point. At
   authored 8 s or admitted injection, follow actual stopping→detection→report→bid
   evidence and receipts. Reporter proceeds independently when the borrowed corridor
   is clear; blocked admission stays visible. Medical Alpha keeps its task.
5. Inspect the one eligible reservation, real stop/suspension receipt, capture
   goto/hold receipts and complete 3 s sampled dwell. Display a PNG only after D's
   real storage and separate edge acceptance. Missing/invalid/stale/late evidence
   leaves the chain incomplete/failed, with the exact reason.
6. Seek one journal cut/microstep shared by graph, 3D and inspector. Open artifact
   links and replay with services disabled; replay reads WAL and performs no model,
   physics or camera calls. Native owner replacement always starts a new run/epoch.

The HTTP and CLI end-to-end gates are in `tests/demos/traffic_accident/test_end_to_end.py`. Frontend operation and native replacement profiles are separate gates; see [INTEGRATION.md](INTEGRATION.md).

## Domain verification

```bash
python -m pytest -q -p no:cacheprovider -m 'not docker' tests/demos
```

Predicate-related tests skip when the pinned predicate AST is not configured, rather than use a copied evaluator.
Domain tests select the real physical plugins at the original 15 Hz and an explicitly test-authored target
producer, remove all behaviour fields/relations and feed actual typed commands. They
compare two road/air journals byte-for-byte, test swept blockage, compute shared-model
feasibility and evaluate policy/dwell counterexamples. This is distinct from a full
no-LLM accident chain.

The source comparison is deliberately bounded to the first second, where there is
no decision-latency ambiguity. Reporter interpolation at source rounded timestamps
has maximum error below 0.003 m. Accelerated Alpha moves about 2 m by 1 s, versus
about 7 m in the recorded old constant-speed routine: deviation is about 5 m. This
is an expected declared model change, not visual parity. At the two actual recorded bid-input poses, assessment distance minus rounded
historical distance is +0.01743 m (Alpha) / −0.00897 m (Bravo). The new rest-to-rest
ETA minus historical rounded ETA is +2.100000101 s / +2.033333483 s, respectively;
this includes acceleration, source rounding and upward publication-grid quantization.
No comparable historical joule budget exists. Historical award/arrival/
upload times and legacy battery percentages are not reproduced by these tests.
