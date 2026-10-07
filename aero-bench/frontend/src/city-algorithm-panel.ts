import type { CityWorkspaceConfig } from "./city-workspace-config";

type Parameters = CityWorkspaceConfig["algorithms"]["parameters"];

const DIGEST_IMAGE = /^\S+@sha256:[0-9a-f]{64}$/;

function addNote(parent: HTMLElement, text: string): HTMLParagraphElement {
  const note = document.createElement("p");
  note.className = "studio-note";
  note.textContent = text;
  parent.append(note);
  return note;
}

function addField(parent: HTMLElement, title: string, control: HTMLElement): void {
  const label = document.createElement("label");
  label.className = "studio-field";
  const caption = document.createElement("span");
  caption.textContent = title;
  label.append(caption, control);
  parent.append(label);
}

function addSelect<T extends string>(
  parent: HTMLElement,
  title: string,
  value: T,
  options: readonly (readonly [T, string])[],
  onSelect: (value: T) => void,
): void {
  const select = document.createElement("select");
  select.setAttribute("aria-label", title);
  for (const [optionValue, label] of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = label;
    select.append(option);
  }
  select.value = value;
  select.addEventListener("change", () => onSelect(select.value as T));
  addField(parent, title, select);
}

function parseParameters(text: string): Parameters {
  const value: unknown = JSON.parse(text);
  if (value === null || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).some(key => key.length === 0)
      || Object.values(value).some(item =>
        typeof item !== "string" && typeof item !== "boolean"
        && (typeof item !== "number" || !Number.isFinite(item)))) {
    throw new Error("参数须为 JSON 对象，键不能为空，值只能是字符串、有限数字或布尔值。");
  }
  return value as Parameters;
}

function roleDescription(mode: CityWorkspaceConfig["algorithms"]["mode"]): string {
  return mode === "centralized"
    ? "拟定角色：中心调度器读取获授权订单池、机队公开状态及空域和设施摘要，统一作出分配；单机只接收获授权任务和自身状态。具体可见范围待后端契约确认。"
    : "拟定角色：各机载代理读取获授权订单和自身状态，经通信接口协商；默认不读取其他机体的私有状态或环境私有真值。具体可见范围待后端契约确认。";
}

