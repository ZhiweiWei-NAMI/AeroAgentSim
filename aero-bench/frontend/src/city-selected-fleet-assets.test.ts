import { describe, expect, it } from "vitest";
import { selectedFleetOptionsFromCatalog } from "./city-selected-fleet-assets";

describe("selected city fleet asset catalogue", () => {
  it("offers only curated ready UAV GLBs and identifies them as visual previews", () => {
    const options = selectedFleetOptionsFromCatalog({
      schema_version: "aero-bench.asset-library/v1", entries: [
        { id: "model:holybro-x500", category: "uav", kind: "model", status: "ready",
          format: "GLB", title: "Holybro X500", subgroup: "多旋翼", model_url: "/models/uav/x500.glb" },
        { id: "cad:unknown", category: "uav", kind: "model", status: "ready",
          format: "GLB", title: "未知 CAD", subgroup: "待识别", model_url: "/models/uav/unknown.glb" },
        { id: "model:unfinished", category: "uav", kind: "model", status: "pending",
          format: "GLB", title: "未就绪", subgroup: "多旋翼", model_url: "/models/uav/unready.glb" },
      ],
    });
    expect(options).toHaveLength(1);
    expect(options[0]).toMatchObject({ id: "model:holybro-x500", title: "Holybro X500", subgroup: "多旋翼" });
    expect(options[0]!.preview).toContain("未由素材验证");
  });

  it("refuses a declared ready model whose GLB path is missing", () => {
    expect(() => selectedFleetOptionsFromCatalog({
      schema_version: "aero-bench.asset-library/v1", entries: [
        { id: "model:missing", category: "uav", kind: "model", status: "ready",
          format: "GLB", title: "模型", subgroup: "多旋翼", model_url: "/other/model.glb" },
      ],
    })).toThrow(/目录条目无效/);
  });
});
