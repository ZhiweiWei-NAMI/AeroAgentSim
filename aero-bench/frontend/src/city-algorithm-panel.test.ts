import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderCityAlgorithmPanel } from "./city-algorithm-panel";
import { createDefaultCityWorkspaceConfig } from "./city-workspace-config";
import type { CityWorkspaceConfig } from "./city-workspace-config";

function draft(): CityWorkspaceConfig {
  const base = createDefaultCityWorkspaceConfig();
  return { ...base, algorithms: {
    ...base.algorithms, parameters: { reserve_fraction: 0.2 },
  } };
}

function select(root: HTMLElement, label: string): HTMLSelectElement {
  const control = root.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`);
  if (!control) throw new Error(`Missing ${label} select`);
  return control;
}

describe("city algorithm panel", () => {
  beforeEach(() => document.body.replaceChildren());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("persists the decision mode and later algorithm changes in one draft", () => {
    const root = document.createElement("div");
    const onChange = vi.fn<(next: CityWorkspaceConfig) => void>();
    renderCityAlgorithmPanel(root, draft(), onChange);

    const mode = select(root, "决策方式");
    mode.value = "distributed";
    mode.dispatchEvent(new Event("change"));
    const assignment = select(root, "订单分配");
    assignment.value = "auction";
    assignment.dispatchEvent(new Event("change"));

    const changed = onChange.mock.lastCall?.[0];
    expect(changed?.algorithms).toMatchObject({ mode: "distributed", assignment: "auction" });
    expect(root.textContent).toContain("各机载代理读取获授权订单和自身状态");
    renderCityAlgorithmPanel(root, changed!, onChange);
    expect(select(root, "决策方式").value).toBe("distributed");
    expect(select(root, "订单分配").value).toBe("auction");
  });

  it("labels templates and missing backends without making a network deployment call", () => {
    const root = document.createElement("div");
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const createObjectURL = vi.fn(() => "blob:city-config");
    const revokeObjectURL = vi.fn();
    class DownloadURL extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    }
    vi.stubGlobal("URL", DownloadURL);
    vi.useFakeTimers();
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderCityAlgorithmPanel(root, draft(), vi.fn());

    expect(root.textContent).toContain("算法配置模板，尚未部署");
    expect(root.textContent).toContain("城市物流订单后端尚不存在");
    expect(root.textContent).toContain("充电后端尚不存在");
    expect(root.textContent).toContain("它不能编译本草稿");
    expect(root.querySelector("button")?.textContent).toBe("导出配置供后端接入");

    root.querySelector("button")!.click();
    vi.runAllTimers();
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(anchorClick).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:city-config");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("blocks export of invalid parameter JSON or an unpinned image reference", () => {
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCityAlgorithmPanel(root, draft(), onChange);
    const button = root.querySelector("button")!;
    const params = root.querySelector<HTMLTextAreaElement>("textarea")!;
    params.value = '{"weights": [1, 2]}';
    params.dispatchEvent(new Event("input"));
    params.dispatchEvent(new Event("change"));
    expect(button.disabled).toBe(true);
    expect(onChange).not.toHaveBeenCalled();

    params.value = '{"reserve_fraction": 0.25}';
    params.dispatchEvent(new Event("change"));
    const image = root.querySelector<HTMLInputElement>('input[aria-label="镜像 digest 引用"]')!;
    image.value = "registry.example/agent:latest";
    image.dispatchEvent(new Event("input"));
    image.dispatchEvent(new Event("change"));
    expect(button.disabled).toBe(true);
    expect(onChange.mock.lastCall?.[0].deployment.imageRef).toBe("");
  });
});
