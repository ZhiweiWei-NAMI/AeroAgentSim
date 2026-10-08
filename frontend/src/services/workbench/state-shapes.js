import { isObjectLike, firstDefined } from './helpers';

export function normalizeRun(run) {
  if (!run) {
    return null;
  }
  return {
    run_id: run.run_id,
    config_id: run.config_id,
    config_name: run.config_name,
    status: run.status == null ? undefined : String(run.status).toUpperCase(),
    started_at: run.started_at || run.created_at || null,
    updated_at: run.ended_at || run.started_at || run.created_at || null,
    simulation_time: run.latest_sim_time ?? run.simulation_time,
    speed: run.latest_speed ?? run.speed,
    coordinate_mode: run.coordinate_mode,
    registry_references: run.registry_references || [],
  };
}

export function normalizeHealth(data) {
  if (!data) {
    return {
      backend_available: undefined,
      db_writable: undefined,
      simulation_status: undefined,
      active_run_id: null,
      recent_startup_error: null,
      timestamp: null,
    };
  }
  return {
    backend_available: data.backend_available,
    db_writable: data.db_writable,
    simulation_status: data.simulation_status == null ? undefined : String(data.simulation_status).toUpperCase(),
    active_run_id: data.active_run_id || null,
    recent_startup_error: data.recent_startup_error || null,
    timestamp: data.timestamp || null,
  };
}

export function normalizeLog(log, index = 0) {
  const raw = isObjectLike(log) ? log : { message: String(log || '') };
  const value = isObjectLike(raw.value)
    ? raw.value
    : isObjectLike(raw.event_data)
      ? raw.event_data
      : {};
  const result = isObjectLike(raw.result)
    ? raw.result
    : isObjectLike(value.result)
      ? value.result
      : {};
  const details = {
    ...value,
    ...result,
  };
  const event = firstDefined(raw.event, raw.event_type, value.event_name, details.event_name, null);
  const sourceId = firstDefined(raw.source_id, value.source_id, details.source_id, raw.source, null);
  const workflowId = firstDefined(
    raw.workflow_id,
    value.workflow_id,
    details.workflow_id,
    result.workflow_id,
    null
  );
  const taskId = firstDefined(raw.task_id, value.task_id, details.task_id, result.task_id, null);
  const taskName = firstDefined(
    raw.task_name,
    value.task_name,
    details.task_name,
    null
  );
  const taskClass = firstDefined(
    raw.task_class,
    value.task_class,
    details.task_class,
    result.task_class,
    null
  );
  const status = firstDefined(raw.status, value.status, result.status, null);

  return {
    id: raw.id,
    run_id: raw.run_id || null,
    level: raw.level,
    source: raw.source || sourceId,
    source_id: sourceId,
    event,
    workflow_id: workflowId,
    task_id: taskId,
    task_name: taskName,
    task_class: taskClass,
    status: status ? String(status).toLowerCase() : null,
    result,
    value,
    details,
    sim_time: raw.time ?? raw.sim_time,
    message: raw.message || JSON.stringify(raw),
    timestamp: raw.timestamp || raw.recorded_at,
    raw,
  };
}

export function normalizeSpatialPayload(data) {
  if (!data) {
    return null;
  }
  return {
    run_id: data.run_id,
    coordinate_mode: data.coordinate_mode,
    timestamp: data.timestamp,
    agents: (data.agents || []).map((agent) => ({
      id: agent.agent_id || agent.id,
      type: agent.agent_type || agent.type,
      status: agent.status == null ? undefined : String(agent.status).toLowerCase(),
      workflow_id: agent.current_workflow || agent.workflow_id || null,
      task_id: agent.current_task_id || agent.task_id || null,
      task_name: agent.current_task || agent.task_name || null,
      position:
        data.coordinate_mode === 'geo_osm'
          ? {
              lng: agent.display_position?.[1] ?? agent.position?.[0],
              lat: agent.display_position?.[0] ?? agent.position?.[1],
              z: agent.altitude ?? agent.position?.[2],
            }
          : {
              x: agent.display_position?.[1] ?? agent.position?.[0],
              y: agent.display_position?.[0] ?? agent.position?.[1],
              z: agent.altitude ?? agent.position?.[2],
            },
      recent_log: agent.recent_log || null,
    })),
  };
}

export function normalizeTrajectoryPayload(data, coordinateMode) {
  if (!data) {
    return null;
  }
  const list = Array.isArray(data) ? data : data.trajectories || [];
  return {
    coordinate_mode: data.coordinate_mode || coordinateMode,
    trajectories: list.map((trajectory) => ({
      agent_id: trajectory.agent_id,
      agent_type: trajectory.agent_type,
      points: (trajectory.points || []).map((point) =>
        (data.coordinate_mode || coordinateMode) === 'geo_osm'
          ? {
              lng: point.display_position?.[1] ?? point.position?.[0] ?? point.lng,
              lat: point.display_position?.[0] ?? point.position?.[1] ?? point.lat,
              z: point.position?.[2] ?? point.z,
            }
          : {
              x: point.display_position?.[1] ?? point.position?.[0] ?? point.x,
              y: point.display_position?.[0] ?? point.position?.[1] ?? point.y,
              z: point.position?.[2] ?? point.z,
            }
      ),
    })),
  };
}

