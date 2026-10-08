# AeroAgentSim ns-3 backend

A real ns-3.48 Wi-Fi ad-hoc/IPv4/UDP backend with a stdlib Python TCP service.
Nodes are opaque identities with ENU positions. There are no entity-type,
mission, workload, session-token or AeroBench artifact dependencies.

Build from the recovered, digest-pinned production image already stored locally:

```sh
DOCKER_BUILDKIT=0 docker build --memory 8g --cpu-period 100000 --cpu-quota 800000 \
  -t aeroagentsim/ns3:dev-p4b containers/ns3
```

The Dockerfile reuses its SHA-verified ns-3 tree and compiler, then copies only
runtime files into a fresh rootfs derived from that image's Ubuntu 24.04. The
compiler, ns-3 sources, old backend and recovered archives stay outside the
runtime image. This avoids a fresh Docker Hub/download dependency.

Run the service and smoke test from the repository root:

```sh
docker run -d --name aas-ns3-p4 --label aeroagentsim.job=p4 \
  --cpus 8 --memory 8g -p 127.0.0.1:19004:9000 aeroagentsim/ns3:dev-p4b
/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python \
  containers/ns3/smoke.py --port 19004 --output /tmp/aas-p4/smoke
# Remove only the container you just created.
docker rm -f aas-ns3-p4
```

The smoke sends 2,400 packets from one source to four nodes moving apart over
60 simulated seconds, advances at 100 ms, flushes packet lifetimes, repeats the
same seed on a new connection/process, and byte-compares every response. Wall
measurements are written separately from response transcripts.

Host-only contract tests and lint:

```sh
HYPOTHESIS_STORAGE_DIRECTORY=/tmp/aas-p4/hypothesis PYTHONDONTWRITEBYTECODE=1 \
  /mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python -m pytest \
  -q -p no:cacheprovider containers/ns3/tests
/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python -m ruff check \
  --cache-dir /tmp/aas-p4/ruff-cache containers/ns3
```

See [the backend specification](../../docs/platform/ns3-backend.md) for protocol,
model assumptions, recovered source hashes, measured results and limitations.
The original sources from all inspected tags are retained in
`recovered/sources.tar.gz`, with per-tag hashes in `recovered/provenance.json`;
they are archival and excluded from the Docker build context.
