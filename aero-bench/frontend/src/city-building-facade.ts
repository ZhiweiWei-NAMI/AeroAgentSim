import * as THREE from "three";
import { mergeGroups, mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

// One window bay and one floor in the supplied BigCity facade atlas.
export const FACADE_BAY_WIDTH_M = 3;
export const FACADE_FLOOR_HEIGHT_M = 3.5;
const NARROW_WALL_M = 2.5;
const WINDOWS_PER_TILE = 4;
const FACADE_RELIEF_M = 0.12;
const WINDOW_SILL_WIDTH_M = 2.05;
const WINDOW_SILL_HEIGHT_M = 0.10;

export function needsMeteredFacadePart(width: number, depth: number): boolean {
  if (!Number.isFinite(width) || !Number.isFinite(depth) || width <= 0 || depth <= 0) {
    throw new Error("Invalid OSM building part dimensions");
  }
  return Math.min(width, depth) < 5 || Math.max(width, depth) / Math.min(width, depth) > 4;
}

/** BoxGeometry face order is +X, -X, +Y, -Y, +Z, -Z. UVs are physical metres per tile. */
export function meteredFacadeGeometry(width: number, depth: number, height: number,
                                      facadeRelief = true): THREE.BufferGeometry {
  if (!Number.isFinite(height) || height <= 0) throw new Error("Invalid OSM building part height");
  needsMeteredFacadePart(width, depth);
  const canDetail = facadeRelief && Math.min(width, depth) >= 2;
  const relief = canDetail ? Math.min(FACADE_RELIEF_M, Math.min(width, depth) * .035) : 0;
  const geometry = new THREE.BoxGeometry(width - relief * 2, height, depth - relief * 2);
  const uv = geometry.getAttribute("uv") as THREE.BufferAttribute;
  const faceSpans = [depth, depth, width, width, width, width];
  geometry.clearGroups();
  for (let face = 0; face < 6; face++) {
    const horizontal = faceSpans[face]!;
    const vertical = face === 2 || face === 3 ? depth : height;
    const metresPerU = face === 2 || face === 3 ? FACADE_BAY_WIDTH_M : FACADE_BAY_WIDTH_M * WINDOWS_PER_TILE;
    const metresPerV = face === 2 || face === 3 ? FACADE_BAY_WIDTH_M : FACADE_FLOOR_HEIGHT_M * WINDOWS_PER_TILE;
    for (let vertex = face * 4; vertex < face * 4 + 4; vertex++) {
      uv.setXY(vertex, uv.getX(vertex) * horizontal / metresPerU, uv.getY(vertex) * vertical / metresPerV);
    }
    const materialIndex = face === 2 || face === 3 ? 2 : horizontal < NARROW_WALL_M ? 1 : 0;
    geometry.addGroup(face * 6, 6, materialIndex);
  }
  uv.needsUpdate = true;
  const groupedBody = mergeGroups(geometry);
  if (!canDetail) return groupedBody;

  const details = facadeReliefGeometry(width, depth, height, relief);
  const merged = mergeGeometries([groupedBody, details]);
  if (merged === null) {
    groupedBody.dispose();
    details.dispose();
    throw new Error("Could not batch facade relief geometry");
  }

  const indicesByMaterial = [[], [], []] as number[][];
  const bodyIndex = groupedBody.getIndex();
  const detailIndex = details.getIndex();
  if (bodyIndex === null || detailIndex === null) throw new Error("Facade geometry lost its triangle indices");
  for (const group of groupedBody.groups) {
    const materialIndex = group.materialIndex;
    if (materialIndex === undefined) throw new Error("Facade geometry group has no material index");
    const target = indicesByMaterial[materialIndex];
    if (target === undefined) throw new Error("Facade geometry references an unavailable material");
    for (let index = group.start; index < group.start + group.count; index++) target.push(bodyIndex.getX(index));
  }
  const materialOne = indicesByMaterial[1]!;
  const detailVertexOffset = groupedBody.getAttribute("position").count;
  for (let index = 0; index < detailIndex.count; index++) materialOne.push(detailIndex.getX(index) + detailVertexOffset);

  merged.clearGroups();
  const orderedIndices: number[] = [];
  for (let materialIndex = 0; materialIndex < indicesByMaterial.length; materialIndex++) {
    const indices = indicesByMaterial[materialIndex]!;
    if (indices.length === 0) continue;
    merged.addGroup(orderedIndices.length, indices.length, materialIndex);
    for (const index of indices) orderedIndices.push(index);
  }
  merged.setIndex(orderedIndices);
  merged.computeBoundingBox();
  groupedBody.dispose();
  details.dispose();
  return merged;
}

/** Sills, floor courses and tile seams use the wall material in the existing building draw call. */
function facadeReliefGeometry(width: number, depth: number, height: number, relief: number): THREE.BufferGeometry {
  const unit = new THREE.BoxGeometry(1, 1, 1);
  const unitPositions = unit.getAttribute("position");
  const unitNormals = unit.getAttribute("normal");
  const unitUvs = unit.getAttribute("uv");
  const unitIndex = unit.getIndex();
  if (unitIndex === null) throw new Error("Facade detail template has no triangle indices");

  const positions: number[] = [];
  const normals: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const metresPerU = [FACADE_BAY_WIDTH_M * WINDOWS_PER_TILE, FACADE_BAY_WIDTH_M * WINDOWS_PER_TILE,
    FACADE_BAY_WIDTH_M, FACADE_BAY_WIDTH_M, FACADE_BAY_WIDTH_M * WINDOWS_PER_TILE, FACADE_BAY_WIDTH_M * WINDOWS_PER_TILE];
  const metresPerV = [FACADE_FLOOR_HEIGHT_M * WINDOWS_PER_TILE, FACADE_FLOOR_HEIGHT_M * WINDOWS_PER_TILE,
    FACADE_BAY_WIDTH_M, FACADE_BAY_WIDTH_M, FACADE_FLOOR_HEIGHT_M * WINDOWS_PER_TILE,
    FACADE_FLOOR_HEIGHT_M * WINDOWS_PER_TILE];
  const addBox = (size: readonly [number, number, number], center: readonly [number, number, number],
                  omitFaces?: readonly number[]): void => {
    const offset = positions.length / 3;
    for (let vertex = 0; vertex < unitPositions.count; vertex++) {
      const face = Math.floor(vertex / 4);
      const horizontal = face < 2 ? size[2] : size[0];
      const vertical = face < 2 ? size[1] : face < 4 ? size[2] : size[1];
      positions.push(unitPositions.getX(vertex) * size[0] + center[0],
        unitPositions.getY(vertex) * size[1] + center[1],
        unitPositions.getZ(vertex) * size[2] + center[2]);
      normals.push(unitNormals.getX(vertex), unitNormals.getY(vertex), unitNormals.getZ(vertex));
      uvs.push(unitUvs.getX(vertex) * horizontal / metresPerU[face]!,
        unitUvs.getY(vertex) * vertical / metresPerV[face]!);
    }
    for (let face = 0; face < 6; face++) {
      if (omitFaces?.includes(face)) continue;
      for (let index = face * 6; index < face * 6 + 6; index++) indices.push(unitIndex.getX(index) + offset);
    }
  };

  const addRingBand = (y: number, bandHeight: number): void => {
    const localY = y - height / 2;
    for (const sign of [-1, 1]) {
      addBox([width, bandHeight, relief], [0, localY, sign * (depth - relief) / 2]);
      addBox([relief, bandHeight, depth - relief * 2], [sign * (width - relief) / 2, localY, 0]);
    }
  };

  // These corner piers return the inset wall to the measured OSM envelope.
  for (const xSign of [-1, 1]) for (const zSign of [-1, 1]) {
    addBox([relief, height, relief], [xSign * (width - relief) / 2, 0, zSign * (depth - relief) / 2]);
  }

  // A shallow base and restrained floor bands make the repeated facade read as built floors.
  addRingBand(Math.min(.25, height / 3), Math.min(.28, height / 5));
  for (let floor = FACADE_FLOOR_HEIGHT_M; floor < height - .16; floor += FACADE_FLOOR_HEIGHT_M) {
    addRingBand(floor, .10);
  }

  // The atlas repeats every four bays; narrow piers cover the repeat seam without hiding windows.
  const tileWidth = FACADE_BAY_WIDTH_M * WINDOWS_PER_TILE;
  for (let x = -width / 2 + tileWidth; x < width / 2 - .2; x += tileWidth) {
    for (const sign of [-1, 1]) {
      addBox([.14, height, relief], [x, 0, sign * (depth - relief) / 2]);
    }
  }
  for (let z = -depth / 2 + tileWidth; z < depth / 2 - .2; z += tileWidth) {
    for (const sign of [-1, 1]) {
      addBox([relief, height, .14], [sign * (width - relief) / 2, 0, z]);
    }
  }

  // Repeated window sills follow the three metre bay and 3.5 metre floor scale of the atlas.
  const windowInset = Math.min(relief, .12);
  const sillY = .78;
  const addSills = (span: number, side: "front" | "side"): void => {
    if (span < WINDOW_SILL_WIDTH_M) return;
    for (let center = FACADE_BAY_WIDTH_M / 2; center <= span - WINDOW_SILL_WIDTH_M / 2; center += FACADE_BAY_WIDTH_M) {
      for (let floor = 0; floor + sillY + WINDOW_SILL_HEIGHT_M / 2 <= height; floor += FACADE_FLOOR_HEIGHT_M) {
        const position = center - span / 2;
        for (const sign of [-1, 1]) {
          if (side === "front") {
            addBox([WINDOW_SILL_WIDTH_M, WINDOW_SILL_HEIGHT_M, windowInset],
              [position, floor + sillY - height / 2, sign * (depth - windowInset) / 2], sign > 0 ? [3, 5] : [3, 4]);
          } else {
            addBox([windowInset, WINDOW_SILL_HEIGHT_M, WINDOW_SILL_WIDTH_M],
              [sign * (width - windowInset) / 2, floor + sillY - height / 2, position], sign > 0 ? [3, 1] : [3, 0]);
          }
        }
      }
    }
  };
  addSills(width, "front");
  addSills(depth, "side");

  unit.dispose();
  const detail = new THREE.BufferGeometry();
  detail.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  detail.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  detail.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  detail.setIndex(indices);
  detail.addGroup(0, indices.length, 1);
  detail.computeBoundingBox();
  return detail;
}

/** Crop only known tiles from the supplied BigCity atlas; keep physical window pitch. */
export function facadeTile(source: THREE.Texture, rectangle: readonly [number, number, number, number],
                           colorSpace: THREE.ColorSpace = THREE.SRGBColorSpace): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = rectangle[2]; canvas.height = rectangle[3];
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("Facade atlas canvas is unavailable");
  context.drawImage(source.image as CanvasImageSource, ...rectangle, 0, 0, canvas.width, canvas.height);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = colorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = 8;
  return texture;
}

