import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { basicFinishes, createFinishMaterial, defaultFinishSettings, materialTextures, type FinishPreset, type FinishSettings } from "./material-finishes";
import "./asset-library.css";

type Category = "vehicle" | "character" | "uav" | "city";
type CatalogScope = "uavModels" | "vehicle" | "character" | "uavArchives" | "materials" | "city" | "all";
type AssetStatus = "ready" | "available" | "downloaded" | "pending" | "partial";
type AssetKind = "model" | "archive" | "image" | "scene" | "texture" | "package";

interface AssetEntry {
  id: string;
  category: Exclude<Category, "all">;
  subgroup: string;
  title: string;
  kind: AssetKind;
  status: AssetStatus;
  format: string;
  source_path: string;
  origin_path?: string;
  converted_model_id?: string | null;
  note: string;
  model_url?: string;
  preview_exposure?: number;
  material_origin?: string;
  preview_url?: string | null;
  reference_preview_url?: string | null;
  image_url?: string | null;
  texture_alias_url?: string;
  material_overrides?: Record<string, {
    color?: string;
    colorLinear?: [number, number, number];
    alphaMap?: null;
    alphaTest?: number;
    transparent?: boolean;
    depthWrite?: boolean;
    normalMap?: string;
    metalness?: number;
    roughness?: number;
  }>;
  animation_clip?: string;
  bytes?: number | null;
  details?: Record<string, number>;
}

interface AssetCatalog {
  schema_version: string;
  entries: AssetEntry[];
  finishes: FinishPreset[];
  summary: {
    browser_models: number;
    uav_archives_downloaded: number;
    uav_archives_pending: number;
    uav_archives_partial: number;
    uav_preview_images: number;
    texture_images: number;
    city_packs: number;
  };
}

const categoryLabels: Record<CatalogScope, string> = { uavModels: "无人机与配件 3D", vehicle: "车辆模型", character: "人物角色", uavArchives: "无人机源包图片", materials: "材质与贴图", city: "城市资产", all: "全部素材" };
const assetCategoryLabels: Record<Category, string> = { vehicle: "车辆模型", character: "人物角色", uav: "无人机与配件", city: "城市资产" };
const categoryDescriptions: Record<CatalogScope, string> = {
  uavModels: "可旋转、缩放的 GLB 模型。材质来源逐项标明，详情附有源包展示图供对照。",
  vehicle: "可旋转查看车辆模型；材质来源和源包位置在详情中标明。",
  character: "可旋转查看人物角色。角色动作是否可播放以模型实际包含的动画为准。",
  uavArchives: "290 个无人机 ZIP 和 292 张源包图片逐项列出。图片可放大查看；已转换的型号可以旋转。",
  materials: "BigCity、OSM2World 和新增 Unity 包的纹理逐项列出；3D 模型可在右侧试用通用涂装。",
  city: "BigCity、Urban Traffic 和 C2239 的城市资源可按目录查看。模型可旋转，纹理可放大；Unity 灯光未完整还原。",
  all: "模型、源包展示图和城市目录都在此列出，状态逐项标明。",
};
const statusLabels: Record<AssetStatus, string> = {
  ready: "3D 可预览", available: "本地可用", downloaded: "已下载源包", pending: "待下载", partial: "文件未完整",
};
const kindLabels: Record<AssetKind, string> = {
  model: "可旋转 3D 模型", archive: "CAD 源包与展示图", image: "源包展示图", scene: "城市场景", texture: "纹理图片或目录", package: "源工程包",
};

