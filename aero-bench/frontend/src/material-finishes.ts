import * as THREE from "three";

export interface FinishPreset {
  id: string;
  label: string;
  family: string;
  color_url?: string | null;
  normal_url?: string | null;
  orm_url?: string | null;
  source_path?: string;
}

export interface FinishSettings {
  color: string;
  roughness: number;
  metalness: number;
  repeat: number;
}

export const basicFinishes: FinishPreset[] = [
  { id: "paint", label: "喷涂漆面", family: "基础涂层" },
  { id: "carbon", label: "碳纤维", family: "基础涂层" },
  { id: "plastic", label: "哑光塑料", family: "基础涂层" },
  { id: "rubber", label: "橡胶", family: "基础涂层" },
  { id: "glass", label: "半透明玻璃", family: "基础涂层" },
];

export function defaultFinishSettings(preset: FinishPreset): FinishSettings {
  switch (preset.id) {
    case "paint": return { color: "#257998", roughness: 0.3, metalness: 0.18, repeat: 2 };
    case "carbon": return { color: "#333c42", roughness: 0.56, metalness: 0.08, repeat: 3 };
    case "plastic": return { color: "#596d72", roughness: 0.74, metalness: 0, repeat: 2 };
    case "rubber": return { color: "#242b30", roughness: 0.88, metalness: 0, repeat: 2 };
    case "glass": return { color: "#a9d8e3", roughness: 0.17, metalness: 0.08, repeat: 2 };
    case "custom:Glass": return { color: "#ffffff", roughness: 0.28, metalness: 0.08, repeat: 2 };
    default: return { color: "#ffffff", roughness: 1, metalness: 1, repeat: 2 };
  }
}

function carbonTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("无法创建碳纤维纹理画布");
  context.fillStyle = "#363c40";
  context.fillRect(0, 0, 256, 256);
  for (let y = 0; y < 256; y += 16) for (let x = 0; x < 256; x += 16) {
    const alternate = (x / 16 + y / 16) % 2 === 0;
    context.fillStyle = alternate ? "#657078" : "#232b30";
    context.fillRect(x, y, 16, 8);
    context.fillStyle = alternate ? "#252d32" : "#59666e";
    context.fillRect(x, y + 8, 16, 8);
    context.fillStyle = "rgba(255,255,255,.08)";
    context.fillRect(x, y, 1, 16);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function configureTexture(texture: THREE.Texture, repeat: number, color: boolean): THREE.Texture {
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeat, repeat);
  if (color) texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

export async function createFinishMaterial(
  preset: FinishPreset,
  settings: FinishSettings,
  textureLoader = new THREE.TextureLoader(),
): Promise<THREE.MeshStandardMaterial> {
  const loaded: THREE.Texture[] = [];
  try {
    let colorMap: THREE.Texture | null = null;
    let normalMap: THREE.Texture | null = null;
    let ormMap: THREE.Texture | null = null;
    if (preset.id === "carbon") {
      colorMap = carbonTexture();
      loaded.push(colorMap);
    } else {
      if (preset.color_url) { colorMap = await textureLoader.loadAsync(preset.color_url); loaded.push(colorMap); }
      if (preset.normal_url) { normalMap = await textureLoader.loadAsync(preset.normal_url); loaded.push(normalMap); }
      if (preset.orm_url) { ormMap = await textureLoader.loadAsync(preset.orm_url); loaded.push(ormMap); }
    }
    if (colorMap) configureTexture(colorMap, settings.repeat, true);
    if (normalMap) configureTexture(normalMap, settings.repeat, false);
    if (ormMap) configureTexture(ormMap, settings.repeat, false);
    const glass = preset.id === "glass" || preset.id === "custom:Glass";
    const material = new THREE.MeshStandardMaterial({
      name: `预览涂装 · ${preset.label}`,
      color: new THREE.Color(settings.color),
      map: colorMap,
      normalMap,
      roughnessMap: ormMap,
      metalnessMap: ormMap,
      roughness: settings.roughness,
      metalness: settings.metalness,
      transparent: glass,
      opacity: glass ? 0.68 : 1,
      depthWrite: !glass,
      side: THREE.DoubleSide,
    });
    return material;
  } catch (error) {
    for (const texture of loaded) texture.dispose();
    throw error;
  }
}

export function materialTextures(material: THREE.Material): THREE.Texture[] {
  return Object.values(material).filter((value): value is THREE.Texture => value instanceof THREE.Texture);
}
