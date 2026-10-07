import * as THREE from "three";
import type { O2WMesh } from "./runtime";
import type { OsmElement } from "./source";
import type { TraceTarget } from "../state/target";

export type StaticLayer = "buildings" | "roads" | "terrain";
export interface MeshIdentity { layer: StaticLayer; target: TraceTarget | null; }

export function meshIdentity(mesh: O2WMesh, elements: ReadonlyMap<string, OsmElement>): MeshIdentity {
  const id = mesh.elementId();
  const element = id === null ? undefined : elements.get(id);
  const tags = element?.tags;
  const model = mesh.modelClass() ?? "";
  if (tags?.building !== undefined || tags?.["building:part"] !== undefined || /Building|Roof/.test(model)) {
    return { layer: "buildings", target: id === null ? null : { kind: "building", id: tags?.["aero:building_id"] ?? id } };
  }
  if (tags?.highway !== undefined || tags?.railway !== undefined || /Road|Railway|Junction|Crossing/.test(model)) {
    return { layer: "roads", target: id === null ? null : { kind: "road", id: tags?.["aero:road_id"] ?? id } };
  }
  return { layer: "terrain", target: null };
}

export function meshGeometry(mesh: O2WMesh): THREE.BufferGeometry {
  const positions = new Float32Array(mesh.positions());
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new THREE.BufferAttribute(new Float32Array(mesh.normals()), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(mesh.uvs()), 2));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(mesh.indices()), 1));
  // Clockwise left-handed OSM2World faces become counterclockwise after this
  // reflection. Reversing indices as well would turn roofs and walls inside out.
  geometry.scale(1, 1, -1);
  geometry.computeBoundingSphere();
  return geometry;
}