export interface RoofPart {
  size: [number, number, number];
  position: [number, number, number];
  kind: "rim" | "plant" | "fan" | "duct";
}

function roofVariant(width: number, depth: number, height: number, styleSeed?: string | number): number {
  if (typeof styleSeed === "number" && Number.isFinite(styleSeed)) return Math.abs(Math.trunc(styleSeed)) % 3;
  const value = typeof styleSeed === "string" ? styleSeed : `${width.toFixed(2)}:${depth.toFixed(2)}:${height.toFixed(2)}`;
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  return (hash >>> 0) % 3;
}

/** All details stay within the declared OSM envelope, including their top surfaces. */
export function roofParts(width: number, depth: number, height: number, styleSeed?: string | number): { wallHeight: number; parts: RoofPart[] } {
  needsMeteredFacadePart(width, depth);
  if (!Number.isFinite(height) || height <= 0) throw new Error("Invalid building height");
  const allowance = Math.min(1.4, height * .12);
  const wallHeight = height - allowance;
  const rimHeight = Math.min(.9, allowance);
  const thickness = Math.min(.24, width * .12, depth * .12);
  const parts: RoofPart[] = [
    ...[-1, 1].map(sign => ({ kind: "rim" as const, size: [width, rimHeight, thickness] as [number, number, number],
      position: [0, wallHeight + rimHeight / 2, sign * (depth - thickness) / 2] as [number, number, number] })),
    ...[-1, 1].map(sign => ({ kind: "rim" as const, size: [thickness, rimHeight, depth - thickness * 2] as [number, number, number],
      position: [sign * (width - thickness) / 2, wallHeight + rimHeight / 2, 0] as [number, number, number] })),
  ];
  if (width >= 8 && depth >= 8 && allowance >= 1.1) {
    const variant = roofVariant(width, depth, height, styleSeed);
    const roomSize: [number, number, number] = [Math.min(2.8, width * .35), Math.min(.8, allowance * .58), Math.min(2.2, depth * .34)];
    const unitSize: [number, number, number] = [Math.min(1.45, width * .19), Math.min(.58, allowance * .42), Math.min(1.1, depth * .18)];
    const fanSize: [number, number, number] = [Math.min(.72, unitSize[0] * .58), .08, Math.min(.72, unitSize[2] * .66)];
    const equipmentBase = wallHeight + .08;
    const unitFan = (x: number, z: number): void => {
      parts.push({ kind: "plant", size: unitSize, position: [x, equipmentBase + unitSize[1] / 2, z] });
      parts.push({ kind: "fan", size: fanSize, position: [x, equipmentBase + unitSize[1] + fanSize[1] / 2, z] });
    };
    const room = (x: number, z: number): void => {
      parts.push({ kind: "plant", size: roomSize, position: [x, equipmentBase + roomSize[1] / 2, z] });
    };

    if (variant === 0) {
      const roomX = -width * .2;
      const unitX = width * .19;
      const roomZ = -depth * .18;
      const unitZ = depth * .18;
      room(roomX, roomZ);
      unitFan(unitX, unitZ);
      unitFan(unitX, roomZ);
      const gap = unitX - unitSize[0] / 2 - (roomX + roomSize[0] / 2);
      if (gap > .18) parts.push({ kind: "duct", size: [gap, .18, .16],
        position: [(roomX + roomSize[0] / 2 + unitX - unitSize[0] / 2) / 2, equipmentBase + .12, roomZ] });
    } else if (variant === 1) {
      room(width * .2, -depth * .18);
      const unitX = -width * .22;
      const zPositions = [-depth * .25, 0, depth * .25];
      zPositions.forEach(z => unitFan(unitX, z));
      const ductLength = Math.max(0, depth * .5 - unitSize[2]);
      if (ductLength > .18) parts.push({ kind: "duct", size: [.16, .18, ductLength],
        position: [unitX + unitSize[0] / 2 + .08, equipmentBase + .12, 0] });
    } else {
      room(0, -depth * .22);
      const unitZ = depth * .2;
      unitFan(-width * .25, unitZ);
      unitFan(width * .25, unitZ);
      const ductLength = Math.max(0, width * .5 - unitSize[0]);
      if (ductLength > .18) parts.push({ kind: "duct", size: [ductLength, .18, .16],
        position: [0, equipmentBase + .12, unitZ] });
    }
  }
  return { wallHeight, parts };
}

