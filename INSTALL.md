# Installation

Follow [Install AeroAgentSim](docs/getting-started/install.md) for Python, the kernel, the console build and camera dependencies. Then [run the traffic-accident demo](docs/getting-started/quickstart.md).

The release includes `aerokernel/`. Current development checkouts can install the sibling instead:

```bash
python -m pip install -e ../aerokernel -e '.[server]'
```

CI uses the included kernel when available. Until that subtree lands, configure repository variables `AEROKERNEL_REPOSITORY` and `AEROKERNEL_REF` for the sibling checkout; private kernel access also requires `KERNEL_READ_TOKEN`.

Native simulator services are optional; see [Containers](docs/guides/containers.md).
