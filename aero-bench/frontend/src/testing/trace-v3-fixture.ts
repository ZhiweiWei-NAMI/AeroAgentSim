/**
 * Test-only fixture builders for the strict Public Trace v3 boundary.
 *
 * These documents exist to exercise the generated Ajv contracts and the
 * console modules in unit tests. They are not runtime evidence, not
 * demo data, and never imported by production source. The builders
 * produce schema-valid documents (closed objects, declared enum values,
 * real-shaped digests) with coherent identities.
 */

export const RUN_ID = "a".repeat(64);
export const OTHER_RUN_ID = "b".repeat(64);
export const SCENARIO_DIGEST = "c".repeat(64);
export const CHAIN_ROOT = "d".repeat(64);
export const DIGEST_1 = "1".repeat(64);
export const DIGEST_2 = "2".repeat(64);

export function identifier(name: string): string {
  return `fixture.${name}`;
}

export function coordinate(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    enu: { east_m: 0, north_m: 0, up_m: 0 },
    ned: { east_m: 0, north_m: 0, down_m: 0 },
    ecef: { x_m: -2_160_000, y_m: 4_380_000, z_m: 4_071_000 },
    wgs84: { latitude_deg: 31.2, longitude_deg: 121.5, ellipsoid_height_m: 4 },
    geoid_separation_m: -32.4,
    amsl_m: 4,
    terrain_amsl_m: 4,
    agl_m: 0,
    ...overrides,
  };
}

export function pose(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const { position: positionOverride, ...rest } = overrides as {
    position?: Record<string, unknown>;
  } & Record<string, unknown>;
  const position = coordinate(positionOverride);
  return {
    orientation_enu: { qw: 1, qx: 0, qy: 0, qz: 0 },
    orientation_ned: { qw: 1, qx: 0, qy: 0, qz: 0 },
    ...rest,
    position,
  };
}

export function simulationTime(tick: number, simTimeNs: number): Record<string, unknown> {
  return { tick, sim_time_ns: simTimeNs };
}

export function stateSample(
  tick: number,
  entityId: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const { at: atOverride, ...rest } = overrides as { at?: Record<string, unknown> } & Record<string, unknown>;
  const at = atOverride ?? simulationTime(tick, tick * 1_000_000_000);
  return {
    schema_version: "aero-bench.state-sample/v1",
    run_id: RUN_ID,
    scenario_digest: SCENARIO_DIGEST,
    stage: "motion",
    entity_id: entityId,
    provider_id: identifier("motion"),
    sample_kind: "dynamic",
    pose: pose(),
    linear_velocity_enu: { east_mps: 0, north_mps: 0, up_mps: 0, frame_id: "ENU" },
    linear_velocity_ned: { east_mps: 0, north_mps: 0, down_mps: 0, frame_id: "NED" },
    sample_digest: DIGEST_1,
    ...rest,
    at,
  };
}

export function stageBarrier(tick: number, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const { at: atOverride, ...rest } = overrides as { at?: Record<string, unknown> } & Record<string, unknown>;
  const at = atOverride ?? simulationTime(tick, tick * 1_000_000_000);
  return {
    schema_version: "aero-bench.stage-barrier/v1",
    run_id: RUN_ID,
    scenario_digest: SCENARIO_DIGEST,
    stage: "motion",
    provider_ids: [identifier("motion")],
    receipts: [],
    receipt_digests: [],
    barrier_digest: DIGEST_2,
    ...rest,
    at,
  };
}

export function sceneState(
  tick: number,
  entityIds: readonly string[],
  samples: readonly Record<string, unknown>[],
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const { at: atOverride, ...rest } = overrides as { at?: Record<string, unknown> } & Record<string, unknown>;
  const at = atOverride ?? simulationTime(tick, tick * 1_000_000_000);
  return {
    schema_version: "aero-bench.scene-state/v1",
    run_id: RUN_ID,
    scenario_digest: SCENARIO_DIGEST,
    declared_entity_ids: [...entityIds],
    samples: [...samples],
    stage_barrier: stageBarrier(tick),
    contribution_digests: [DIGEST_1],
    previous_scene_state_digest: DIGEST_1,
    scene_state_digest: DIGEST_2,
    ...rest,
    at,
  };
}

