# AeroAgentSim ns-3 backend

A real ns-3.48 Wi-Fi ad-hoc/IPv4/UDP backend with a stdlib Python TCP service.
Nodes are opaque identities with ENU positions. There are no entity-type,
mission, workload, session-token or AeroBench artifact dependencies.

Build from the public digest-pinned Ubuntu 24.04 base and SHA-256-verified
ns-3.48 release archive:

```sh
containers/ns3/build.sh
```

The build uses the existing platform native provider, with the original optimized
ns-3 module configuration. Only its binary, dynamic libraries, Python stdlib and
input provenance are copied into the runtime rootfs. Recovered AeroBench sources
remain archival inputs for provenance, outside the build context.

Run the service and smoke test from the repository root:

```sh
docker run -d --name aas-p9-ns3 --label aeroagentsim.job=p9 \
  --cpus 16 --memory 8g -p 127.0.0.1:19004:9000 aeroagentsim/ns3:standalone
python \
  containers/ns3/smoke.py --port 19004 --output /tmp/aas-p9/ns3-smoke
# Remove only the container you just created.
docker rm -f aas-p9-ns3
```

The smoke sends 2,400 packets from one source to four nodes moving apart over
60 simulated seconds, advances at 100 ms, flushes packet lifetimes, repeats the
same seed on a new connection/process, and byte-compares every response. Wall
measurements are written separately from response transcripts.

Host-only contract tests and lint:

```sh
HYPOTHESIS_STORAGE_DIRECTORY=/tmp/aas-p4/hypothesis PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest \
  -q -p no:cacheprovider containers/ns3/tests
python -m ruff check \
  --cache-dir /tmp/aas-p4/ruff-cache containers/ns3
```

See [the backend specification](../../docs/platform/ns3-backend.md) for protocol,
model assumptions, recovered source hashes, measured results and limitations.
The original sources from all inspected tags are retained in
`recovered/sources.tar.gz`, with per-tag hashes in `recovered/provenance.json`;
they are archival and excluded from the Docker build context.

## Standalone build provenance

The Dockerfile has no AeroBench base image or repository dependency. Public bases,
verified source archives, vendored dependency locks/patches, build timings, image
sizes and real validation results are listed in
[the container build record](../../docs/platform/containers.md). APT-selected
artifact URLs/SHA-256 values and installed package versions are retained under
`/opt/aeroagentsim/build-inputs`; Python wheel selection is recorded alongside
its enforced hash lock. Build-only caches, wheels and compilers are excluded
from the runtime where a separate build stage is used.