/** Draft-only algorithm choices and the currently missing deployment contracts. */
export function renderCityAlgorithmPanel(
  root: HTMLElement,
  config: CityWorkspaceConfig,
  onChange: (next: CityWorkspaceConfig) => void,
): void {
  let currentConfig = config;
  const emit = (next: CityWorkspaceConfig): void => {
    currentConfig = next;
    onChange(next);
  };

  const card = document.createElement("section");
  card.className = "studio-card";
  const heading = document.createElement("h2");
  heading.textContent = "调度算法与部署配置";
  card.append(heading);
  addNote(card, "所有内置选项均为算法配置模板，尚未部署。此草稿不会生成 Run ID 或执行算法。");

  const algorithmGrid = document.createElement("div");
  algorithmGrid.className = "studio-grid";
  addSelect(algorithmGrid, "决策方式", config.algorithms.mode, [
    ["centralized", "中心式调度"],
    ["distributed", "分布式协商"],
  ], mode => {
    emit({ ...currentConfig, algorithms: { ...currentConfig.algorithms, mode } });
    roleNote.textContent = roleDescription(mode);
  });
  addSelect(algorithmGrid, "订单分配", config.algorithms.assignment, [
    ["greedy", "贪心 · 算法配置模板，尚未部署"],
    ["auction", "竞价 · 算法配置模板，尚未部署"],
    ["min_cost_flow", "最小费用流 · 算法配置模板，尚未部署"],
    ["external", "外部算法 · 待接入"],
  ], assignment => emit({ ...currentConfig, algorithms: { ...currentConfig.algorithms, assignment } }));
  addSelect(algorithmGrid, "航路规划", config.algorithms.routing, [
    ["astar", "A* · 算法配置模板，尚未部署"],
    ["rrt_star", "RRT* · 算法配置模板，尚未部署"],
    ["external", "外部算法 · 待接入"],
  ], routing => emit({ ...currentConfig, algorithms: { ...currentConfig.algorithms, routing } }));
  addSelect(algorithmGrid, "能源策略", config.algorithms.energy, [
    ["reserve_threshold", "预留电量阈值 · 算法配置模板，尚未部署"],
    ["external", "外部算法 · 待接入"],
  ], energy => emit({ ...currentConfig, algorithms: { ...currentConfig.algorithms, energy } }));
  card.append(algorithmGrid);
  const roleNote = addNote(card, roleDescription(config.algorithms.mode));

  const params = document.createElement("textarea");
  params.setAttribute("aria-label", "算法参数 JSON");
  params.rows = 5;
  params.spellcheck = false;
  params.value = JSON.stringify(config.algorithms.parameters, null, 2);
  addField(card, "算法参数 JSON", params);
  const paramsNote = addNote(card, "键不能为空；值只能是字符串、有限数字或布尔值。修改后离开输入框保存。");
  paramsNote.setAttribute("aria-live", "polite");

  const deploymentGrid = document.createElement("div");
  deploymentGrid.className = "studio-grid";
  addSelect(deploymentGrid, "执行器", config.deployment.executor, [
    ["docker_reference", "Docker Reference Executor"],
    ["kubernetes_cluster", "Kubernetes Cluster Executor"],
  ], executor => {
    emit({ ...currentConfig, deployment: { ...currentConfig.deployment, executor } });
    executorNote.textContent = executorRequirement(executor);
  });
  const image = document.createElement("input");
  image.type = "text";
  image.setAttribute("aria-label", "镜像 digest 引用");
  image.placeholder = "registry.example/agent@sha256:<64 位小写十六进制>";
  image.value = config.deployment.imageRef;
  addField(deploymentGrid, "算法工作负载镜像", image);
  card.append(deploymentGrid);
  const imageNote = addNote(card, "镜像未指定；需填写 digest 固定的 OCI 镜像引用。");
  imageNote.setAttribute("aria-live", "polite");
  const executorNote = addNote(card, executorRequirement(config.deployment.executor));

  const apiHeading = document.createElement("h3");
  apiHeading.textContent = "拟定 API 与接入状态";
  card.append(apiHeading);
  const apis = document.createElement("div");
  apis.className = "studio-grid";
  for (const [name, operations, status] of [
    ["订单", "查询 / 领取 / 接受 / 拒绝 / 指派", "待接入：城市物流订单后端尚不存在。"],
    ["航路", "提议 / 验证 / 提交", "待接入：本工作区尚无航路服务 API。"],
    ["起降", "起飞 / 降落", "待接入：现有巡检执行链有起降命令，本草稿尚未映射。"],
    ["充电泊位", "预约 / 开始 / 停止", "待接入：充电后端尚不存在。"],
    ["通信", "发送 / 接收", "待接入：本工作区尚无通信 API。"],
    ["状态", "运行 / 机体 / 订单状态查询", "部分现有：ControlClient 可查已存在 Run 的状态；机体和订单查询待接入。"],
  ] as const) {
    const row = document.createElement("div");
    row.className = "studio-row";
    const label = document.createElement("strong");
    label.textContent = `${name}：${operations}`;
    row.append(label);
    addNote(row, status);
    apis.append(row);
  }
  card.append(apis);

  const checkHeading = document.createElement("h3");
  checkHeading.textContent = "部署前置检查";
  card.append(checkHeading);
  addNote(card, "需先将草稿映射到严格 Spec 和不可变 ResolvedRun，完成 Provider 能力与可行性预检，并核对实际镜像、版本和证据。城市物流与充电后端仍需接入。");
  addNote(card, "现有 ControlClient 只可从 catalog 启动预解析的 Run ID，并查询或控制该运行；它不能编译本草稿。下方仅导出配置供后端接入。");

  const actions = document.createElement("div");
  actions.className = "studio-row";
  const exportButton = document.createElement("button");
  exportButton.type = "button";
  exportButton.className = "studio-button";
  exportButton.textContent = "导出配置供后端接入";
  actions.append(exportButton);
  card.append(actions);
  root.replaceChildren(card);

  const updateValidity = (): void => {
    let parametersValid = true;
    try {
      parseParameters(params.value);
      paramsNote.textContent = "键不能为空；值只能是字符串、有限数字或布尔值。修改后离开输入框保存。";
    } catch {
      parametersValid = false;
      paramsNote.textContent = "参数须为 JSON 对象，键不能为空，值只能是字符串、有限数字或布尔值。";
    }
    const imageValid = image.value === "" || DIGEST_IMAGE.test(image.value);
    imageNote.textContent = image.value === ""
      ? "镜像未指定；需填写 digest 固定的 OCI 镜像引用。"
      : imageValid
        ? "镜像引用格式有效；镜像存在性、内容和运行权限尚未检查。"
        : "镜像必须使用 @sha256: 后接 64 位小写十六进制摘要。";
    exportButton.disabled = !parametersValid || !imageValid;
  };
  params.addEventListener("input", updateValidity);
  params.addEventListener("change", () => {
    updateValidity();
    let parsed: Parameters;
    try {
      parsed = parseParameters(params.value);
    } catch {
      return;
    }
    emit({ ...currentConfig, algorithms: {
      ...currentConfig.algorithms, parameters: parsed,
    } });
  });
  image.addEventListener("input", updateValidity);
  image.addEventListener("change", () => {
    updateValidity();
    if (!DIGEST_IMAGE.test(image.value) && image.value !== "") return;
    emit({ ...currentConfig, deployment: { ...currentConfig.deployment, imageRef: image.value } });
  });
  exportButton.addEventListener("click", () => {
    if (exportButton.disabled) return;
    const blob = new Blob([JSON.stringify(currentConfig, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "city-workspace-config.json";
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  });
  updateValidity();
}

function executorRequirement(executor: CityWorkspaceConfig["deployment"]["executor"]): string {
  return executor === "docker_reference"
    ? "Docker 前置条件待查：用户可访问的 Docker daemon、digest 镜像及正式工作负载安全约束。"
    : "Kubernetes 前置条件待查：实际 kubeconfig、已有 Namespace、namespaced RBAC 和 NetworkPolicy 强制执行。";
}