export function scenario(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: "aero-bench.public-scenario/v1",
    world_schema_version: "aero-bench.world/v2",
    world_id: identifier("world"),
    world_digest: DIGEST_1,
    scenario_asset_digest: DIGEST_1,
    scenario_digest: SCENARIO_DIGEST,
    selected_launch_site_id: identifier("site"),
    seed: 7,
    frame_authority: {
      geodetic_frame_id: "WGS84",
      ecef_frame_id: "ECEF",
      enu_frame_id: "ENU",
      ned_frame_id: "NED",
      origin: coordinate(),
      origin_ecef: { x_m: -2_160_000, y_m: 4_380_000, z_m: 4_071_000 },
      ecef_to_enu_rotation: { rows: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] },
      enu_to_ecef_rotation: { rows: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] },
      enu_to_ned_rotation: { rows: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] },
      ned_to_enu_rotation: { rows: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] },
      spatial_extent: {
        min_east_m: -100, max_east_m: 100, min_north_m: -100, max_north_m: 100,
        min_up_m: 0, max_up_m: 120, vertical_reference: "enu_up",
      },
      geoid_correction_asset_id: identifier("geoid"),
      terrain_height_asset_id: identifier("terrain"),
      geoid_interpolation: "bilinear",
      terrain_interpolation: "bilinear",
      geoid_precision_m: 0.01,
      terrain_precision_m: 0.01,
    },
    assets: [
      {
        asset_id: identifier("geoid"),
        selector: "geoid/egm96",
        sha256: DIGEST_1,
        size_bytes: 16,
        media_type: "application/octet-stream",
        replay_path: `assets/${DIGEST_1}`,
        license_id: null,
        license_selector: null,
        license_sha256: null,
        license_size_bytes: null,
        license_replay_path: null,
      },
    ],
    base_layers: [],
    layers: [],
    buildings: [],
    roads: [],
    regions: [],
    launch_sites: [
      {
        launch_site_id: identifier("site"),
        primary_uav_entity_id: identifier("uav"),
        allowed_uav_entity_ids: [identifier("uav")],
        pose: pose(),
        pad_radius_m: 5,
        selected: true,
      },
    ],
    entities: [
      {
        entity_id: identifier("uav"),
        kind: "uav",
        owner_kind: "scenario",
        owner_id: identifier("world"),
        authority_kind: "gazebo_physics",
        state: "dynamic",
        model_asset_id: null,
        initial_pose: pose(),
        selected_launch_override: false,
      },
    ],
    sensors: [],
    semantic_targets: [],
    weather: [
      {
        sample_id: identifier("weather"),
        mode: "deterministic_constant",
        wind: { east_mps: 1, north_mps: 0, up_mps: 0 },
        visibility_m: 10_000,
        precipitation: "none",
        precipitation_rate_mm_per_h: 0,
        temperature_c: 20,
        pressure_pa: 101_325,
      },
    ],
    sumo: null,
    network: null,
    mission_requirements: [],
    expected_public_assets: [],
    ...overrides,
  };
}

export function publicRunEvent(
  sequence: number,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const { at: atOverride, ...rest } = overrides as { at?: Record<string, unknown> } & Record<string, unknown>;
  const at = atOverride ?? simulationTime(1, 1_000_000_000);
  return {
    run_id: RUN_ID,
    event_id: `event.${String(sequence).padStart(16, "0")}`,
    event_digest: DIGEST_1,
    sequence,
    source_kind: "agent",
    source: identifier("agent"),
    event_type: "agent.interaction",
    correlation_id: identifier("corr"),
    parent_event_id: null,
    causal_event_ids: [],
    agent_id: identifier("agent"),
    provider_id: null,
    vehicle_id: null,
    entity_id: identifier("uav"),
    command_id: null,
    observation_id: null,
    query_id: null,
    payload_schema_id: identifier("schema"),
    payload_digest: DIGEST_2,
    interaction_type: "agent.decision_summary.v1",
    public_payload: [
      { name: "summary", value: "advance to waypoint", value_type: "str" },
    ],
    ...rest,
    at,
  };
}

