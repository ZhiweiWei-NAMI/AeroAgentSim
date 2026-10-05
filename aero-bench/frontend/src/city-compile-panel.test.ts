import { afterEach, describe, expect, it, vi } from "vitest";
import { renderCityCompilePanel, type CityCompiledSelection } from "./city-compile-panel";
import { createDefaultCityWorkspaceConfig } from "./city-workspace-config";
import type { CityCompilationResult, NativeSceneRegistration } from "./city-authoring-api";

const registrationId = "inspection.reference.explicit";
const registration: NativeSceneRegistration = {
  registration_id: registrationId,
  registration_sha256: "a".repeat(64),
  scene_path: "/city-presentation/default-scene-v1.json",
  scene_url: `/authoring/v1/native-scenes/${registrationId}/scene`,
  scene_schema_version: "aero-bench.public-scenario/v1",
  scene_sha256: "b".repeat(64),
  scene_size_bytes: 157730,
  world_id: "world.inspection-reference",
  world_digest: "c".repeat(64),
  profile_id: "inspection.reference.v1",
  editable_execution_fields: ["/seed"],
  retained_authoring_fields: ["/name", "/environment", "/stateKeyframes"],
  reference_draft: createDefaultCityWorkspaceConfig(),
};
const catalog = { schema_version: "aero-bench.city-scene-registration-catalog/v1", registrations: [registration] };
const compiledResult: CityCompilationResult = {
  schema_version: "aero-bench.city-compilation-result/v1",
  compilation_id: "d".repeat(64), draft_sha256: "e".repeat(64),
  registration_id: registrationId, registration_sha256: "a".repeat(64),
  status: "compiled", blockers: [],
  suite: { path: "compiled/suite.json", sha256: "f".repeat(64) },
  runs: [{ run_id: "1".repeat(64), scenario_digest: "2".repeat(64), world_id: "world.inspection-reference",
    world_digest: "c".repeat(64), executor_kind: "docker_reference", feasible: true }],
  executed: false, verified: false,
};
const blockedResult: CityCompilationResult = {
  schema_version: "aero-bench.city-compilation-result/v1",
  compilation_id: "d".repeat(64), draft_sha256: "e".repeat(64),
  registration_id: registrationId, registration_sha256: "a".repeat(64),
  status: "blocked",
  blockers: [{ code: "unsupported_field", field: "/fleet/1/assetId", message: "preview-only airframe cannot be lowered" }],
  suite: null, runs: [], executed: false, verified: false,
};
const apiError = (code: string, message: string) => ({
  schema_version: "aero-bench.authoring-error/v1", error: { code, message },
});
const jsonResponse = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

