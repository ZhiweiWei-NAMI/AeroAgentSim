// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import {
  parseCityAuthoredLandscape,
  type CityAuthoredLandscapeItem,
} from "./city-authored-landscape";
import { renderCityLandscapePanel } from "./city-landscape-panel";

function item(id = "landscape-1"): CityAuthoredLandscapeItem {
  return {
    id,
    label: "庭院绿地",
    provenance: "authored",
    kind: "green",
    polygon: [
      { x: 0, z: 0 },
      { x: 10, z: 0 },
      { x: 10, z: 10 },
      { x: 0, z: 10 },
    ],
  };
}

function click(root: HTMLElement, label: string): void {
  const control = [...root.querySelectorAll("button")].find(
    (candidate) => candidate.textContent === label,
  );
  if (control === undefined) throw new Error(`Missing button ${label}`);
  control.click();
}

function clickAria(root: HTMLElement, label: string): void {
  const control = root.querySelector<HTMLButtonElement>(
    `button[aria-label="${label}"]`,
  );
  if (control === null) throw new Error(`Missing button ${label}`);
  control.click();
}

function setText(root: HTMLElement, label: string, value: string): void {
  const control = root.querySelector<HTMLInputElement>(
    `input[aria-label="${label}"]`,
  );
  if (control === null) throw new Error(`Missing input ${label}`);
  control.value = value;
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

function setNumber(root: HTMLElement, label: string, value: number): void {
  setText(root, label, String(value));
}

function choose(root: HTMLElement, label: string, value: string): void {
  const control = root.querySelector<HTMLSelectElement>(
    `select[aria-label="${label}"]`,
  );
  if (control === null) throw new Error(`Missing select ${label}`);
  control.value = value;
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("city landscape panel", () => {
  it("stages a new polygon locally and emits only an exact parser-valid item", () => {
    const root = document.createElement("div");
    const onChange =
      vi.fn<(items: readonly CityAuthoredLandscapeItem[]) => void>();
    renderCityLandscapePanel(root, [], onChange);
    expect(root.textContent).toContain("不会写入 OSM 来源文件");
    click(root, "添加创作景观");
    expect(onChange).not.toHaveBeenCalled();
    click(root, "保存景观");
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "label is invalid",
    );
    expect(onChange).not.toHaveBeenCalled();

    setText(root, "景观标签", "滨水种植带");
    choose(root, "景观类型", "planting_strip");
    for (let index = 0; index < 4; index++) click(root, "添加顶点");
    for (const [index, [x, z]] of [
      [0, [0, 0]],
      [1, [12, 0]],
      [2, [12, 4]],
      [3, [0, 4]],
    ] as const) {
      setNumber(root, `顶点 ${index + 1} X / m`, x);
      setNumber(root, `顶点 ${index + 1} Z / m`, z);
    }
    click(root, "保存景观");
    expect(onChange).toHaveBeenCalledTimes(1);
    const saved = onChange.mock.lastCall![0];
    expect(saved).toEqual([
      {
        id: "landscape-1",
        label: "滨水种植带",
        provenance: "authored",
        kind: "planting_strip",
        polygon: [
          { x: 0, z: 0 },
          { x: 12, z: 0 },
          { x: 12, z: 4 },
          { x: 0, z: 4 },
        ],
      },
    ]);
    expect(() => parseCityAuthoredLandscape(saved)).not.toThrow();
    expect(JSON.stringify(saved)).not.toMatch(/osm|sourceId|sourceSha/i);
  });

  it("edits labels, kinds, and vertices without mutating the caller, then deletes the item", () => {
    const root = document.createElement("div");
    const original = [item()];
    const changes: (readonly CityAuthoredLandscapeItem[])[] = [];
    renderCityLandscapePanel(root, original, (next) => changes.push(next));
    clickAria(root, "编辑景观 landscape-1");
    setText(root, "景观标签", "铺装庭院");
    choose(root, "景观类型", "plaza");
    setNumber(root, "顶点 2 X / m", 12);
    click(root, "保存景观");
    expect(changes).toHaveLength(1);
    expect(changes[0]![0]).toMatchObject({
      id: "landscape-1",
      label: "铺装庭院",
      kind: "plaza",
      provenance: "authored",
    });
    expect(changes[0]![0]!.polygon[1]).toEqual({ x: 12, z: 0 });
    expect(original[0]).toEqual(item());

    renderCityLandscapePanel(root, changes[0]!, (next) => changes.push(next));
    clickAria(root, "删除景观 landscape-1");
    expect(changes.at(-1)).toEqual([]);
  });

  it("blocks self-intersection, degenerate edits, and duplicate IDs without emitting", () => {
    const root = document.createElement("div");
    const first = item("landscape-1"),
      second = {
        ...item("landscape-2"),
        polygon: item().polygon.map((point) => ({
          x: point.x + 20,
          z: point.z,
        })),
      };
    const onChange = vi.fn();
    renderCityLandscapePanel(root, [first, second], onChange);
    clickAria(root, "编辑景观 landscape-1");
    setNumber(root, "顶点 2 Z / m", 10);
    setNumber(root, "顶点 3 X / m", 0);
    setNumber(root, "顶点 4 X / m", 8);
    setNumber(root, "顶点 4 Z / m", 0);
    click(root, "保存景观");
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "self-intersects",
    );
    expect(onChange).not.toHaveBeenCalled();

    setText(root, "景观 ID", "landscape-2");
    setNumber(root, "顶点 2 Z / m", 0);
    setNumber(root, "顶点 3 X / m", 10);
    setNumber(root, "顶点 4 X / m", 0);
    setNumber(root, "顶点 4 Z / m", 10);
    click(root, "保存景观");
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "duplicated",
    );
    expect(onChange).not.toHaveBeenCalled();

    setText(root, "景观 ID", "landscape-1");
    setNumber(root, "顶点 3 X / m", 20);
    setNumber(root, "顶点 3 Z / m", 0);
    clickAria(root, "删除顶点 4");
    click(root, "保存景观");
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "degenerate",
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  it("rejects non-authored input before rendering editable controls", () => {
    const root = document.createElement("div");
    expect(() =>
      renderCityLandscapePanel(
        root,
        [
          {
            ...item(),
            provenance: "osm",
          } as unknown as CityAuthoredLandscapeItem,
        ],
        vi.fn(),
      ),
    ).toThrow(/provenance/);
    expect(root.children).toHaveLength(0);
  });
});

describe("optional authored completion controls", () => {
  it("requires an explicit kind, replaces suggestions, edits them normally and removes only completion items", () => {
    const root = document.createElement("div");
    const manual = item("manual");
    const proposals = [item("auto-completion-v1-plaza-0-0")];
    const propose = vi.fn().mockReturnValue({ items: proposals, stats: {
      residualCells: 1, rectangles: 1, areaM2: 100, droppedSmall: 0, truncated: false } });
    const onChange = vi.fn();
    renderCityLandscapePanel(root, [manual], onChange, { propose });
    expect(root.textContent).toContain("自动补全（创作层，默认关闭）");
    const select = root.querySelector<HTMLSelectElement>('select[aria-label="自动补全类型"]')!;
    expect(select.value).toBe("");
    expect(select.options[0]!.disabled).toBe(true);
    expect(select.options[0]!.textContent).toBe("选择类型");
    const generate = [...root.querySelectorAll("button")].find(button => button.textContent === "生成建议")!;
    expect(generate.disabled).toBe(true);
    generate.click();
    expect(propose).not.toHaveBeenCalled();
    choose(root, "自动补全类型", "plaza");
    expect(generate.disabled).toBe(false);
    click(root, "生成建议");
    expect(propose).toHaveBeenCalledWith("plaza");
    expect(onChange.mock.lastCall![0]).toEqual([manual, ...proposals]);
    expect(root.querySelector(`[data-landscape-id="${proposals[0]!.id}"]`)).not.toBeNull();
    clickAria(root, `编辑景观 ${proposals[0]!.id}`);
    setText(root, "景观标签", "修改后的自动设计");
    click(root, "保存景观");
    expect(onChange.mock.lastCall![0][1].label).toBe("修改后的自动设计");
    const replacement = [item("auto-completion-v1-plaza-10-0")];
    propose.mockReturnValue({ items: replacement, stats: {
      residualCells: 1, rectangles: 1, areaM2: 100, droppedSmall: 0, truncated: false } });
    click(root, "生成建议");
    expect(onChange.mock.lastCall![0]).toEqual([manual, ...replacement]);
    expect(root.querySelector(`[data-landscape-id="${proposals[0]!.id}"]`)).toBeNull();
    click(root, "移除自动补全");
    expect(onChange.mock.lastCall![0]).toEqual([manual]);
    expect(manual).toEqual(item("manual"));
  });

  it("omits the section without capability, including when an existing panel loses it", () => {
    const root = document.createElement("div");
    const propose = vi.fn();
    renderCityLandscapePanel(root, [], vi.fn());
    expect(root.querySelector('[data-role="terrain-completion"]')).toBeNull();
    renderCityLandscapePanel(root, [], vi.fn(), { propose });
    expect(root.querySelector('[data-role="terrain-completion"]')).not.toBeNull();
    renderCityLandscapePanel(root, [], vi.fn(), null);
    expect(root.querySelector('[data-role="terrain-completion"]')).toBeNull();
    expect(propose).not.toHaveBeenCalled();
  });

  it("reports a proposal failure without replacing the existing items", () => {
    const root = document.createElement("div"), onChange = vi.fn();
    renderCityLandscapePanel(root, [item()], onChange,
      { propose: () => { throw new Error("Source geometry unavailable"); } });
    choose(root, "自动补全类型", "green");
    click(root, "生成建议");
    expect(root.querySelector('[role="alert"]')?.textContent).toBe("Source geometry unavailable");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("states the generation result with count, area and truncation, and hides it on remove", () => {
    const root = document.createElement("div"), onChange = vi.fn();
    const proposals = [
      item("auto-completion-v1-green-0-0"),
      item("auto-completion-v1-green-10-0"),
    ];
    renderCityLandscapePanel(root, [], onChange, {
      propose: vi.fn().mockReturnValue({ items: proposals, stats: {
        residualCells: 210, rectangles: 2, areaM2: 1180.5, droppedSmall: 1, truncated: false } }),
    });
    expect(root.querySelector(".completion-status")).toBeNull();
    choose(root, "自动补全类型", "green");
    click(root, "生成建议");
    const status = root.querySelector(".completion-status")!;
    expect(status.textContent).toContain("已生成 2 项");
    expect(status.textContent).toContain("共 1180.5 m²");
    expect(status.textContent).not.toContain("已达上限");
    click(root, "移除自动补全");
    expect(root.querySelector(".completion-status")).toBeNull();

    const truncated = vi.fn().mockReturnValue({ items: proposals.slice(0, 1), stats: {
      residualCells: 210, rectangles: 401, areaM2: 16000, droppedSmall: 0, truncated: true } });
    renderCityLandscapePanel(root, [], onChange, { propose: truncated });
    choose(root, "自动补全类型", "green");
    click(root, "生成建议");
    const capped = root.querySelector(".completion-status")!;
    expect(capped.textContent).toContain("已生成 1 项，共 16000 m²");
    expect(capped.textContent).toContain("（已达上限 400 项，按面积保留最大者）");
  });
});
