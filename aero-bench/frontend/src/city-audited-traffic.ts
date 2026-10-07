import { parseCityTrafficData, type TrafficData } from "./city-presentation";
import { cityTrafficProvenance, type VerifiedCityRoadAssets } from "./city-road-assets";
import { sameJson } from "./city-roads";
import { parseCitySceneConfig, type CitySceneConfig } from "./city-scene-config";
import {
  parseTrafficPreviewCatalog, parseTrafficPreviewJob, parseTrafficPreviewTrace,
  snapshotTrafficPreviewDraft, trafficPreviewDraftMatches,
  type VerifiedTrafficPreviewArtifact,
} from "./city-traffic-preview-api";
import type { CityWorkspaceConfig } from "./city-workspace-config";
import { assertTrafficPreviewRequest } from "./generated/contract-validators";
import { parseStrictJson } from "./strict-json";
import { assertNotAborted, readBoundedResponse } from "./verified-bytes";

export interface AuditedTrafficSceneBinding {
  readonly scenePath: string;
  readonly scene: CitySceneConfig;
  readonly roads: VerifiedCityRoadAssets;
  readonly recordedTraffic: TrafficData;
}

export interface VerifiedSceneTrafficPreview extends VerifiedTrafficPreviewArtifact {
  readonly data: TrafficData;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`交通预览 ${label} 必须为对象`);
  }
  return value as Record<string, unknown>;
}

function jsonBytes(bytes: ArrayBuffer, label: string): unknown {
  try {
    return parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) {
    throw new Error(`交通预览 ${label} 不是有效的 UTF-8 JSON：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function sha256(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
}

/** Bind a ready offline artifact to the loaded, verified city before creating any render resources. */
export async function verifyAuditedTrafficForScene(
  artifact: VerifiedTrafficPreviewArtifact,
  currentDraft: CityWorkspaceConfig,
  binding: AuditedTrafficSceneBinding,
  signal?: AbortSignal,
): Promise<VerifiedSceneTrafficPreview> {
  assertNotAborted(signal);
  // Copy caller-owned values before yielding; the parsed trace is reconstructed from these bytes.
  const bytes = new Uint8Array(artifact.bytes);
  const job = parseTrafficPreviewJob(structuredClone(artifact.job));
  const profile = parseTrafficPreviewCatalog({
    schema_version: "aero-bench.traffic-preview-profile-catalog/v1",
    profiles: [structuredClone(artifact.profile)],
  }).profiles[0]!;
  const draftSnapshot = snapshotTrafficPreviewDraft(artifact.draftSnapshot.draft);
  if (draftSnapshot.localRevision !== artifact.draftSnapshot.localRevision
      || !trafficPreviewDraftMatches(draftSnapshot, currentDraft)) {
    throw new Error("交通预览草稿已经变更；请重新生成");
  }
  if (job.state !== "ready" || job.trace === null || job.canonical_audit === null
      || job.profile_id !== profile.profile_id || job.profile_sha256 !== profile.profile_sha256) {
    throw new Error("交通预览任务未通过审计或档案身份不一致");
  }
  if (profile.scene_path !== binding.scenePath || draftSnapshot.draft.scenePath !== binding.scenePath
      || binding.scene.road_assets === undefined) {
    throw new Error("交通预览与当前已验证道路场景不一致");
  }
  if (bytes.byteLength !== job.trace.size_bytes || bytes.byteLength > 256 * 1024 * 1024
      || await sha256(bytes.buffer) !== job.trace.sha256) {
    throw new Error("交通预览轨迹字节或 SHA-256 已改变");
  }
  assertNotAborted(signal);
  const request: unknown = {
    schema_version: "aero-bench.traffic-preview-request/v1",
    profile_id: profile.profile_id, profile_sha256: profile.profile_sha256,
    duration_seconds: job.duration_seconds, draft: draftSnapshot.draft,
  };
  assertTrafficPreviewRequest(request);
  const trace = parseTrafficPreviewTrace(jsonBytes(bytes.buffer, "轨迹"), {
    profile, draftSnapshot, acceptedJob: job, request,
  }, job);
  const data = parseCityTrafficData(trace);
  cityTrafficProvenance(data);
  const roads = binding.scene.road_assets;
  const baseline = record(binding.recordedTraffic, "当前轨迹");
  const basis = record(trace.visual_obstacle_basis, "障碍几何");
  const context = record(binding.roads.sourceContext, "道路来源");
  if (!sameJson(trace.source_context, context)
      || trace.source_network_sha256 !== roads.source_network_sha256
      || trace.source_osm_sha256 !== roads.source_osm_sha256
      || trace.mesh_pack_source_sha256 !== roads.mesh_pack_source_sha256
      || trace.vehicle_position_reference !== "center-derived-from-native-TraCI-front-bumper-and-length"
      || !sameJson(trace.signals, baseline.signals)
      || !sameJson(basis, baseline.visual_obstacle_basis)
      || basis.policy !== "canonical-rendered-building-footprints-and-effective-fixtures"
      || basis.route_obstacle_basis !== basis.policy
      || basis.effective_fixture_geometry_sha256 !== roads.effective_fixtures.sha256
      || basis.rendered_footprint_count !== record(context.building_geometry, "建筑几何").count
      || binding.roads.displayedSurfaceSha256 !== roads.displayed_surface_sha256) {
    throw new Error("交通预览的路网、信号、建筑或有效设施与当前场景不一致");
  }
  const response = await fetch(profile.scene_path, { method: "GET", redirect: "error", signal });
  if (!response.ok) throw new Error(`交通预览场景身份读取失败：HTTP ${response.status}`);
  const sceneBytes = await readBoundedResponse(response, 1024 * 1024, "交通预览场景", signal);
  if (await sha256(sceneBytes) !== profile.scene_sha256
      || !sameJson(parseCitySceneConfig(jsonBytes(sceneBytes, "场景")), binding.scene)) {
    throw new Error("交通预览档案的场景字节与当前加载场景不一致");
  }
  assertNotAborted(signal);
  return { job, profile, draftSnapshot, bytes, trace, data };
}
