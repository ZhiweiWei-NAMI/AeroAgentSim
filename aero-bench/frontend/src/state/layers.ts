import type { PublicEntityDefinition } from "../generated/aero-bench-contracts";
import { Observable, type Unsubscribe } from "./observable";
import { sameTarget, targetKey, type TraceTarget } from "./target";

/**
 * The typed Public Trace v3 layer tree. Every node maps to a declared
 * section of the validated document; there are no viewer-invented
 * layers.
 *
 * - `imagery` / `terrain`: declared base-layer assets. They are
 *   registered by identity; the console renders them only through the
 *   verified asset resolver and otherwise shows an unresolved state.
 * - `buildings` / `roads` / `regions` / `weather`: projected from the
 *   declared scenario.
 * - `uav` / `ugv` / `pedestrian` / `static_assets` / `trajectories` /
 *   `network_links`: projected from exact SceneState records and their
 *   canonical projections.
 */
export type LayerId =
  | "imagery"
  | "terrain"
  | "buildings"
  | "roads"
  | "weather"
  | "regions"
  | "uav"
  | "ugv"
  | "pedestrian"
  | "static_assets"
  | "trajectories"
  | "network_links";

export type EntityKind = PublicEntityDefinition["kind"];

export const ENTITY_KIND_LAYERS: Readonly<Record<EntityKind, LayerId>> = {
  uav: "uav",
  ugv: "ugv",
  pedestrian: "pedestrian",
  static_asset: "static_assets",
};

export const ENTITY_KINDS: readonly EntityKind[] = ["uav", "ugv", "pedestrian", "static_asset"];

export type LayerGroup = "base" | "overlay" | "operational";

export interface LayerNode {
  readonly id: LayerId;
  readonly group: LayerGroup;
}

export const LAYER_TREE: readonly LayerNode[] = [
  { id: "imagery", group: "base" },
  { id: "terrain", group: "base" },
  { id: "buildings", group: "base" },
  { id: "weather", group: "overlay" },
  { id: "regions", group: "overlay" },
  { id: "roads", group: "overlay" },
  { id: "uav", group: "operational" },
  { id: "ugv", group: "operational" },
  { id: "pedestrian", group: "operational" },
  { id: "static_assets", group: "operational" },
  { id: "trajectories", group: "operational" },
  { id: "network_links", group: "operational" },
];

export type LayerVisibility = Readonly<Record<LayerId, boolean>>;

export interface LayerSnapshot {
  readonly visibility: LayerVisibility;
  readonly isolate: TraceTarget | null;
  readonly hiddenEntities: ReadonlySet<string>;
  readonly hiddenTrajectories: ReadonlySet<string>;
}

function initialVisibility(): LayerVisibility {
  const visibility = {} as Record<LayerId, boolean>;
  for (const node of LAYER_TREE) {
    visibility[node.id] = true;
  }
  return Object.freeze(visibility);
}

function initialSnapshot(): LayerSnapshot {
  return {
    visibility: initialVisibility(),
    isolate: null,
    hiddenEntities: new Set(),
    hiddenTrajectories: new Set(),
  };
}

/**
 * Map layer visibility, per-entity visibility overrides, trajectory
 * toggles and the isolate target. The state is one coherent snapshot:
 * every subscriber sees exactly one emission with the full snapshot at
 * subscribe time and one emission per change.
 */
export class LayerState {
  private readonly snapshot = new Observable<LayerSnapshot>(initialSnapshot());

  snapshotValue(): LayerSnapshot {
    return this.snapshot.value;
  }

  visibility(): LayerVisibility {
    return this.snapshot.value.visibility;
  }

  isLayerVisible(layer: LayerId): boolean {
    return this.snapshot.value.visibility[layer];
  }

  isolateTarget(): TraceTarget | null {
    return this.snapshot.value.isolate;
  }

  hiddenEntityKeys(): ReadonlySet<string> {
    return this.snapshot.value.hiddenEntities;
  }

  hiddenTrajectoryIds(): ReadonlySet<string> {
    return this.snapshot.value.hiddenTrajectories;
  }

  isEntityHidden(target: TraceTarget): boolean {
    return this.snapshot.value.hiddenEntities.has(targetKey(target));
  }

  isTrajectoryHidden(entityId: string): boolean {
    return this.snapshot.value.hiddenTrajectories.has(entityId);
  }

  setLayerVisible(layer: LayerId, visible: boolean): void {
    if (this.snapshot.value.visibility[layer] === visible) {
      return;
    }
    this.setSnapshot({ ...this.snapshot.value, visibility: { ...this.snapshot.value.visibility, [layer]: visible } });
  }

  toggleLayer(layer: LayerId): void {
    this.setLayerVisible(layer, !this.isLayerVisible(layer));
  }

  setIsolate(target: TraceTarget | null): void {
    this.setSnapshot({ ...this.snapshot.value, isolate: target });
  }

  toggleIsolate(target: TraceTarget): void {
    this.setIsolate(sameTarget(this.isolateTarget(), target) ? null : target);
  }

  toggleEntity(target: TraceTarget): void {
    const next = new Set(this.snapshot.value.hiddenEntities);
    const key = targetKey(target);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    this.setSnapshot({ ...this.snapshot.value, hiddenEntities: next });
  }

  toggleTrajectory(entityId: string): void {
    const next = new Set(this.snapshot.value.hiddenTrajectories);
    if (next.has(entityId)) {
      next.delete(entityId);
    } else {
      next.add(entityId);
    }
    this.setSnapshot({ ...this.snapshot.value, hiddenTrajectories: next });
  }

  /**
   * Adopt the declared default visibility of the document's base and
   * overlay layers. Declared, never invented.
   */
  applyDeclaredDefaults(defaults: ReadonlyMap<LayerId, boolean>): void {
    const visibility = { ...this.snapshot.value.visibility };
    let changed = false;
    for (const [layer, visible] of defaults) {
      if (visibility[layer] !== visible) {
        visibility[layer] = visible;
        changed = true;
      }
    }
    if (changed) {
      this.setSnapshot({ ...this.snapshot.value, visibility });
    }
  }

  subscribe(listener: (snapshot: LayerSnapshot) => void): Unsubscribe {
    return this.snapshot.subscribe(listener);
  }

  private setSnapshot(snapshot: LayerSnapshot): void {
    this.snapshot.set(snapshot);
  }
}
