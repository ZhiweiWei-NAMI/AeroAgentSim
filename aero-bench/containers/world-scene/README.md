# Optional world-scene projection workload

This image contains a read-only `ResolvedScenario/v2` projection service. The
`world.scene.rpc` adapter is intentionally absent from the built-in provider
registry, so it is not a formal runtime authority and is not selected by the
standard executor path. Static declarations are owned only by
`scenario.compiler`; dynamic entities remain owned by their motion or traffic
providers.

If launched explicitly, the default command is the projection service:

```text
docker run --rm \
  -e AERO_BENCH_PROVIDER_BIND_HOST=0.0.0.0 \
  -e AERO_BENCH_PROVIDER_PORT=17436 \
  -e AERO_BENCH_RUN_ID=<resolved-run-sha256> \
  -e AERO_BENCH_SEED=<resolved-seed> \
  -e AERO_BENCH_WORKLOAD_ID=<projection-workload-id> \
  -e AERO_BENCH_PROVIDER_TOKEN=<executor-issued-session-sha256> \
  -e AERO_BENCH_CONTRACT=/input/contract.json \
  -e AERO_BENCH_BUNDLE_DIR=/input/bundle \
  -e AERO_BENCH_ARTIFACT_DIR=/input/artifacts \
  ... aero-bench/world-scene@sha256:<digest>
```

The service accepts only canonical `aero-bench.workload-contract/v3` provider
contracts containing an exact `aero-bench.resolved-scenario/v2` and the exact
asset projection authorized to the workload. Provider configuration is limited
to implementation identity:

```json
{"provider_id":"world","scene":{"commit":"<40-hex>","version":"0.3.0-world-scene.2"},"schema_version":"aero-bench.world-scene/v2"}
```

The reduced `prepare` request binds `provider_id`, `run_id`, `runtime_image`,
`config_digest`, `artifact_requirements`, and `scenario_digest`. Source
WorldPackage fields, independent scene declarations, and compatibility payloads
are rejected. Barrier events use `world.scene.projection.v2` and report only a
digest-bound mirror of compiler-owned scenario identity; the service exposes no
command or static-scene RPC authority.

`selfcheck` is an explicit command. It constructs and canonically seals a small
validated scenario projection in source, rather than packaging or rebuilding a
WorldPackage, then exercises the real JSON-line service lifecycle:

```text
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  aero-bench/world-scene@sha256:<digest> selfcheck
```

The selfcheck proves exact contract loading, workload identity fields
(seed/clock/projection), compiler ownership of one static entity, reduced
prepare parsing, session-token denial, barrier receipts, snapshot identity,
and finalization evidence.

The workload contract must declare exactly one private runtime artifact of type
`world.scene.evidence`. The service creates only its normalized
`relative_path` below `AERO_BENCH_ARTIFACT_DIR`, rejects symlink and escape
paths, enforces `max_size_bytes`, and binds the resulting bytes to the provider
finalization receipt.

`AERO_BENCH_PROVIDER_TOKEN` is required on the first `prepare` and every later
frame, including `shutdown`. The token-free `probe` operation is
side-effect-free and reports only public workload identity.
