"""Write a per-ZIP inventory from the verified browser models and CAD report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "public/models/uav/cad-conversion-report.json"
OUTPUT = ROOT / "public/models/uav/conversion-inventory.json"

MESH_LIMITATIONS = {
    "albatros-2k SW.zip": "17 个 STL 仅有机翼与升降舵部件；需打开 SolidWorks 根总装核验引用和实例位置后导出整机。",
    "blackbolt-xbr220-racing-quadcopter SW STL.zip": "9 个 STL 是独立零件；包内有 SolidWorks 根总装，但引用与实例位置尚未验证。",
    "carbon-fiber-hexacopter SW.zip": "13 个 STL 仅有打印件、云台和带轮；SolidWorks 总装的电机与标准件引用尚待核验。",
    "reconnaissance-attack-aircraft-1 6 STL X_T.zip": "可旋转 GLB 仅为 Hellfire 弹体外观，不是飞机机体；整机仍需可读取的 CAD 源，包内 X_T 尚无可验证导入路径。",
}

NEUTRAL_LIMITATIONS = {
    "Discovery MK inventor stp.zip": "外层 12 个 STEP 和嵌套 ZIP 的伺服器 STEP 都是部件或子装配；缺少可验证的飞机整机实例位置，需打开 Inventor 总装导出。",
    "drone-220 SW.zip": "36 个 STEP 中仅 SIYI ZR10 相机云台有实例层级；整机位置留在 SolidWorks 装配，当前无法验证。",
    "firefighting-quadcopter SW STP.zip": "45 个 STEP 各为单独 PRODUCT，未发现整机实例位姿；需从包内 SolidWorks 总装核验并导出。",
    "package-delivery-hexacopter SW STP.zip": "35 个 STEP 各为单独 PRODUCT，未发现整机实例位姿；需从包内 SolidWorks 总装核验并导出。",
}


def read_json(path: Path):
    return json.loads(path.read_text())


def existing_model(path: str) -> dict:
    file = ROOT / path
    if not file.is_file():
        raise ValueError(f"Declared preview is missing: {path}")
    return {"path": path, "bytes": file.stat().st_size}


def reason_for(item: dict) -> str:
    status = item["status"]
    extensions = set(item["extensions"])
    if item["archive"] == "atmos-uav-project-1 2 STP.zip" and status == "neutral_components_require_assembly":
        return "ATMOS TWIN EAGLE.stp 有 XCAF 装配根和 5414 个实体；浏览器预览网格仍在质量验收，尚未入库。"
    if status == "native_cad_requires_export":
        if item["archive"] == "large-uav-wip 3dm.zip":
            return "外层 BODY.3dm 和内层 RAR 的 NL DRONE WIP.3dm 均已核对；BODY 的 198 个 Brep 面没有缓存网格，本机缺少 3DM 曲面网格化工具，需在支持 Rhino Brep 的软件中导出并复核装配。"
        if item["archive"] == "custom-quadcopter-based-off-the-talon SW.zip":
            return "内层 Custom Talon Quad ZIP 已核对：29 个 SolidWorks 零件和 1 个原生总装，没有独立 STEP、IGES 或网格；需用可读取 SLDASM 的工具核验实例位置并导出。"
        if ".rar" in extensions:
            return "外层未发现可验收整机中性 CAD；包内 RAR 内容仍需单独核对，原生工程装配尚未导出。"
        if ".x_t" in extensions:
            return "包内有 Parasolid X_T，当前没有可验证的导入器；需核对整机装配并导出 STEP 或 GLB。"
        if {".sldasm", ".sldprt"} & extensions:
            return "当前可识别几何为 SolidWorks 原生装配/零件；需用可读取装配的软件核对引用后导出 STEP 或 GLB。"
        if {".iam", ".ipt"} & extensions:
            return "源包只有 Inventor 原生装配/零件；需核对引用后导出 STEP 或 GLB。"
        if {".creo-asm", ".creo-prt"} & extensions:
            return "源包只有 Creo 原生装配/零件；需核对引用后导出 STEP 或 GLB。"
        if {".catproduct", ".catpart"} & extensions:
            return "源包只有 CATIA 原生装配/零件；需核对引用后导出 STEP 或 GLB。"
        return "没有可验证的整机 STEP、IGES 或浏览器网格；需从原生工程导出。"
    if status == "neutral_components_require_assembly":
        return NEUTRAL_LIMITATIONS.get(item["archive"], "包内中性 CAD 是分散零件或子装配，未验证整机实例变换；需从原生总装导出。")
    if status == "mesh_without_neutral_cad":
        return MESH_LIMITATIONS.get(item["archive"], "源网格不能验证整机完整性或装配位置。")
    if status == "conversion_timeout":
        return f"中性 CAD 导入超过 {item['timeout_seconds']} 秒；尚未获得可验收 GLB。"
    if status in {"conversion_failed", "conversion_process_failed"}:
        return "CAD 导入或网格化失败；错误详情在 cad-conversion-report.json。"
    if status == "unsupported_or_nested_source":
        return "包内未识别可导入的中性 CAD 或整机网格，需检查嵌套文件及源工程。"
    raise ValueError(f"Unexpected unconverted status: {status}")


def main() -> None:
    cad = read_json(REPORT)
    if cad.get("schema_version") != "aero-bench.cad-conversion/v1" or len(cad["entries"]) != 290:
        raise ValueError("Expected a complete 290-archive CAD report")
    curated = read_json(ROOT / "scripts/uav-preview-models.json")
    meshes = read_json(ROOT / "scripts/uav-mesh-previews.json")
    browser_models: dict[str, list[dict]] = {}
    for item in curated:
        browser_models.setdefault(item["archive"], []).append({
            **existing_model(f"public/models/uav/{item['output']}"), "origin": "curated_stl_or_3dxml"})
    for item in meshes:
        browser_models.setdefault(item["archive"], []).append({
            **existing_model(f"public/models/uav/mesh/{item['output']}"), "origin": "source_stl",
            **({"scope": "component_only"} if item.get("component_only") else {})})
    rows = []
    for item in sorted(cad["entries"], key=lambda value: value["archive"].lower()):
        archive = item["archive"]
        models = list(browser_models.get(archive, []))
        if item["status"] == "converted":
            models.append({**existing_model(item["model_path"]), "origin": "neutral_cad"})
        component_only = bool(models) and all(model.get("scope") == "component_only" for model in models)
        state = "preview_available" if models and not component_only else "needs_source_work"
        rows.append({
            "archive": archive,
            "archive_bytes": item["archive_bytes"],
            "state": state,
            "conversion_status": item["status"],
            "models": models,
            "selected_member": item.get("selected_member"),
            "source_completeness": "component_only" if component_only else "unverified" if models else None,
            "reason": reason_for(item) if component_only or not models else "可在素材库旋转查看；GLB 不证明原生装配和零件全部完整。",
        })
    summary = Counter(row["state"] for row in rows)
    summary["total_archives"] = len(rows)
    summary["preview_archives"] = summary["preview_available"]
    summary["blocked_archives"] = summary["needs_source_work"]
    summary["component_previews"] = sum(row["source_completeness"] == "component_only" for row in rows)
    result = {"schema_version": "aero-bench.uav-conversion-inventory/v1",
              "summary": dict(summary), "entries": rows}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