function compileButton(root: HTMLElement): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll("button")).find(item => item.textContent?.includes("编译"));
  if (!button) throw new Error("Missing compile button");
  return button;
}
function runButton(root: HTMLElement): HTMLButtonElement | null {
  return Array.from(root.querySelectorAll("button")).find(item => item.textContent === "运行") ?? null;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("city compile panel", () => {
  it("loads a reference draft only through an explicit user action", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, catalog)));
    const root = document.createElement("div");
    const onReference = vi.fn(async () => "参考草稿已载入，尚未保存或运行");
    const handle = renderCityCompilePanel(root, () => createDefaultCityWorkspaceConfig(), vi.fn(), onReference);
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(1));
    expect(onReference).not.toHaveBeenCalled();
    const button = Array.from(root.querySelectorAll("button")).find(item => item.textContent?.startsWith("载入所选"));
    expect(button?.disabled).toBe(false);
    button!.click();
    await vi.waitFor(() => expect(onReference).toHaveBeenCalledWith(registration));
    await vi.waitFor(() => expect(root.textContent).toContain("尚未保存或运行"));
    handle.dispose();
  });
  it("loads the catalog, compiles the draft, and reports a compiled result without starting a run", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/authoring/v1/native-scenes")) return jsonResponse(200, catalog);
      if (url.endsWith("/authoring/v1/compilations")) return jsonResponse(201, compiledResult);
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const root = document.createElement("div");
    const onCompiled = vi.fn<(selection: CityCompiledSelection) => void>();
    const handle = renderCityCompilePanel(root, () => createDefaultCityWorkspaceConfig(), onCompiled);

    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(1));
    expect(compileButton(root).disabled).toBe(false);
    expect(runButton(root)).toBeNull();

    compileButton(root).click();
    await vi.waitFor(() => expect(root.textContent).toContain("编译完成"));
    expect(root.textContent).toContain(compiledResult.compilation_id);
    expect(root.textContent).toContain(compiledResult.runs[0]!.run_id);
    const run = runButton(root);
    expect(run).not.toBeNull();
    expect(onCompiled).not.toHaveBeenCalled();

    run!.click();
    expect(onCompiled).toHaveBeenCalledWith({
      compilationId: compiledResult.compilation_id,
      registrationId,
      runIds: [compiledResult.runs[0]!.run_id],
    });

    const compileCall = fetchMock.mock.calls.find(call =>
      String(call[0] instanceof Request ? call[0].url : call[0]).endsWith("/compilations"));
    expect(compileCall).toBeDefined();
    const sentBody = JSON.parse(String((compileCall![1] as RequestInit).body)) as Record<string, unknown>;
    expect(sentBody).toMatchObject({ registration_id: registrationId, registration_sha256: registration.registration_sha256 });

    handle.dispose();
    expect(root.children).toHaveLength(0);
  });

  it("shows field-pointer blockers and never offers a Run affordance for a blocked result", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/authoring/v1/native-scenes")) return jsonResponse(200, catalog);
      if (url.endsWith("/authoring/v1/compilations")) return jsonResponse(422, blockedResult);
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const root = document.createElement("div");
    const onCompiled = vi.fn();
    renderCityCompilePanel(root, () => createDefaultCityWorkspaceConfig(), onCompiled);
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(1));

    compileButton(root).click();
    await vi.waitFor(() => expect(root.textContent).toContain("存在执行阻断"));
    expect(root.textContent).toContain("/fleet/1/assetId");
    expect(root.textContent).toContain("unsupported_field");
    expect(runButton(root)).toBeNull();
    expect(onCompiled).not.toHaveBeenCalled();
  });

  it("surfaces a declared API error without inventing a result", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/authoring/v1/native-scenes")) return jsonResponse(200, catalog);
      if (url.endsWith("/authoring/v1/compilations")) return jsonResponse(503, apiError("compiler_unconfigured", "未配置编译器"));
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const root = document.createElement("div");
    renderCityCompilePanel(root, () => createDefaultCityWorkspaceConfig(), vi.fn());
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(1));

    compileButton(root).click();
    await vi.waitFor(() => expect(root.textContent).toContain("compiler_unconfigured"));
    expect(root.textContent).toContain("未配置编译器");
    expect(runButton(root)).toBeNull();
  });

  it("disables compiling on an empty catalog and reports a catalog-level API error", async () => {
    const empty = vi.fn(async () => jsonResponse(200, { ...catalog, registrations: [] }));
    vi.stubGlobal("fetch", empty);
    const root = document.createElement("div");
    renderCityCompilePanel(root, () => createDefaultCityWorkspaceConfig(), vi.fn());
    await vi.waitFor(() => expect(root.textContent).toContain("目录为空"));
    expect(compileButton(root).disabled).toBe(true);

    vi.unstubAllGlobals();
    const unavailable = vi.fn(async () => jsonResponse(503, apiError("compiler_unconfigured", "未配置编译器")));
    vi.stubGlobal("fetch", unavailable);
    const root2 = document.createElement("div");
    renderCityCompilePanel(root2, () => createDefaultCityWorkspaceConfig(), vi.fn());
    await vi.waitFor(() => expect(root2.textContent).toContain("compiler_unconfigured"));
    expect(compileButton(root2).disabled).toBe(true);
  });

  it("aborts its pending request on dispose instead of leaking a stray fetch", async () => {
    let aborted = false;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => { aborted = true; reject(new DOMException("aborted", "AbortError")); });
    }));
    vi.stubGlobal("fetch", fetchMock);
    const root = document.createElement("div");
    const handle = renderCityCompilePanel(root, () => createDefaultCityWorkspaceConfig(), vi.fn());
    handle.dispose();
    await vi.waitFor(() => expect(aborted).toBe(true));
    expect(root.children).toHaveLength(0);
  });
});
