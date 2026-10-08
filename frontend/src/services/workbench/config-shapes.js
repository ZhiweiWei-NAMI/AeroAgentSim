export function normalizeConfig(data) {
  if (!data) {
    return null;
  }
  return {
    config_id: data.config_id,
    name: data.name || data?.metadata?.name,
    coordinate_mode: data.coordinate_mode,
    metadata: {
      name: data.name || data?.metadata?.name,
      created_at: data.created_at || data?.metadata?.created_at || null,
      updated_at: data.updated_at || data?.metadata?.updated_at || null,
    },
    traffic: data.traffic || {},
    agents: (data.agents || []).map((agent) => {
      const source = agent.source || agent.definition_ref?.source;
      return {
        id: agent.id,
        name: agent.name,
        type: agent.type,
        source,
        version: agent.version || agent.definition_ref?.version || null,
        definition_ref: agent.definition_ref || agent.registry_ref || null,
        registry_ref: agent.definition_ref || agent.registry_ref || null,
        initial_position: agent.initial_position || agent.position,
        initial_battery:
          agent.initial_battery ??
          agent.battery ??
          agent.properties?.battery_level,
        components: agent.components || [],
        properties: agent.properties || {},
      };
    }),
    workflows: (data.workflows || []).map((workflow) => {
      const source = workflow.source || workflow.definition_ref?.source;
      return {
        id: workflow.id,
        name: workflow.name,
        type: workflow.type,
        source,
        version: workflow.version || workflow.definition_ref?.version || null,
        definition_ref: workflow.definition_ref || workflow.registry_ref || null,
        registry_ref: workflow.definition_ref || workflow.registry_ref || null,
        agent_id: workflow.agent_id,
        enabled: workflow.enabled,
        properties: workflow.properties || workflow.parameters || workflow.details || {},
      };
    }),
  };
}

export function serializeConfigForApi(configData) {
  return {
    config_id: configData.config_id,
    name: configData?.metadata?.name || configData?.name,
    coordinate_mode: configData.coordinate_mode,
    traffic: configData.traffic || {},
    agents: (configData.agents || []).map((agent) => ({
      id: agent.id,
      name: agent.name,
      type: agent.type,
      source: agent.source || (agent.definition_ref?.source),
      version: agent.version || agent.definition_ref?.version || undefined,
      definition_ref: agent.definition_ref || agent.registry_ref || undefined,
      initial_position: agent.initial_position,
      initial_battery:
        agent.initial_battery ??
        agent.properties?.battery_level ??
        agent.battery,
      components: agent.components || [],
      properties: agent.properties || {},
    })),
    workflows: (configData.workflows || []).map((workflow) => ({
      id: workflow.id,
      name: workflow.name,
      type: workflow.type,
      source: workflow.source || (workflow.definition_ref?.source),
      version: workflow.version || workflow.definition_ref?.version || undefined,
      definition_ref: workflow.definition_ref || workflow.registry_ref || undefined,
      agent_id: workflow.agent_id,
      enabled: workflow.enabled,
      properties: workflow.properties || {},
    })),
  };
}

export function normalizeValidation(data) {
  if (!data) {
    return null;
  }
  const issues = [
    ...(data.errors || []).map((message) => ({ level: 'error', category: 'errors', message })),
    ...(data.missing_dependencies || []).map((message) => ({
      level: 'error',
      category: 'missing_dependencies',
      message,
    })),
    ...(data.unresolved_proxies || []).map((message) => ({
      level: 'error',
      category: 'unresolved_proxies',
      message,
    })),
    ...(data.version_conflicts || []).map((message) => ({
      level: 'error',
      category: 'version_conflicts',
      message,
    })),
    ...(data.compatibility_gaps || []).map((message) => ({
      level: 'warning',
      category: 'compatibility_gaps',
      message,
    })),
    ...(data.graph_warnings || []).map((message) => ({
      level: 'warning',
      category: 'graph_warnings',
      message,
    })),
    ...(data.warnings || []).map((message) => ({ level: 'warning', category: 'warnings', message })),
  ];
  return {
    valid: data.valid ?? data.is_valid,
    issues: [
      ...(Array.isArray(data.issues) ? data.issues : []),
      ...issues,
    ],
    checked_at: data.checked_at,
    source: 'api',
    blockers: (data.errors || []).slice(),
    missing_dependencies: data.missing_dependencies || [],
    compatibility_gaps: data.compatibility_gaps || [],
    unresolved_proxies: data.unresolved_proxies || [],
    version_conflicts: data.version_conflicts || [],
    graph_warnings: data.graph_warnings || [],
  };
}

export function normalizePreflight(data) {
  if (!data) {
    return {
      is_ready: undefined,
      errors: [],
      warnings: [],
      checks: [],
    };
  }
  return {
    is_ready: data.is_ready,
    errors: data.errors || [],
    warnings: data.warnings || [],
    checks: data.checks || [],
  };
}

export function normalizeGraph(data) {
  if (!data) return null;
  return { ...data };
}
