# Data and assets

The default traffic-accident demo uses committed registry snapshots, road inputs and a small OSM-derived city. The viewer and camera render building footprints as extrusions and vehicles as procedural meshes. No separate mesh pack is required.

OSM-derived data is credited to **© OpenStreetMap contributors, ODbL 1.0**. Read the [lite-city attribution](scenarios/demos/traffic-accident/inputs/lite-city/ATTRIBUTION.txt) and the [authoring-map attribution](src/aeroagentsim/authoring/assets/ATTRIBUTION.txt) for sources and dataset notes.

`AEROAGENTSIM_TRAFFIC_ASSET_ROOT` optionally selects a separately supplied high-detail pack. Those meshes and textures are not distributed in this repository. Use only assets whose terms permit your intended research and redistribution, and retain their attribution. The public demo does not require access to the AeroGraph repository.

See [Install](docs/getting-started/install.md), [Views](docs/concepts/views.md) and the [traffic walkthrough](docs/examples/traffic-accident.md).

Optional mesh packs use format `aeroagentsim.osm2world-mesh-pack/v1` and coordinate format `aeroagentsim.osm2world-source-coordinates/v1`. When using a previously generated pack, update its format labels to these names; mesh data and coordinates are unchanged.
