# Legacy AirFogSim research simulator

This is the independent SUMO-based simulator from `crowdsensing` at
`a7b130a2ff019b09df5dce408fa418a2c28ab36e` (2025-03-18). It is retained here so the
crowdsensing algorithms and experiments remain available without overwriting
the current AeroAgentSim package.

`blockchain_example` at `14dc2299e9fad48ba9e76acef9945474128686b5` is an ancestor
of that commit. Its scheduler, blockchain metrics, and example are already
included; the example became `examples/example03_manage_Blockchain_example.py`.
A second legacy package or an older copy of those implementations is unnecessary.

## What is preserved

- `airfogsim/`: legacy simulator, traffic/task/channel/mission/sensor/energy and
  blockchain managers, schedulers, and evaluation logic.
- `airfogsim/algorithm/`: DDQN, D3QN, MADDPG, MAPPO, shared MAPPO, and transformer
  variants, including the crowdsensing-specific implementations.
- `baselines/`, `my_test/`, `old_train/`, `train/`, `train2/`: source experiment
  drivers and configurations. Their names and historical variants are retained.
- `examples/`, `docs/source/`, `icon/`, and both SUMO map directories.
- Original dependency snapshots, `LICENSE`, and `README.upstream.md`.

The imported source is 375 files / 25,569,741 bytes before the small corrections
and additional guidance/tests in this integration. `SOURCE_MANIFEST.json` lists
every original blob, its destination, and each omitted artifact. The original
README says MIT, but the actual retained `LICENSE` is Apache-2.0; consult that
file rather than the obsolete README sentence.

This is source preservation with an isolated runtime. It does not expose these
algorithms through the current AeroAgentSim API or claim that they were ported
to the current simulation kernel.

## Keep the environments separate

The repository's supported package is under `src/`. This project also calls its
package `airfogsim`, using the old `AirFogSimEnv` API. Do not copy this directory
into `src/`, add it to the current package discovery, or mix both packages on a
single Python import path.

Use a separate virtual environment. The original code and dependency snapshots
come from the Python 3.10 era; all retained Python sources pass Python 3.10
syntax parsing, and compilation was checked under Python 3.12. That is not a
complete dependency/runtime compatibility test.

### Dependencies

The original `requirements.txt`, `requirement.txt`, and `requirements_win.txt`
are historical environment snapshots, not newly verified lock files. In
particular, they include platform-specific NVIDIA/CUDA packages, and the main
snapshot includes CuPy CUDA 11 alongside PyTorch CUDA 12 dependencies. Do not
install these snapshots into the current AeroAgentSim environment.

A CPU/heuristic environment needs these imports from the preserved source:
NumPy, pandas, PyYAML, Jinja2, matplotlib, Pillow, OSMnx (with GeoPandas/Shapely/
pyproj), SymPy, scikit-learn, tqdm, TraCI, and sumolib. Tkinter must also be
available because visualization modules are imported even for headless runs.
The original snapshots omit scikit-learn although `algorithm_sched.py` imports
`sklearn.cluster.DBSCAN`; install it explicitly. XML conversion also uses lxml.

RL experiments additionally need PyTorch; training drivers use TensorBoard and
pyinstrument. CuPy is optional when `useCUPY=False` is set before import. Install
a PyTorch/CUDA build appropriate for the machine if GPU training is required.

The external `sumo` executable is required for live simulation. The upstream
README reports SUMO 1.15.0; map configuration files originated with SUMO 1.8.0,
and the retained Python dependency snapshots pin TraCI/sumolib 1.20.0. Matching
these components and running a full simulation is still required before using
results for research.

## Running examples

Examples interpret map/icon paths relative to the working directory. After
setting up the separate environment and SUMO, run from `examples/` and put this
legacy project first on `PYTHONPATH`:

```sh
cd legacy/airfogsim/examples
export PYTHONPATH="$(pwd)/.."
export useCUPY=False
python example03_manage_Blockchain_example.py config.sumo.yaml
```

`config.sumo.yaml` is a short live-SUMO configuration derived from the preserved
example config. It uses the included Wujiaochang map at a 0.1-second traffic step
and does not require a pre-generated CSV. It has not been executed end-to-end in
the integration environment, which lacks SUMO and several legacy dependencies.

For an experiment, change into its own driver directory, set `PYTHONPATH` to the
absolute path of `legacy/airfogsim`, inspect its config, and run its `main.py`.
Do not launch training as a test: some drivers write models, logs, and evaluation
outputs or run many episodes. The `testAPI.py` file is a historical API sketch,
not a verified current smoke test.

### Real-traffic data is not bundled by default

Most configs refer to `sumo_wujiaochang/tripinfo_0.1_500s.csv`. That file was not
tracked in the source branch. The only tracked trace, `tripinfo.csv`, is a
66,049,990-byte generated export and is omitted from the working tree. It spans
0.0–199.5 seconds, so it cannot replace the missing 500-second trace for configs
whose replay starts at 200 seconds.

The exact historical trace remains recoverable from the retained merge history:

```sh
# From the repository root, only if this historical trace is needed:
git show a7b130a2ff019b09df5dce408fa418a2c28ab36e:sumo_wujiaochang/tripinfo.csv \
  > legacy/airfogsim/sumo_wujiaochang/tripinfo.csv
```

Before using a real-mode config, supply the intended dataset or select an
available time range and timestep deliberately. The original README explains
export/conversion, but generated data must match the columns expected by
`TrafficManager`, including `data_timestep` and vehicle position/speed fields.
Do not silently rename an incompatible trace to satisfy a missing filename.

## Small, reproduced corrections

Only these original source files were changed:

1. `airfogsim/manager/block_manager.py`: refresh a block's hash after assigning
   its final predecessor. A batch of two queued blocks previously failed
   `isValidChain()` after the second insertion.
2. `examples/example03_manage_Blockchain_example.py`: pass the supported PoS
   enum instead of the string `'PoS'`, which the scheduler rejects; report the
   block's estimated memory size rather than labeling its transaction count
   as bytes. The scheduler's existing count-returning API is unchanged.
3. `testAPI.py`: restore the missing comma before the CPU allocation list.
   The original expression attempts to index a string with a tuple.

No algorithm, training variant, or feature was removed as erroneous. Generated
bytecode, IDE metadata, Sphinx build output, a TensorBoard event file, the BFG
history-cleanup JAR, and the generated traffic CSV were omitted from the working
tree. They remain in the source history, and the manifest records each omission.

## Verification

From the repository root, with NumPy installed:

```sh
python -m unittest discover -s legacy/airfogsim/tests -v
```

The tests exercise real legacy blockchain modules in an isolated import
namespace, without loading the dependency-heavy environment initializer. They
cover batched-chain validation, tamper detection, enum handling, example metric
units, the API-sketch typo, snapshot integrity, source compilation, and package/
pytest isolation. They are not substitutes for a SUMO or RL experiment.

The root `pytest.ini` still selects only `tests/`, and setuptools package
finding still selects only `src/`. This directory has its own `pytest.ini` for
explicit legacy test runs. Current-package source, dependencies, and test
configuration are unchanged by this integration.
