# AeroAgentSim

[AeroAgentSim](https://github.com/ZhiweiWei-NAMI/AeroAgentSim) contains
AeroBench 0.1 in [`aero-bench/`](aero-bench/). The platform connects scenario
configuration, immutable compilation, native PX4/Gazebo flight, parcel
custody, sealed verification and replay within this repository.

From the repository root, start the retained local delivery:

```bash
./run-aero-bench
```

After the launcher reports ready, open:

- Viewer: <http://127.0.0.1:5416/?view=live>.
- City Studio: <http://127.0.0.1:5416/city-studio.html>.
- Recorded video: <http://127.0.0.1:5416/platform-0.1/native-parcel-0.1.mp4>.

In `正式运行控制`, use the private bootstrap values from
`aero-bench/credentials/platform-0.1.json`, load the catalog and select
`打开已封存回放`. This opens the existing sealed run without starting another
simulation. The credentials file stays local with mode 0600; the safe
connection pointer is `aero-bench/validation/platform-0.1/connection.json`.

For SSH or VS Code Remote, forward only port **5416** to local port 5416.
Use `127.0.0.1:5416` or `localhost:5416`; browser API requests use the same
origin through `/v1`.

The retained episode has 300 ticks, pickup at tick 60, delivery at tick 128,
and five passing formal goals. Its live reconnect preserved the same run,
11 issued commands and two custody transfers. Publication recovery used the
same runtime and verification seals while preserving the original
`public_trace_projection_failed` summary.

Local data lives under `aero-bench/validation/platform-0.1/`. The video file is
`final-watchable/native-parcel-0.1.mp4` within that directory. These large
artifacts, native input assets and private credentials are not Git source.
See the [platform guide](aero-bench/docs/p02-native-logistics/platform-0.1.md)
for prerequisites, evidence locations and a new physical reproduction.

## Legacy research library

[`airfogsim/`](airfogsim/) retains the AirFogSim research library.
[`benchmarks/`](benchmarks/) and [`paper_code/`](paper_code/) contain its
benchmark and paper implementations.

## Citation

If you use AirFogSim in your research, please cite our paper:

```bibtex
@misc{wei2024airfogsimlightweightmodularsimulator,
      title={AirFogSim: A Light-Weight and Modular Simulator for UAV-Integrated Vehicular Fog Computing}, 
      author={Zhiwei Wei and Chenran Huang and Bing Li and Yiting Zhao and Xiang Cheng and Liuqing Yang and Rongqing Zhang},
      year={2024},
      eprint={2409.02518},
      archivePrefix={arXiv},
      primaryClass={cs.NI},
      url={https://arxiv.org/abs/2409.02518}, 
}
```

## License

This project uses the [MIT License](LICENSE).
