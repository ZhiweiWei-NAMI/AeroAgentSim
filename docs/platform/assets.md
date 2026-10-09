# Independent asset and map inputs

Studio's default map is a packaged, attributed, 3.5 KB historical OSM selection;
it works from a non-editable wheel. It is deliberately small and sparse. Choose
a larger real extract explicitly when authoring a different area. Its bounds
are reported by `/v1/studio/catalog`; no neighbouring checkout is consulted.

```bash
export AEROAGENTSIM_AEROGRAPH_ROOT=/absolute/path/to/AeroGraph
export AEROAGENTSIM_STUDIO_ROOT=/absolute/path/to/writable/drafts
# Optional: replaces the packaged map catalogue; missing/invalid files fail.
export AEROAGENTSIM_OSM_EXTRACTS='{"my-area":"/absolute/path/to/source.osm.xml"}'
aeroagentsim serve --out runs --frontend frontend/dist
```

AeroGraph remains an explicit optional authoring/source-compilation input.
Studio requires its persisted `semantic-directory/data` tree. It does not build
or change AeroGraph. Snapshot-based pack scenarios run without that checkout;
engine-free journal replay needs neither maps nor ontology data. Scenario source
paths can use `${AEROAGENTSIM_AEROGRAPH_ROOT}`; an unset variable is an error,
and existing source/digest checks still apply. No value is inferred from a
neighbouring project or the user's HOME.

Fetch and compile a bounded map from the public OSM map API, with explicit
bounds and assumptions (larger regions should use a downloaded OSM extract):

```bash
python tools/build_map.py --fetch-bounds 121.50 31.29 121.53 31.31 \
  --out /path/to/asset-store/my-area --alt-m 0 --level-height-m 3.5 \
  --public-url /assets/my-area/city.geojson
# Or reproduce from saved source bytes, without a network request:
python tools/build_map.py --osm /path/to/source.osm.xml \
  --out /path/to/asset-store/my-area --alt-m 0 --level-height-m 3.5 \
  --public-url /assets/my-area/city.geojson
```

Run with installed platform/server dependencies, or `PYTHONPATH=src`. The script
writes source XML, GeoJSON, scene metadata, provenance/hash and attribution. The
public fetch is explicit; the service never downloads a map on a request. For
Studio, point `AEROAGENTSIM_OSM_EXTRACTS` at the resulting `source.osm.xml`. For
viewer-only scenarios, copy the generated `origin` and `scene` into scenario
`origin` and `presentation.scene`, and serve the GeoJSON at `--public-url`.
This pipeline retains OSM footprints/roads and source diagnostics. It does not
replace the historical textured OSM2World pack or a collision/terrain compiler.

Fetch licensed display models and environment data from pinned public sources:

```bash
python tools/sync_assets.py --out frontend/public/assets \
  --work-dir /path/to/writable/converter --models all --optimize meshopt
# Smaller upstream-download check without model conversion tooling:
python tools/sync_assets.py --out /path/to/asset-store --models car
```

No source-tree flag or implicit old-project input remains. X500 conversion uses
the frontend's existing Three.js installation and installs the existing optional
converter tools `linkedom@0.18.13` and `@gltf-transform/cli@4.5.1` only under
`--work-dir`; it does not alter frontend dependencies or its node_modules.
Public-source revision pins, generated hashes and licence notices are retained.
Large outputs stay ignored or outside the repository. The root
`frontend/assets.manifest.json` identifies historical inventory separately from
installed outputs; each build's actual inventory is `<out>/inventory.json`.

[ASSETS.md](../../ASSETS.md) records licences, source notices and undecided assets.
[RETIREMENT.md](RETIREMENT.md) states which missing capabilities still block deletion.