export function createMeteredBuilding(size: THREE.Vector3, materials: readonly THREE.Material[], deferRoof = false,
                                     styleSeed?: string | number, facadeRelief = true): THREE.Group {
  if (materials.length !== 5) throw new Error("Metered building requires facade, stone, roof, equipment and fan materials");
  const plan = roofParts(size.x, size.z, size.y, styleSeed);
  const group = new THREE.Group();
  const body = new THREE.Mesh(meteredFacadeGeometry(size.x, size.z, plan.wallHeight, facadeRelief), materials.slice(0, 3));
  body.position.y = plan.wallHeight / 2;
  group.add(body);
  if (deferRoof) {
    body.castShadow = body.receiveShadow = true;
    group.userData.deferredRoof = { parts: plan.parts, materials };
    return group;
  }
  for (const [materialIndex, kinds] of [[1, ["rim"]], [3, ["plant", "duct"]], [4, ["fan"]]] as const) {
    const fan = materialIndex === 4;
    const pieces = plan.parts.filter(part => (kinds as readonly RoofPart["kind"][]).includes(part.kind)).map(part => {
      const geometry = fan
        ? new THREE.CylinderGeometry(part.size[0] / 2, part.size[0] / 2, part.size[1], 12)
        : new THREE.BoxGeometry(...part.size);
      geometry.translate(...part.position);
      return geometry;
    });
    if (!pieces.length) continue;
    const geometry = mergeGeometries(pieces);
    pieces.forEach(piece => piece.dispose());
    if (geometry === null) throw new Error("Could not batch rooftop geometry");
    group.add(new THREE.Mesh(geometry, materials[materialIndex]));
  }
  group.traverse(node => {
    if (node instanceof THREE.Mesh) { node.castShadow = true; node.receiveShadow = true; }
  });
  return group;
}