export function publicTrace(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const entityId = identifier("uav");
  const trace = {
    schema_version: "aero-bench.public-trace/v3",
    projector_version: "aero-bench.public-projector/v2",
    run_id: RUN_ID,
    suite_id: identifier("suite"),
    case_id: identifier("case"),
    execution_scope: "executor_validation",
    phase: "sealed",
    time: simulationTime(1, 1_000_000_000),
    event_chain_root: CHAIN_ROOT,
    scenario_digest: SCENARIO_DIGEST,
    scenario: scenario(),
    scene_state_history_artifact: {
      artifact_id: identifier("history"),
      selector: "artifacts/scene-state-history",
      digest: DIGEST_2,
      visibility: "public",
    },
    runtime_artifacts: [
      {
        artifact_id: identifier("history"),
        artifact_type: "scene.state-history",
        selector: "artifacts/scene-state-history",
        sha256: DIGEST_2,
        size_bytes: 0,
        replay_path: `artifacts/${DIGEST_2}`,
      },
    ],
    scene_states: [sceneState(1, [entityId], [stateSample(1, entityId)])],
    trajectories: [],
    network_frames: [],
    network_events: [],
    mission_status_history: [],
    mission_status: null,
    mission_events: [],
    sensor_frames: [],
    events: [publicRunEvent(0)],
    provider_status: [
      {
        provider_id: identifier("motion"),
        state: "complete",
        version: "1.0.0",
        implementation_kind: "production",
      },
    ],
    terminal: {
      kind: "completed",
      event_id: identifier("terminal"),
      at: simulationTime(1, 1_000_000_000),
      failure_class: null,
      provider_failure_ids: [],
    },
    verifier_public: null,
    ...overrides,
  };
  return trace;
}

/** A schema-valid scene-state stream event envelope (live control stream). */
export function sceneStateStreamEvent(
  tick: number,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const entityId = identifier("uav");
  return {
    schema_version: "aero-bench.scene-state-stream-event/v1",
    run_id: RUN_ID,
    scene_state: sceneState(tick, [entityId], [stateSample(tick, entityId)]),
    ...overrides,
  };
}

/** A schema-valid run-transition stream event envelope. */
export function runTransitionStreamEvent(
  sequence: number,
  phase: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: "aero-bench.run-transition-event/v1",
    run_id: RUN_ID,
    transition: {
      event_type: "phase.changed",
      failure_class: null,
      phase,
      run_id: RUN_ID,
      runtime_control: null,
      schema_version: "aero-bench.run-execution-transition/v1",
      sequence,
      wall_time_ns: sequence * 1_000_000_000,
    },
    ...overrides,
  };
}

/** A schema-valid public-run-event stream envelope. */
export function publicRunEventStreamEvent(
  sequence: number,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: "aero-bench.public-run-event-stream-event/v1",
    run_id: RUN_ID,
    event: publicRunEvent(sequence, overrides),
  };
}

/** A schema-valid ControlCatalog. */
export function controlCatalog(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: "aero-bench.control-catalog/v1",
    suite_id: identifier("suite"),
    suite_sha256: DIGEST_1,
    runs: [
      {
        run_id: RUN_ID,
        suite_id: identifier("suite"),
        case_id: identifier("case"),
        execution_scope: "executor_validation",
        world_id: identifier("world"),
        scenario_digest: SCENARIO_DIGEST,
        task_id: identifier("task"),
        seed: 7,
        launch_site_id: identifier("site"),
        matrix_axis_ids: [],
      },
    ],
    ...overrides,
  };
}
