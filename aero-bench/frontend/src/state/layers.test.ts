import { describe, expect, it } from "vitest";
import { ENTITY_KIND_LAYERS, LAYER_TREE, LayerState, type LayerId } from "./layers";
import { publicTrace, scenario } from "../testing/trace-v3-fixture";
import { parsePublicTrace } from "../trace";

describe("LayerState", () => {
  it("declares exactly the typed public-trace/v3 layer tree", () => {
    expect(LAYER_TREE.map((node) => node.id)).toEqual([
      "imagery",
      "terrain",
      "buildings",
      "weather",
      "regions",
      "roads",
      "uav",
      "ugv",
      "pedestrian",
      "static_assets",
      "trajectories",
      "network_links",
    ]);
  });

  it("maps every declared entity kind onto exactly one layer", () => {
    expect(ENTITY_KIND_LAYERS.uav).toBe("uav");
    expect(ENTITY_KIND_LAYERS.ugv).toBe("ugv");
    expect(ENTITY_KIND_LAYERS.pedestrian).toBe("pedestrian");
    expect(ENTITY_KIND_LAYERS.static_asset).toBe("static_assets");
  });

  it("emits exactly one coherent snapshot at subscribe time", () => {
    const layers = new LayerState();
    const snapshots: unknown[] = [];
    const unsubscribe = layers.subscribe((snapshot) => snapshots.push(snapshot));

    expect(snapshots).toHaveLength(1);
    const snapshot = layers.snapshotValue();
    expect(snapshot.isolate).toBeNull();
    expect(snapshot.hiddenEntities.size).toBe(0);
    expect(snapshot.hiddenTrajectories.size).toBe(0);
    expect(Object.keys(snapshot.visibility).sort()).toEqual(
      LAYER_TREE.map((node) => node.id).sort(),
    );
    for (const node of LAYER_TREE) {
      expect(snapshot.visibility[node.id]).toBe(true);
    }
    unsubscribe();
  });

  it("notifies once per change after the initial emission", () => {
    const layers = new LayerState();
    let changes = 0;
    const unsubscribe = layers.subscribe(() => {
      changes += 1;
    });
    const initial = changes;
    layers.toggleLayer("trajectories");
    layers.toggleLayer("trajectories");
    layers.setIsolate({ kind: "region", id: "zone.restricted" });
    layers.toggleEntity({ kind: "entity", id: "fixture.uav" });
    layers.toggleTrajectory("fixture.uav");
    expect(changes - initial).toBe(5);
    unsubscribe();
  });

  it("adopts declared scenario layer defaults without inventing values", () => {
    const layers = new LayerState();
    const trace = parsePublicTrace(
      publicTrace({
        scenario: scenario({
          base_layers: [
            {
              layer_id: "fixture.imagery",
              kind: "imagery",
              asset_id: "fixture.geoid",
              visibility: "public",
              default_visible: false,
            },
          ],
        }),
      }),
    );
    const defaults = new Map<LayerId, boolean>();
    for (const layer of trace.scenario.base_layers) {
      defaults.set("imagery", layer.default_visible);
    }

    layers.applyDeclaredDefaults(defaults);
    expect(layers.isLayerVisible("imagery")).toBe(false);
    expect(layers.isLayerVisible("terrain")).toBe(true);

    layers.applyDeclaredDefaults(new Map([["imagery", true]]));
    expect(layers.isLayerVisible("imagery")).toBe(true);
  });

  it("isolates exactly one target and clears it", () => {
    const layers = new LayerState();
    const entity = { kind: "entity" as const, id: "fixture.uav" };
    layers.toggleIsolate(entity);
    expect(layers.isolateTarget()).toEqual(entity);

    layers.toggleIsolate(entity);
    expect(layers.isolateTarget()).toBeNull();

    layers.setIsolate(entity);
    layers.setIsolate(null);
    expect(layers.isolateTarget()).toBeNull();
  });

  it("toggles per-entity visibility overrides and trajectory toggles", () => {
    const layers = new LayerState();
    const entity = { kind: "entity" as const, id: "fixture.uav" };
    expect(layers.isEntityHidden(entity)).toBe(false);

    layers.toggleEntity(entity);
    expect(layers.isEntityHidden(entity)).toBe(true);
    layers.toggleEntity(entity);
    expect(layers.isEntityHidden(entity)).toBe(false);

    expect(layers.isTrajectoryHidden("fixture.uav")).toBe(false);
    layers.toggleTrajectory("fixture.uav");
    expect(layers.isTrajectoryHidden("fixture.uav")).toBe(true);
    layers.toggleTrajectory("fixture.uav");
    expect(layers.isTrajectoryHidden("fixture.uav")).toBe(false);
  });
});