interface RoofInstance { owner: THREE.Object3D; matrix: THREE.Matrix4 }

/** Three shared instance batches replace thousands of per-building roof draw calls. */
export function attachMeteredRoofs(buildings: THREE.Group): void {
  buildings.updateMatrixWorld(true);
  const batches = new Map<THREE.Material, { fan: boolean; instances: RoofInstance[] }>();
  for (const owner of buildings.children) {
    const roof = owner.userData.deferredRoof as { parts: RoofPart[]; materials: THREE.Material[] } | undefined;
    if (!roof) continue;
    for (const part of roof.parts) {
      const material = roof.materials[part.kind === "rim" ? 1 : part.kind === "fan" ? 4 : 3]!;
      let batch = batches.get(material);
      if (!batch) { batch = { fan: part.kind === "fan", instances: [] }; batches.set(material, batch); }
      const local = new THREE.Matrix4().compose(new THREE.Vector3(...part.position),
        new THREE.Quaternion(), new THREE.Vector3(...part.size));
      batch.instances.push({ owner, matrix: owner.matrix.clone().multiply(local) });
    }
  }
  for (const [material, batch] of batches) {
    const mesh = new THREE.InstancedMesh(batch.fan ? new THREE.CylinderGeometry(.5, .5, 1, 12)
      : new THREE.BoxGeometry(1, 1, 1), material, batch.instances.length);
    mesh.name = "Metered rooftop details";
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.userData.roofInstances = batch.instances;
    batch.instances.forEach((item, index) => mesh.setMatrixAt(index, item.matrix));
    mesh.instanceMatrix.needsUpdate = true;
    buildings.add(mesh);
  }
}

export function syncMeteredRoofVisibility(buildings: THREE.Group): void {
  const hidden = new THREE.Matrix4().makeScale(0, 0, 0);
  for (const node of buildings.children) {
    const instances = node.userData.roofInstances as RoofInstance[] | undefined;
    if (!(node instanceof THREE.InstancedMesh) || !instances) continue;
    instances.forEach((item, index) => node.setMatrixAt(index, item.owner.visible ? item.matrix : hidden));
    node.instanceMatrix.needsUpdate = true;
  }
}