function required<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing asset library element: ${id}`);
  return element as T;
}

const elements = {
  categories: required<HTMLElement>("category-nav"),
  subgroups: required<HTMLElement>("subgroup-nav"),
  grid: required<HTMLElement>("asset-grid"),
  sectionTitle: required<HTMLElement>("section-title"),
  sectionDescription: required<HTMLElement>("section-description"),
  summary: required<HTMLElement>("inventory-summary"),
  resultCount: required<HTMLElement>("result-count"),
  search: required<HTMLInputElement>("asset-search"),
  statusFilter: required<HTMLSelectElement>("status-filter"),
  refresh: required<HTMLButtonElement>("refresh-catalog"),
  loadMore: required<HTMLButtonElement>("load-more"),
  stage: required<HTMLElement>("preview-stage"),
  panel: required<HTMLElement>("preview-panel"),
  canvas: required<HTMLCanvasElement>("preview-canvas"),
  image: required<HTMLImageElement>("preview-image"),
  placeholder: required<HTMLElement>("preview-placeholder"),
  loading: required<HTMLElement>("preview-loading"),
  mode: required<HTMLElement>("preview-mode"),
  hint: required<HTMLElement>("interaction-hint"),
  reset: required<HTMLButtonElement>("reset-view"),
  expand: required<HTMLButtonElement>("expand-preview"),
  materialToggle: required<HTMLButtonElement>("material-toggle"),
  materialPanel: required<HTMLElement>("material-panel"),
  materialTarget: required<HTMLSelectElement>("material-target"),
  materialPreset: required<HTMLSelectElement>("material-preset"),
  materialColor: required<HTMLInputElement>("material-color"),
  materialRoughness: required<HTMLInputElement>("material-roughness"),
  materialMetalness: required<HTMLInputElement>("material-metalness"),
  materialRepeat: required<HTMLInputElement>("material-repeat"),
  materialApply: required<HTMLButtonElement>("material-apply"),
  materialRestore: required<HTMLButtonElement>("material-restore"),
  materialStatus: required<HTMLElement>("material-status"),
  detailCategory: required<HTMLElement>("detail-category"),
  detailTitle: required<HTMLElement>("detail-title"),
  detailNote: required<HTMLElement>("detail-note"),
  openModel: required<HTMLButtonElement>("open-converted-model"),
  sourceReference: required<HTMLElement>("source-reference"),
  sourceReferenceLink: required<HTMLAnchorElement>("source-reference-link"),
  sourceReferenceImage: required<HTMLImageElement>("source-reference-image"),
  detailFields: required<HTMLElement>("detail-fields"),
};

let catalog: AssetCatalog | null = null;
let category: CatalogScope = "uavModels";
let subgroup = "all";
let selectedId = "model:holybro-x500";
let preview: ModelPreview | null = null;
let selectionSerial = 0;
let visibleLimit = 80;
let finishes: FinishPreset[] = [...basicFinishes];

function currentFinish(): FinishPreset {
  const chosen = finishes.find(item => item.id === elements.materialPreset.value);
  if (!chosen) throw new Error(`未知材质：${elements.materialPreset.value}`);
  return chosen;
}

function setFinishDefaults(): void {
  const settings = defaultFinishSettings(currentFinish());
  elements.materialColor.value = settings.color;
  elements.materialRoughness.value = String(settings.roughness);
  elements.materialMetalness.value = String(settings.metalness);
  elements.materialRepeat.value = String(settings.repeat);
}

function renderFinishOptions(): void {
  const groups = new Map<string, HTMLOptGroupElement>();
  elements.materialPreset.replaceChildren();
  for (const finish of finishes) {
    const groupName = finish.family === "基础涂层" ? "基础涂层" : finish.family === "cc0textures" ? "OSM2World · PBR 贴图" : "OSM2World · 自定义贴图";
    let group = groups.get(groupName);
    if (!group) {
      group = document.createElement("optgroup");
      group.label = groupName;
      groups.set(groupName, group);
      elements.materialPreset.append(group);
    }
    const option = document.createElement("option");
    option.value = finish.id;
    option.textContent = finish.label;
    group.append(option);
  }
  elements.materialPreset.value = "paint";
  setFinishDefaults();
}

function showMaterialControls(available: boolean): void {
  elements.materialToggle.disabled = !available;
  elements.materialPanel.hidden = true;
  elements.materialToggle.setAttribute("aria-expanded", "false");
  elements.materialToggle.textContent = "涂装试用";
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "未记录";
  const unit = bytes >= 1024 ** 3 ? "GB" : bytes >= 1024 ** 2 ? "MB" : "KB";
  const base = unit === "GB" ? 1024 ** 3 : unit === "MB" ? 1024 ** 2 : 1024;
  return `${(bytes / base).toFixed(bytes / base >= 10 ? 0 : 1)} ${unit}`;
}

function button(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const node = document.createElement("button");
  node.type = "button";
  node.className = className;
  node.textContent = label;
  node.addEventListener("click", onClick);
  return node;
}

function statusFor(entry: AssetEntry): string {
  if (entry.kind === "model") return "可旋转 3D";
  if (entry.kind === "image") return "图片文件";
  return statusLabels[entry.status];
}

function inScope(entry: AssetEntry, scope: CatalogScope): boolean {
  if (scope === "all") return true;
  if (scope === "uavModels") return entry.category === "uav" && entry.kind === "model";
  if (scope === "uavArchives") return entry.category === "uav" && (entry.kind === "archive" || entry.kind === "image");
  if (scope === "materials") return entry.kind === "texture";
  return entry.category === scope;
}

function renderCategoryNav(): void {
  if (!catalog) return;
  elements.categories.replaceChildren();
  for (const key of ["uavModels", "vehicle", "character", "uavArchives", "materials", "city", "all"] as const) {
    const count = catalog.entries.filter(entry => inScope(entry, key)).length;
    const node = button(categoryLabels[key], `category-item${category === key ? " active" : ""}`, () => {
      category = key;
      subgroup = "all";
      elements.statusFilter.value = "all";
      elements.search.value = "";
      renderList(true);
      selectFirstResult();
    });
    const value = document.createElement("span");
    value.textContent = String(count);
    node.append(value);
    elements.categories.append(node);
  }
}

function renderSummary(): void {
  if (!catalog) return;
  const facts: [string, string][] = [
    ["可旋转 3D", String(catalog.summary.browser_models)],
    ["无人机 ZIP 已下载", String(catalog.summary.uav_archives_downloaded)],
    ["无人机源包图片", String(catalog.summary.uav_preview_images)],
    ["纹理与标识图", String(catalog.summary.texture_images)],
    ["城市场景条目", String(catalog.summary.city_packs)],
  ];
  if (catalog.summary.uav_archives_pending) facts.push(["无人机 ZIP 待下载", String(catalog.summary.uav_archives_pending)]);
  if (catalog.summary.uav_archives_partial) facts.push(["文件未完整", String(catalog.summary.uav_archives_partial)]);
  elements.summary.replaceChildren(...facts.map(([label, value]) => {
    const node = document.createElement("div");
    node.className = "summary-item";
    const number = document.createElement("strong");
    number.textContent = value;
    const caption = document.createElement("span");
    caption.textContent = label;
    node.append(number, caption);
    return node;
  }));
}

function filteredEntries(): AssetEntry[] {
  if (!catalog) return [];
  const term = elements.search.value.trim().toLocaleLowerCase();
  const wantedStatus = elements.statusFilter.value;
  return catalog.entries.filter(entry =>
    inScope(entry, category)
    && (subgroup === "all" || entry.subgroup === subgroup)
    && (wantedStatus === "all" || entry.status === wantedStatus)
    && (!term || [entry.title, entry.subgroup, entry.source_path, entry.format].some(value => value.toLocaleLowerCase().includes(term))),
  ).sort((left, right) => {
    const order: Record<AssetStatus, number> = { ready: 0, available: 1, downloaded: 2, partial: 3, pending: 4 };
    const featured = ["model:holybro-x500", "model:quadcopter-40-preview", "model:scaneagle-preview", "model:vtol-fixedwing-preview"];
    const leftRank = category === "uavModels" ? featured.indexOf(left.id) : -1;
    const rightRank = category === "uavModels" ? featured.indexOf(right.id) : -1;
    if (leftRank !== rightRank) return (leftRank < 0 ? featured.length : leftRank) - (rightRank < 0 ? featured.length : rightRank);
    return order[left.status] - order[right.status] || left.title.localeCompare(right.title, "zh-CN");
  });
}

function renderSubgroups(): void {
  if (!catalog) return;
  const scoped = catalog.entries.filter(entry => inScope(entry, category));
  const names = [...new Set(scoped.map(entry => entry.subgroup))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  elements.subgroups.replaceChildren();
  for (const name of ["all", ...names]) {
    const count = name === "all" ? scoped.length : scoped.filter(entry => entry.subgroup === name).length;
    const label = name === "all" ? "全部目录" : name;
    elements.subgroups.append(button(`${label}  ${count}`, `subgroup-chip${subgroup === name ? " active" : ""}`, () => {
      subgroup = name;
      renderList(true);
      selectFirstResult();
    }));
  }
}

function renderStatusFilter(): void {
  const choices: [AssetStatus | "all", string][] = category === "uavArchives"
    ? [["all", "全部文件"], ["downloaded", "源包已下载"], ["available", "图片文件"], ["pending", "待下载"], ["partial", "文件未完整"]]
    : category === "city"
      ? [["all", "全部状态"], ["ready", "3D 可预览"], ["available", "本地可用"], ["downloaded", "源包已下载"]]
      : [["all", "全部状态"], ["ready", "3D 可预览"], ["available", "场景/纹理可用"], ["downloaded", "源包已下载"], ["pending", "待下载"], ["partial", "文件未完整"]];
  const selected = elements.statusFilter.value;
  elements.statusFilter.replaceChildren(...choices.map(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    return option;
  }));
  elements.statusFilter.value = choices.some(([value]) => value === selected) ? selected : "all";
  elements.statusFilter.hidden = category === "uavModels" || category === "vehicle" || category === "character" || category === "materials";
}

function cardFor(entry: AssetEntry): HTMLButtonElement {
  const node = button("", `asset-card${selectedId === entry.id ? " selected" : ""}`, () => selectEntry(entry.id, true));
  node.dataset.assetId = entry.id;
  node.setAttribute("aria-label", `${entry.title}，${statusFor(entry)}`);
  const media = document.createElement("div");
  media.className = `card-media card-media-${entry.kind}`;
  if (entry.preview_url) {
    const image = document.createElement("img");
    image.loading = "lazy";
    image.src = entry.preview_url;
    image.alt = "";
    media.append(image);
  } else {
    const symbol = document.createElement("span");
    symbol.className = "card-symbol";
    symbol.textContent = entry.kind === "scene" ? "▥" : entry.kind === "texture" ? "▦" : entry.kind === "model" ? "◇" : "▤";
    media.append(symbol);
  }
  const badge = document.createElement("span");
  badge.className = `card-badge status-${entry.status}`;
  badge.textContent = statusFor(entry);
  media.append(badge);
  if (entry.kind === "archive" || entry.kind === "image") {
    const type = document.createElement("span");
    type.className = "card-type";
    type.textContent = entry.kind === "image" ? "图片 · 非 3D" : "展示图 · 非 3D";
    media.append(type);
  }
  const copy = document.createElement("div");
  copy.className = "card-copy";
  const overline = document.createElement("span");
  overline.className = "card-overline";
  overline.textContent = `${entry.subgroup} / ${entry.format}`;
  const title = document.createElement("strong");
  title.textContent = entry.title;
  const detail = document.createElement("small");
  detail.textContent = entry.kind === "scene" ? `${entry.details?.objects ?? 0} 个对象 · ${entry.details?.batches ?? 0} 个网格批次`
    : entry.kind === "texture" && entry.details?.files ? `${entry.details.files} 个文件`
      : entry.bytes ? formatBytes(entry.bytes) : kindLabels[entry.kind];
  copy.append(overline, title, detail);
  node.append(media, copy);
  return node;
}

function renderList(resetLimit = false): void {
  if (!catalog) return;
  if (resetLimit) visibleLimit = 80;
  elements.sectionTitle.textContent = categoryLabels[category];
  elements.sectionDescription.textContent = categoryDescriptions[category];
  renderCategoryNav();
  renderSubgroups();
  renderStatusFilter();
  const entries = filteredEntries();
  elements.resultCount.textContent = `找到 ${entries.length} 项 · 已显示 ${Math.min(entries.length, visibleLimit)} 项`;
  elements.grid.replaceChildren(...entries.slice(0, visibleLimit).map(cardFor));
  elements.loadMore.hidden = entries.length <= visibleLimit;
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "empty-results";
    empty.textContent = "没有匹配的素材。可清除搜索或切换目录。";
    elements.grid.append(empty);
  }
}

function selectFirstResult(): void {
  const first = filteredEntries()[0];
  if (first) {
    selectEntry(first.id);
    return;
  }
  selectedId = "";
  ++selectionSerial;
  preview?.clear();
  elements.canvas.hidden = true;
  elements.image.hidden = true;
  elements.loading.hidden = true;
  elements.placeholder.hidden = false;
  elements.placeholder.textContent = "当前筛选没有可预览的素材。";
  elements.mode.textContent = "无结果";
  elements.reset.disabled = true;
  showMaterialControls(false);
  elements.detailCategory.textContent = "";
  elements.detailTitle.textContent = "无匹配素材";
  elements.detailNote.textContent = "请调整目录或筛选条件。";
  elements.openModel.hidden = true;
  elements.sourceReference.hidden = true;
  elements.detailFields.replaceChildren();
}

function field(label: string, value: string): HTMLElement {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  row.append(term, description);
  return row;
}

function selectEntry(id: string, fromCard = false): void {
  if (!catalog) return;
  const entry = catalog.entries.find(item => item.id === id);
  if (!entry) return;
  selectedId = id;
  elements.grid.querySelectorAll<HTMLButtonElement>(".asset-card").forEach(card => card.classList.remove("selected"));
  const selected = [...elements.grid.querySelectorAll<HTMLButtonElement>(".asset-card")].find(card => card.dataset.assetId === id);
  selected?.classList.add("selected");
  elements.detailCategory.textContent = `${assetCategoryLabels[entry.category]} / ${entry.subgroup}`;
  elements.detailTitle.textContent = entry.title;
  elements.detailNote.textContent = entry.note;
  elements.sourceReference.hidden = !entry.reference_preview_url;
  if (entry.reference_preview_url) {
    elements.sourceReferenceLink.href = entry.reference_preview_url;
    elements.sourceReferenceImage.src = entry.reference_preview_url;
    elements.sourceReferenceImage.alt = `${entry.title} 的源包展示图`;
  } else {
    elements.sourceReferenceLink.removeAttribute("href");
    elements.sourceReferenceImage.removeAttribute("src");
  }
  elements.openModel.hidden = !entry.converted_model_id;
  elements.openModel.onclick = entry.converted_model_id ? () => {
    category = "uavModels";
    subgroup = "all";
    elements.statusFilter.value = "all";
    elements.search.value = "";
    renderList(true);
    selectEntry(entry.converted_model_id!);
  } : null;
  const localPath = (value: string) => value.startsWith("validation/") ? value : `frontend/${value}`;
  const rows = [field("状态", statusFor(entry)), field("类型", kindLabels[entry.kind]), field("格式", entry.format), field("本地位置", localPath(entry.source_path))];
  if (entry.material_origin) rows.splice(3, 0, field("材质来源", entry.material_origin));
  if (entry.origin_path) rows.push(field("源包", localPath(entry.origin_path)));
  if (entry.bytes !== undefined && entry.bytes !== null) rows.splice(3, 0, field("大小", formatBytes(entry.bytes)));
  if (entry.details) {
    for (const [key, value] of Object.entries(entry.details)) {
      const labels: Record<string, string> = { meshes: "原始网格", batches: "网格批次", objects: "场景对象", textures: "引用纹理", files: "目录文件" };
      rows.push(field(labels[key] ?? key, String(value)));
    }
  }
  elements.detailFields.replaceChildren(...rows);
  void showPreview(entry);
  if (fromCard && window.matchMedia("(max-width: 900px)").matches) {
    elements.stage.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function showPreview(entry: AssetEntry): Promise<void> {
  const serial = ++selectionSerial;
  elements.loading.hidden = true;
  elements.canvas.hidden = true;
  elements.image.hidden = true;
  elements.placeholder.hidden = true;
  elements.reset.disabled = true;
  showMaterialControls(false);
  preview?.clear();
  if (entry.model_url) {
    elements.mode.textContent = "交互式 3D";
    elements.hint.textContent = "拖动旋转 · 滚轮缩放 · 点击涂装试用";
    elements.canvas.hidden = false;
    elements.loading.textContent = "正在加载模型…";
    elements.loading.hidden = false;
    try {
      preview ??= new ModelPreview(elements.canvas, elements.stage);
      await preview.load(entry.model_url, entry.preview_exposure ?? 1.25, entry.texture_alias_url, entry.animation_clip,
        entry.category === "character" && entry.format === "FBX", entry.material_overrides,
        entry.category === "vehicle" && entry.format === "FBX");
      if (serial !== selectionSerial) return;
      elements.reset.disabled = false;
      const targets = preview.materialTargets();
      elements.materialTarget.replaceChildren();
      const all = document.createElement("option");
      all.value = "all";
      all.textContent = `整个模型（${targets.length} 个部件）`;
      elements.materialTarget.append(all);
      for (const target of targets) {
        const option = document.createElement("option");
        option.value = target.id;
        option.textContent = target.label;
        elements.materialTarget.append(option);
      }
      elements.materialTarget.value = "all";
      elements.materialStatus.textContent = "选择材质后可试涂整个模型或单个部件。点击模型可选中部件。";
      showMaterialControls(true);
    } catch (error) {
      if (serial !== selectionSerial) return;
      elements.canvas.hidden = true;
      elements.placeholder.hidden = false;
      elements.placeholder.textContent = `模型加载失败：${error instanceof Error ? error.message : String(error)}`;
      elements.mode.textContent = "加载失败";
    } finally {
      if (serial === selectionSerial) elements.loading.hidden = true;
    }
    return;
  }
  if (entry.image_url || entry.preview_url) {
    elements.mode.textContent = entry.kind === "archive" || entry.kind === "image" ? "源包展示图 · 非 3D" : "纹理或资源图";
    elements.hint.textContent = entry.kind === "archive" ? "这是源包图片；已有 GLB 时可从详情打开。" : "图片可在全屏模式中放大查看。";
    elements.image.src = entry.image_url ?? entry.preview_url!;
    elements.image.alt = `${entry.title} 的${entry.kind === "archive" ? "源包预览图" : "纹理样图"}`;
    elements.loading.textContent = "正在加载图片…";
    elements.loading.hidden = false;
    try {
      await elements.image.decode();
      if (serial !== selectionSerial) return;
      elements.image.hidden = false;
    } catch (error) {
      if (serial !== selectionSerial) return;
      elements.placeholder.hidden = false;
      elements.placeholder.textContent = `图片加载失败：${error instanceof Error ? error.message : String(error)}`;
      elements.mode.textContent = "加载失败";
    } finally {
      if (serial === selectionSerial) elements.loading.hidden = true;
    }
    return;
  }
  elements.mode.textContent = "目录信息";
  elements.hint.textContent = "此项没有独立的 3D 模型预览。";
  elements.placeholder.hidden = false;
  elements.placeholder.textContent = entry.kind === "scene" ? "这是场景网格包，可在城市视图中查看当前场景。" : "当前没有可显示的模型或预览图。";
}

class ModelPreview {
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100);
  private readonly renderer: THREE.WebGLRenderer;
  private readonly controls: OrbitControls;
  private readonly loader = new GLTFLoader();
  private readonly fbxLoaders = new Map<string, FBXLoader>();
  private readonly resizeObserver: ResizeObserver;
  private readonly grid = new THREE.GridHelper(7, 14, 0x3b6376, 0x274152);
  private model: THREE.Object3D | null = null;
  private modelBounds: THREE.Box3 | null = null;
  private mixer: THREE.AnimationMixer | null = null;
  private loadToken = 0;
  private readonly meshTargets = new Map<string, THREE.Mesh>();
  private readonly targetIds = new Map<THREE.Mesh, string>();
  private readonly sourceMaterials = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
  private readonly sourceGeometries = new Map<THREE.Mesh, THREE.BufferGeometry>();
  private readonly finishOverrides = new Map<THREE.Mesh, THREE.MeshStandardMaterial>();
  private readonly finishMaterials = new Set<THREE.MeshStandardMaterial>();
  private selectionBox: THREE.Box3Helper | null = null;

  constructor(canvas: HTMLCanvasElement, stage: HTMLElement) {
    this.scene.background = new THREE.Color(0x101d2a);
    this.scene.add(new THREE.HemisphereLight(0xd9efff, 0x324353, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.5);
    key.position.set(4, 7, 5);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x7ac9e8, 1.1);
    fill.position.set(-5, 2, -4);
    this.scene.add(fill);
    this.grid.position.y = -1.35;
    this.scene.add(this.grid);
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.25;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    const room = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(room).texture;
    this.scene.environmentIntensity = 0.65;
    room.dispose();
    pmrem.dispose();
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.minDistance = 2;
    this.controls.maxDistance = 15;
    this.reset();
    this.resizeObserver = new ResizeObserver(() => this.resize(stage));
    this.resizeObserver.observe(stage);
    this.resize(stage);
  }

  private resize(stage: HTMLElement): void {
    const width = Math.max(stage.clientWidth, 1);
    const height = Math.max(stage.clientHeight, 1);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    if (this.modelBounds) {
      const offset = this.camera.position.clone().sub(this.controls.target);
      const required = this.fitDistance(offset.normalize());
      if (this.camera.position.distanceTo(this.controls.target) < required) {
        this.camera.position.copy(this.controls.target).addScaledVector(offset, required);
        this.controls.update();
      }
    }
    this.renderer.render(this.scene, this.camera);
  }

  private fitDistance(direction: THREE.Vector3): number {
    if (!this.modelBounds) return 4.8;
    const right = new THREE.Vector3().crossVectors(this.camera.up, direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction, right).normalize();
    const tanVertical = Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    const tanHorizontal = tanVertical * this.camera.aspect;
    let distance = 0;
    const min = this.modelBounds.min;
    const max = this.modelBounds.max;
    for (const x of [min.x, max.x]) for (const y of [min.y, max.y]) for (const z of [min.z, max.z]) {
      const corner = new THREE.Vector3(x, y, z).sub(this.controls.target);
      const depth = corner.dot(direction);
      distance = Math.max(distance, depth + Math.abs(corner.dot(right)) / tanHorizontal, depth + Math.abs(corner.dot(up)) / tanVertical);
    }
    return this.camera.aspect >= 1.2
      ? Math.max(Math.min(distance * 1.08, 4.9), 2.8)
      : Math.max(distance * 1.02, 2.8);
  }

  private async getFbxLoader(textureAliasUrl = "/models/bigcity/texture-aliases.json"): Promise<FBXLoader> {
    const cached = this.fbxLoaders.get(textureAliasUrl);
    if (cached) return cached;
    const response = await fetch(textureAliasUrl);
    if (!response.ok) throw new Error(`模型纹理映射读取失败：HTTP ${response.status}`);
    const aliases = await response.json() as Record<string, string>;
    const manager = new THREE.LoadingManager();
    manager.setURLModifier(url => {
      const file = decodeURIComponent(url.replaceAll("\\", "/").split("/").pop() ?? "").toLowerCase();
      return aliases[file] ?? url;
    });
    const loader = new FBXLoader(manager);
    this.fbxLoaders.set(textureAliasUrl, loader);
    return loader;
  }

  async load(url: string, exposure: number, textureAliasUrl?: string, animationClip?: string,
    restoreCharacterTextureColor = false, materialOverrides?: AssetEntry["material_overrides"],
    showVehicleLod0 = false): Promise<void> {
    const token = ++this.loadToken;
    const loaded = url.toLowerCase().endsWith(".fbx")
      ? await (await this.getFbxLoader(textureAliasUrl)).loadAsync(url)
      : await this.loader.loadAsync(url);
    const object = loaded instanceof THREE.Object3D ? loaded : loaded.scene;
    const animations = loaded instanceof THREE.Object3D ? loaded.animations : loaded.animations;
    const clip = animationClip ? animations.find(item => item.name === animationClip) : null;
    if (animationClip && !clip) throw new Error(`模型缺少声明的动作：${animationClip}`);
    if (token !== this.loadToken) {
      this.disposeObject(object);
      return;
    }
    if (showVehicleLod0) {
      let lod0Meshes = 0;
      object.traverse(child => {
        if (!(child instanceof THREE.Mesh)) return;
        if (/_LOD0$/i.test(child.name)) lod0Meshes++;
        else if (/_LOD[1-9]\d*$/i.test(child.name)) child.visible = false;
      });
      if (!lod0Meshes) throw new Error("车辆 FBX 缺少 LOD0 网格");
    }
    if (restoreCharacterTextureColor) {
      object.traverse(child => {
        if (!(child instanceof THREE.Mesh)) return;
        for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
          if (!("map" in material && "color" in material)) continue;
          const mapped = material as THREE.MeshStandardMaterial;
          if (mapped.map && mapped.color.r < 0.001 && mapped.color.g < 0.001 && mapped.color.b < 0.001) {
            mapped.color.setRGB(1, 1, 1);
          }
        }
      });
    }
    const reviewedNormals = new Map<string, THREE.Texture>();
    const retiredMaterials = new Set<THREE.Material>();
    for (const [name, spec] of Object.entries(materialOverrides ?? {})) {
      if (!spec.normalMap) continue;
      const normal = await new THREE.TextureLoader().loadAsync(spec.normalMap);
      normal.colorSpace = THREE.NoColorSpace;
      reviewedNormals.set(name, normal);
    }
    if (token !== this.loadToken) {
      this.disposeObject(object);
      for (const normal of reviewedNormals.values()) normal.dispose();
      return;
    }
    for (const [name, spec] of Object.entries(materialOverrides ?? {})) {
      let matched = false;
      object.traverse(child => {
        if (!(child instanceof THREE.Mesh)) return;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        for (let index = 0; index < materials.length; index++) {
          const material = materials[index];
          if (material.name !== name) continue;
          matched = true;
          if (!(material instanceof THREE.MeshPhongMaterial || material instanceof THREE.MeshStandardMaterial)) {
            throw new Error(`不支持恢复材质 ${name}：${material.type}`);
          }
          let mapped: THREE.MeshPhongMaterial | THREE.MeshStandardMaterial = material;
          if (spec.normalMap || spec.metalness !== undefined || spec.roughness !== undefined) {
            const source = material;
            const normalMap = reviewedNormals.get(name) ?? source.normalMap;
            if (normalMap) normalMap.flipY = source.map?.flipY ?? true;
            mapped = new THREE.MeshStandardMaterial({
              name: source.name, color: source.color.clone(), map: source.map,
              alphaMap: source.alphaMap, opacity: source.opacity, transparent: source.transparent,
              alphaTest: source.alphaTest, side: source.side, depthWrite: source.depthWrite,
              metalness: spec.metalness ?? (source instanceof THREE.MeshStandardMaterial ? source.metalness : 0),
              roughness: spec.roughness ?? (source instanceof THREE.MeshStandardMaterial ? source.roughness : 1),
              normalMap, emissive: source.emissive.clone(), emissiveMap: source.emissiveMap,
              vertexColors: source.vertexColors,
            });
            materials[index] = mapped;
            retiredMaterials.add(source);
          }
          if (spec.color !== undefined) mapped.color.set(spec.color);
          if (spec.colorLinear !== undefined) mapped.color.setRGB(...spec.colorLinear);
          if (spec.alphaMap === null) mapped.alphaMap = null;
          if (spec.alphaTest !== undefined) mapped.alphaTest = spec.alphaTest;
          if (spec.transparent !== undefined) mapped.transparent = spec.transparent;
          if (spec.depthWrite !== undefined) mapped.depthWrite = spec.depthWrite;
          mapped.needsUpdate = true;
        }
        child.material = Array.isArray(child.material) ? materials : materials[0];
      });
      if (!matched) throw new Error(`模型缺少声明的材质：${name}`);
    }
    for (const material of retiredMaterials) material.dispose();
    object.updateMatrixWorld(true);
    const preciseBounds = object.getObjectByProperty("isSkinnedMesh", true) !== undefined;
    const bounds = new THREE.Box3().setFromObject(object, preciseBounds);
    if (bounds.isEmpty()) throw new Error("模型没有可显示的几何体");
    const size = bounds.getSize(new THREE.Vector3());
    const max = Math.max(size.x, size.y, size.z);
    if (!Number.isFinite(max) || max <= 0) throw new Error("模型尺寸无效");
    this.clear();
    object.scale.multiplyScalar(3.1 / max);
    object.updateMatrixWorld(true);
    const scaled = new THREE.Box3().setFromObject(object, preciseBounds);
    object.position.sub(scaled.getCenter(new THREE.Vector3()));
    object.updateMatrixWorld(true);
    this.model = object;
    this.scene.add(object);
    if (clip) {
      this.mixer = new THREE.AnimationMixer(object);
      this.mixer.clipAction(clip).play();
    }
    let meshNumber = 0;
    object.traverse(child => {
      if (!(child instanceof THREE.Mesh) || !child.visible) return;
      const id = `mesh:${meshNumber++}`;
      const materialName = Array.isArray(child.material) ? child.material.map(item => item.name).filter(Boolean).join(" / ") : child.material.name;
      this.meshTargets.set(id, child);
      this.targetIds.set(child, id);
      this.sourceMaterials.set(child, child.material);
      child.userData.finishLabel = child.name || materialName || `部件 ${meshNumber}`;
    });
    this.modelBounds = new THREE.Box3().setFromObject(object, preciseBounds);
    this.grid.position.y = this.modelBounds.min.y - 0.04;
    this.renderer.toneMappingExposure = exposure;
    this.reset();
    let lastFrame = performance.now();
    this.renderer.setAnimationLoop(() => {
      const now = performance.now();
      this.mixer?.update(Math.min((now - lastFrame) / 1000, 0.1));
      lastFrame = now;
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  materialTargets(): { id: string; label: string }[] {
    return [...this.meshTargets].map(([id, mesh], index) => ({ id, label: `${index + 1}. ${String(mesh.userData.finishLabel)}` }));
  }

  pickTarget(clientX: number, clientY: number, canvas: HTMLCanvasElement): string | null {
    if (!this.model) return null;
    const rect = canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2((clientX - rect.left) / rect.width * 2 - 1, -(clientY - rect.top) / rect.height * 2 + 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(pointer, this.camera);
    const hit = raycaster.intersectObjects([...this.meshTargets.values()], false)[0];
    return hit ? this.targetIds.get(hit.object as THREE.Mesh) ?? null : null;
  }

  highlightTarget(id: string): void {
    this.clearSelectionBox();
    const mesh = this.meshTargets.get(id);
    if (!mesh) return;
    const bounds = new THREE.Box3().setFromObject(mesh);
    if (bounds.isEmpty()) return;
    this.selectionBox = new THREE.Box3Helper(bounds, 0xffcc6d);
    this.selectionBox.renderOrder = 100;
    this.scene.add(this.selectionBox);
  }

  private clearSelectionBox(): void {
    if (!this.selectionBox) return;
    this.scene.remove(this.selectionBox);
    this.selectionBox.geometry.dispose();
    for (const material of Array.isArray(this.selectionBox.material) ? this.selectionBox.material : [this.selectionBox.material]) material.dispose();
    this.selectionBox = null;
  }

  private ensureUv(mesh: THREE.Mesh): void {
    if (mesh.geometry.getAttribute("uv")) return;
    const original = mesh.geometry;
    const geometry = original.clone();
    const position = geometry.getAttribute("position");
    if (!position) throw new Error(`部件缺少顶点坐标：${mesh.name}`);
    geometry.computeBoundingBox();
    const bounds = geometry.boundingBox;
    if (!bounds) throw new Error(`部件尺寸无效：${mesh.name}`);
    const size = bounds.getSize(new THREE.Vector3());
    const spans = [size.x, size.y, size.z];
    const axes = [0, 1, 2].sort((a, b) => spans[b]! - spans[a]!);
    const firstAxis = axes[0]!;
    const secondAxis = axes[1]!;
    const minimum = [bounds.min.x, bounds.min.y, bounds.min.z];
    const coordinates = new Float32Array(position.count * 2);
    for (let index = 0; index < position.count; index++) {
      const point = [position.getX(index), position.getY(index), position.getZ(index)];
      coordinates[index * 2] = (point[firstAxis]! - minimum[firstAxis]!) / Math.max(spans[firstAxis]!, 1e-6);
      coordinates[index * 2 + 1] = (point[secondAxis]! - minimum[secondAxis]!) / Math.max(spans[secondAxis]!, 1e-6);
    }
    geometry.setAttribute("uv", new THREE.BufferAttribute(coordinates, 2));
    this.sourceGeometries.set(mesh, original);
    mesh.geometry = geometry;
  }

  async applyFinish(targetId: string, preset: FinishPreset, settings: FinishSettings): Promise<number> {
    if (!this.model) throw new Error("没有可涂装的 3D 模型");
    const targets = targetId === "all" ? [...this.meshTargets.values()] : [this.meshTargets.get(targetId)].filter((mesh): mesh is THREE.Mesh => mesh !== undefined);
    if (!targets.length) throw new Error(`找不到模型部件：${targetId}`);
    const token = this.loadToken;
    const material = await createFinishMaterial(preset, settings);
    if (token !== this.loadToken || !this.model) {
      this.disposeFinishMaterial(material);
      return 0;
    }
    if (material.map || material.normalMap) for (const mesh of targets) this.ensureUv(mesh);
    this.finishMaterials.add(material);
    for (const mesh of targets) {
      mesh.material = material;
      this.finishOverrides.set(mesh, material);
    }
    this.disposeUnusedFinishes();
    return targets.length;
  }

  restoreFinishes(): void {
    for (const [mesh, material] of this.sourceMaterials) mesh.material = material;
    for (const [mesh, geometry] of this.sourceGeometries) {
      if (mesh.geometry !== geometry) mesh.geometry.dispose();
      mesh.geometry = geometry;
    }
    this.sourceGeometries.clear();
    this.finishOverrides.clear();
    this.disposeUnusedFinishes();
    this.clearSelectionBox();
  }

  private disposeUnusedFinishes(): void {
    const active = new Set(this.finishOverrides.values());
    for (const material of this.finishMaterials) {
      if (active.has(material)) continue;
      this.disposeFinishMaterial(material);
      this.finishMaterials.delete(material);
    }
  }

  private disposeFinishMaterial(material: THREE.MeshStandardMaterial): void {
    for (const texture of new Set(materialTextures(material))) texture.dispose();
    material.dispose();
  }

  clear(): void {
    this.loadToken++;
    this.renderer.setAnimationLoop(null);
    this.restoreFinishes();
    if (this.model) {
      this.scene.remove(this.model);
      this.disposeObject(this.model);
    }
    this.model = null;
    this.modelBounds = null;
    this.mixer?.stopAllAction();
    this.mixer = null;
    this.meshTargets.clear();
    this.targetIds.clear();
    this.sourceMaterials.clear();
  }

  private disposeObject(object: THREE.Object3D): void {
    const geometries = new Set<THREE.BufferGeometry>();
    const materials = new Set<THREE.Material>();
    const textures = new Set<THREE.Texture>();
    object.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return;
      geometries.add(child.geometry);
      for (const material of Array.isArray(child.material) ? child.material : [child.material]) {
        materials.add(material);
        for (const texture of materialTextures(material)) textures.add(texture);
      }
    });
    for (const geometry of geometries) geometry.dispose();
    for (const material of materials) material.dispose();
    for (const texture of textures) texture.dispose();
  }

  reset(): void {
    this.controls.target.set(0, 0, 0);
    const direction = new THREE.Vector3(2.8, 1.8, 3.6).normalize();
    this.camera.position.copy(direction.multiplyScalar(this.fitDistance(direction)));
    this.controls.update();
  }
}

async function refreshCatalog(): Promise<void> {
  elements.refresh.disabled = true;
  elements.refresh.textContent = "读取中…";
  try {
    const response = await fetch("/asset-library/catalog.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    const loaded = await response.json() as AssetCatalog;
    if (loaded.schema_version !== "aero-bench.asset-library/v1" || !Array.isArray(loaded.entries) || !Array.isArray(loaded.finishes)) throw new Error("素材清单格式无效");
    catalog = loaded;
    finishes = [...basicFinishes, ...loaded.finishes];
    renderFinishOptions();
    renderSummary();
    renderList();
    const entry = catalog.entries.find(item => item.id === selectedId) ?? catalog.entries[0];
    if (entry) selectEntry(entry.id);
  } catch (error) {
    elements.sectionDescription.textContent = `无法读取素材清单：${error instanceof Error ? error.message : String(error)}`;
  } finally {
    elements.refresh.disabled = false;
    elements.refresh.textContent = "刷新目录";
  }
}

elements.search.addEventListener("input", () => { renderList(true); selectFirstResult(); });
elements.statusFilter.addEventListener("change", () => { renderList(true); selectFirstResult(); });
elements.loadMore.addEventListener("click", () => { visibleLimit += 80; renderList(); });
elements.refresh.addEventListener("click", () => void refreshCatalog());
elements.reset.addEventListener("click", () => preview?.reset());
elements.materialToggle.addEventListener("click", () => {
  if (elements.materialToggle.disabled) return;
  const expanded = elements.materialPanel.hidden;
  elements.materialPanel.hidden = !expanded;
  elements.materialToggle.setAttribute("aria-expanded", String(expanded));
  elements.materialToggle.textContent = expanded ? "收起涂装" : "涂装试用";
});
elements.materialPreset.addEventListener("change", setFinishDefaults);
elements.materialTarget.addEventListener("change", () => preview?.highlightTarget(elements.materialTarget.value));
elements.materialApply.addEventListener("click", async () => {
  if (!preview) return;
  const serial = selectionSerial;
  elements.materialApply.disabled = true;
  elements.materialStatus.textContent = "正在加载并应用材质…";
  try {
    const count = await preview.applyFinish(elements.materialTarget.value, currentFinish(), {
      color: elements.materialColor.value,
      roughness: Number(elements.materialRoughness.value),
      metalness: Number(elements.materialMetalness.value),
      repeat: Number(elements.materialRepeat.value),
    });
    if (serial === selectionSerial && count) elements.materialStatus.textContent = `已试涂 ${count} 个部件；源文件未改动。`;
  } catch (error) {
    if (serial === selectionSerial) elements.materialStatus.textContent = `材质加载失败：${error instanceof Error ? error.message : String(error)}`;
  } finally {
    elements.materialApply.disabled = false;
  }
});
elements.materialRestore.addEventListener("click", () => {
  preview?.restoreFinishes();
  elements.materialTarget.value = "all";
  elements.materialStatus.textContent = "已还原当前模型的入库外观。";
});
let pointerStart: { id: number; x: number; y: number } | null = null;
elements.canvas.addEventListener("pointerdown", event => {
  pointerStart = { id: event.pointerId, x: event.clientX, y: event.clientY };
});
elements.canvas.addEventListener("pointerup", event => {
  if (!pointerStart || event.pointerId !== pointerStart.id) return;
  const moved = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y);
  pointerStart = null;
  if (moved > 5 || elements.materialPanel.hidden || !preview) return;
  const targetId = preview.pickTarget(event.clientX, event.clientY, elements.canvas);
  if (!targetId) return;
  elements.materialTarget.value = targetId;
  preview.highlightTarget(targetId);
  elements.materialStatus.textContent = `已选中 ${elements.materialTarget.selectedOptions[0]?.textContent ?? "部件"}，可单独试涂。`;
});
function setExpanded(expanded: boolean): void {
  elements.panel.classList.toggle("preview-expanded", expanded);
  document.body.classList.toggle("preview-open", expanded);
  elements.expand.textContent = expanded ? "退出全屏" : "全屏预览";
  elements.expand.setAttribute("aria-pressed", String(expanded));
}
elements.expand.addEventListener("click", () => setExpanded(!elements.panel.classList.contains("preview-expanded")));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && elements.panel.classList.contains("preview-expanded")) setExpanded(false);
});
void refreshCatalog();
