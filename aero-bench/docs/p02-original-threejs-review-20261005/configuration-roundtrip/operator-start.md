# P02 compilation-bound operator startup

This command runs only from an already authorized operator launcher context containing the existing variables `AERO_BENCH_CONTROL_BOOTSTRAP_TOKEN` and `AERO_BENCH_CONTROL_BOOTSTRAP_CSRF`. Their values must not appear in source, screenshots, reports or chat. Both variables are absent in the coordinator context checked for this handoff; availability in the user's existing launcher is not established. No credentials were read or created.

Saved draft `68c049b28e15d4e2886b36ff222361e6bfb059b4ff8b23339dd7e0a4fa7f368f` compiled to `a504dba5ab6c8c2183b5ce419173f89bd03289860e62ffeee760ff529417c610`; its exact run is `9e07a000f35961e5d4bca89553729a8e743143781aad6d213fed722abfd324fd` (seed 20261002, native-v7). Compilation root and runner configuration below exist. Port 8766 was available during inspection. This is a supported isolated live service, not the current sealed native-v8 service. Serving/catalog loading is not evidence of simulator startup.

```bash
cd /mnt/data2/weizhiwei/AERO_BENCH
: "${AERO_BENCH_CONTROL_BOOTSTRAP_TOKEN:?Existing authorized operator context required}"
: "${AERO_BENCH_CONTROL_BOOTSTRAP_CSRF:?Existing authorized operator context required}"
test ! -e /mnt/data1/weizhiwei/AERO_WORLD_runtime/p02_compiled_native_v7_operator_20261005 || exit 1
PYTHONPATH=. /home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python -m aero_bench.control.cli serve \
  --compilation-id a504dba5ab6c8c2183b5ce419173f89bd03289860e62ffeee760ff529417c610 \
  --compilation-root /mnt/data2/weizhiwei/AERO_BENCH/validation/platform-plan-20261001/final-verify/authoring-api/compilations-run2 \
  --execution-output /mnt/data1/weizhiwei/AERO_WORLD_runtime/p02_compiled_native_v7_operator_20261005 \
  --runner-config /mnt/data2/weizhiwei/AERO_BENCH/validation/platform-plan-20261001/W1-I5/v8-registration-attempt-1/runner-v8.yaml \
  --bind-host 127.0.0.1 --port 8766 \
  --allowed-origin http://127.0.0.1:5413 \
  --bootstrap-token-env AERO_BENCH_CONTROL_BOOTSTRAP_TOKEN \
  --bootstrap-csrf-env AERO_BENCH_CONTROL_BOOTSTRAP_CSRF
```

Open `http://127.0.0.1:5413/?view=live&run=9e07a000f35961e5d4bca89553729a8e743143781aad6d213fed722abfd324fd&scene=1` in the existing authorized browser, then use its Live connection form with endpoint `http://127.0.0.1:8766`. Supply the existing operator token and CSRF in its supported private credential fields, load catalog, select the exact native-v7 run above, then Start. HTTP uses `Authorization: Bearer …` and `X-Aero-Bench-CSRF`; this is not cookie login. Do not screenshot credential fields or print start responses, which contain per-run credentials. Do not use `tools/run_stack.py`: it generates new bootstrap credentials. Do not use `--sealed-run-id` for this compilation or restart the active v8 service.

If no existing authorized launcher holds this pair, isolated native execution remains blocked on that operator context; this handoff neither generates a replacement nor changes authorization. Once started, actual telemetry/barriers and the resulting public map must be verified before any complete-workflow claim.

Source: `aero_bench/control/cli.py` `_serve`, `ControlRunManager.from_compilation`, and frontend `app.ts` `connectControlService`/`startSelectedRun`. The named `feature/p02-bench-native-integration-20261005` branch is local only; exact remote ref lookup returned no branch. The export branch is separate.
