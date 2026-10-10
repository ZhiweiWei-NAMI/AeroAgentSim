# SUMO TraCI provider workload

The image's default command is the production provider service:

```text
docker run --rm \
  -e AERO_BENCH_PROVIDER_BIND_HOST=0.0.0.0 \
  -e AERO_BENCH_PROVIDER_PORT=17434 \
  -e AERO_BENCH_RUN_ID=<resolved-run-sha256> \
  -e AERO_BENCH_WORKLOAD_ID=<provider-id> \
  -e AERO_BENCH_CONTRACT=/input/contract.json \
  -e AERO_BENCH_BUNDLE_DIR=/input/bundle \
  -e AERO_BENCH_ARTIFACT_DIR=/input/artifacts \
  ... aero-bench/sumo@sha256:<digest>
```

`selfcheck` is an explicit command and runs the same JSON-line service against
an internally generated real SUMO scenario:

```text
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  aero-bench/sumo@sha256:<digest> selfcheck
```

The provider contract must declare exactly one runtime artifact:

```yaml
artifact_id: artifact.sumo.traffic
artifact_type: sumo.traffic.evidence
producer_id: <provider-id>
visibility: private
relative_path: traffic/evidence/declared-sumo.jsonl
max_size_bytes: 16777216
source_asset_id: null
```

The service creates only the declared normalized `relative_path` below
`AERO_BENCH_ARTIFACT_DIR`, rejects symlink/escape paths, and enforces the
declared `max_size_bytes` before every append and fsync. The host executor must
copy exactly this declared path and identity into the corresponding
`ArtifactRecord`; callers cannot select an undeclared output path or invent an
artifact inventory.

For an explicit Docker-backed integration result, run the repository entry
point with the exact locally available image digest:

```text
AERO_BENCH_SUMO_IMAGE=localhost:5000/aero-bench/sumo@sha256:<digest> \
  python tools/validate_sumo_formal.py
```

This command fails with `BLOCKED` when Docker or the image is unavailable and
only reports success after a real TraCI container RPC round-trip. A skipped
`sumo_integration` pytest test is an environment skip, not integration
evidence.
