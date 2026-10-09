# Asset attribution and source policy

The repository's MIT code licence does not replace input-data licences. Large
viewer assets are optional external build outputs, absent from this checkout.
The historical frontend inventory is provenance, not an installed asset set.

| Input | Licence and attribution | Distribution / reproduction |
| --- | --- | --- |
| `src/aeroagentsim/authoring/assets/wujiaochang.osm.xml`; identical `tests/authoring/fixtures/wujiaochang.osm.xml` | © OpenStreetMap contributors; [OSM copyright](https://www.openstreetmap.org/copyright), [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1.0/) | Packaged bounded historical Overpass extract, three building ways and one road. Source bytes, IDs, tags and coordinates are retained. `assets/provenance.json` records its digest and selection; source snapshot is 2024-09-03T07:07:43Z; crop extraction date is unknown. This is not a current or complete district map. |
| `tests/authoring/fixtures/wujiaochang.net.xml` | Derived from OSM; same ODbL source attribution | Existing precompiled SUMO test fixture; not a runtime default. Its complete original 979,135-byte ODbL source is supplied in `src/aeroagentsim/authoring/assets/wujiaochang-source.osm.xml`, with its original note/meta and recorded hash. Network metadata records the original conversion. Regenerate new networks with Studio's real `netconvert` hook; tests that monkeypatch conversion are contract tests, not native conversion evidence. |
| OSM fetched/compiled by `tools/build_map.py` | © OpenStreetMap contributors; ODbL 1.0 | Source XML and attribution travel alongside generated ENU GeoJSON. CLI records source/query/hash and caller-specified altitude/level-height assumptions. Unknown heights retain diagnostics. Current upstream data may change; save source XML to reproduce geometry. |
| OSM2World-default-style car glTF/bin and `textures/sky/DaySkyHDRI041B.hdr` | OSM2World-default-style contributors; [upstream CC0 notice](https://github.com/tordanik/OSM2World-default-style/blob/81ade9bf9793181774891548ac29448f366efd3c/COPYING.txt) | `tools/sync_assets.py` fetches directly from revision `81ade9bf9793181774891548ac29448f366efd3c`; retains notice and per-file hashes in output `inventory.json`. No old project required. |
| PX4 x500_base SDF/DAE/STL → generated `x500.glb` | Copyright (c) 2022 Rudis Laboratories; PX4 Autopilot for Drones; [model BSD-3-Clause](https://github.com/PX4/PX4-gazebo-models/blob/e5997f455e33ec071c7c677fc59a65781de496a4/models/x500_base/LICENSE), [repository notice](https://github.com/PX4/PX4-gazebo-models/blob/e5997f455e33ec071c7c677fc59a65781de496a4/LICENSE) | Same script fetches pinned revision `e5997f455e33ec071c7c677fc59a65781de496a4`; preserves both notices. Conversion uses SDF visual poses and source units, authored display PBR materials, Y-up metres and smoothing/optional mesh compression. It is display geometry, not physical calibration. |
| Vendored Draco and Basis Universal decoder binaries/wrappers | Apache-2.0; Google Draco and Binomial LLC Basis Universal. Full notices in `frontend/public/decoders/DRACO-LICENSE.txt` and `BASIS-LICENSE.txt` | Verified against Three.js r170 public decoder inputs; per-file upstream/digests in `frontend/public/decoders/provenance.json`. Three.js itself retains its separate MIT notice. |
| N8AO | MIT notice in `frontend/src/scene/vendor/N8AO.LICENSE` | Existing repository source; notice remains distributed. |

Owner decisions are still required for these old assets; Q4 did not copy them:

- Existing `frontend/public/textures/buildings/building_residential.jpg`: its upstream author/licence is not established. It has no current source consumer but Vite copies public files; obtain rights or remove it from release distribution. Q4 did not introduce or replace its bytes.
- Existing `frontend/public/logo192.png` and `favicon.ico`: app icon ownership is not documented; establish the owner/source before release, or replace/exclude them. No third-party licence is inferred from their filenames.
- Existing `frontend/public/data/traffic/sumocfg/`: the network/polygon metadata establishes OSM origin (ODbL), but the original XML source and route-generation provenance are absent. Archive those inputs, or rebuild the example from explicitly supplied OSM; do not claim the old sample is independently reproducible.
- `models/city-runtime/holybro-x500-textured-preview.glb`: local redistribution licence and texture origin are not established. Obtain rights/provenance, replace with the BSD-source model, or archive outside the release.
- `models/bigcity/**`: Unity package redistribution rights are not established. Obtain explicit rights or exclude it from shipped scenarios.
- The historical Huangpu OSM2World textured mesh pack: original OSM data is ODbL, but full generator/runtime/patch/config and every texture's upstream mapping must be preserved and independently rebuilt before promising this exact presentation. Retain that capability with a standalone producer, or explicitly accept Studio ENU GeoJSON as the cutover scope. The new builder does not claim byte-equivalent meshes, facades, terrain or imagery. The old tonemapped sky and modified Windows texture family also need a documented derivation or replacement by verified upstream inputs.

Any additional owner-supplied GLB/HDRI must carry a source URL, redistribution
licence, attribution and digest. A caller-supplied path alone does not establish
rights. See [asset configuration and build commands](docs/platform/assets.md).
