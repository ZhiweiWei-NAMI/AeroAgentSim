export function normalizeTemplateMap(templateMap) {
  if (!templateMap) {
    return {};
  }
  if (Array.isArray(templateMap)) {
    return templateMap.reduce((result, item) => {
      if (item?.key) {
        result[item.key] = item;
      }
      return result;
    }, {});
  }
  return templateMap;
}

export function normalizeTemplateList(templateMap) {
  return Object.entries(normalizeTemplateMap(templateMap)).map(([key, value]) => ({
    key,
    value_type: value?.value_type || 'any',
    required: value?.required,
    default: value?.default,
    description: value?.description || '',
  }));
}

export function normalizeRegistryMeta(meta = {}) {
  return {
    id: meta.id,
    version: meta.version,
    schema_version: meta.schema_version,
    source: meta.source,
    display_name: meta.display_name || { zh_CN: '', en_US: '' },
    description: meta.description || { zh_CN: '', en_US: '' },
    created_at: meta.created_at || null,
    updated_at: meta.updated_at || null,
    enabled: meta.enabled,
  };
}

export function flattenRegistryDefinition(kind, item) {
  if (!item) {
    return null;
  }
  const source = item.meta ? item.meta : item;
  const meta = normalizeRegistryMeta(source);
  return {
    ...item,
    id: meta.id,
    version: meta.version,
    schema_version: meta.schema_version,
    source: meta.source,
    enabled: meta.enabled,
    display_name: meta.display_name,
    description: meta.description,
    allowed_components: item.allowed_components || item.compatible_components || [],
    adapter_type: item.adapter_type || item.adapter_task_type || item.workflow_family || '',
    supported_agent_types: item.supported_agent_types || [],
  };
}

export function toRegistryPayload(kind, payload) {
  if (payload?.meta) {
    return payload;
  }
  const meta = {
    id: payload.id,
    version: payload.version,
    schema_version: payload.schema_version || '1.0',
    source: 'custom',
    enabled: payload.enabled !== false,
    display_name: payload.display_name || { zh_CN: '', en_US: '' },
    description: payload.description || { zh_CN: '', en_US: '' },
  };

  if (kind === 'agents') {
    return {
      kind,
      meta,
      base_agent_type: payload.base_agent_type,
      compatible_components: payload.allowed_components || payload.compatible_components || [],
      state_templates: payload.state_templates || {},
      initialization_schema: payload.initialization_schema || {},
      default_properties: payload.default_properties || {},
    };
  }

  if (kind === 'tasks') {
    return {
      kind,
      meta,
      adapter_task_type: payload.adapter_task_type || payload.adapter_type,
      component: payload.component,
      necessary_metrics: payload.necessary_metrics || [],
      produced_states: payload.produced_states || [],
      parameter_schema: payload.parameter_schema || {},
      target_state_schema: payload.target_state_schema || {},
    };
  }

  return {
    kind,
    meta,
    workflow_family: payload.workflow_family || payload.adapter_type || 'custom',
    property_templates: payload.property_templates || {},
    states: payload.states || [],
    start_state: payload.start_state,
    trigger_conditions: payload.trigger_conditions || [],
    task_bindings: payload.task_bindings || [],
    critical_path: payload.critical_path || [],
  };
}

export function normalizeCatalogItem(item, kind) {
  if (!item) {
    return null;
  }
  const templates =
    kind === 'agents'
      ? normalizeTemplateMap(item.state_templates)
      : kind === 'workflows'
        ? normalizeTemplateMap(item.property_templates)
        : normalizeTemplateMap(item.parameter_schema);

  return {
    id: item.id || item.type || item.name,
    type: item.id || item.type || item.name,
    name: item.name || item.id || item.type,
    description: item.description || '',
    source: item.source,
    version: item.version,
    display_name: item.display_name || null,
    registry_ref:
      item.registry_ref ||
      item.definition_ref ||
      (item.source === 'custom'
        ? {
            kind,
            definition_id: item.id || item.type || item.name,
            version: item.version || null,
            source: item.source || 'custom',
          }
        : null),
    state_templates: normalizeTemplateMap(item.state_templates),
    property_templates: normalizeTemplateMap(item.property_templates),
    parameter_schema: normalizeTemplateMap(item.parameter_schema),
    target_state_schema: normalizeTemplateMap(item.target_state_schema),
    compatible_components: item.compatible_components || [],
    produced_metrics: item.produced_metrics || [],
    monitored_states: item.monitored_states || [],
    necessary_metrics: item.necessary_metrics || [],
    produced_states: item.produced_states || [],
    states: item.states || [],
    start_state: item.start_state || null,
    trigger_conditions: item.trigger_conditions || [],
    task_bindings: item.task_bindings || [],
    critical_path: item.critical_path || [],
    workflow_family: item.workflow_family || null,
    base_agent_type: item.base_agent_type || null,
    initialization_schema: normalizeTemplateMap(item.initialization_schema),
    default_properties: item.default_properties || {},
    template_list: normalizeTemplateList(templates),
  };
}

export function normalizeCompatibilityMatrix(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== 'object') throw Error('Compatibility response must contain a real matrix');
  const result = [];
  for (const [relation, key] of [['agent compatible with component', 'agent_components'], ['component compatible with task', 'component_tasks'], ['workflow compatible with agent', 'workflow_agents'], ['workflow suggests task', 'workflow_tasks']]) {
    for (const [source, targets] of Object.entries(data[key] || {})) {
      for (const target of targets) result.push({ relation, source, target });
    }
  }
  return result;
}
