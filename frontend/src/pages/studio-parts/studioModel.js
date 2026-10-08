// Shared local helpers for the workflow studio page tree.
import {
  defaultComponentsForDefinition,
  normalizeAgentTypeToken,
  normalizeWorkflowTypeToken,
} from '../../services/workbenchApi';

export function localizeKey(locale) {
  return locale === 'zh-CN' ? 'zh_CN' : 'en_US';
}

export function resolveDefinitionName(definition, locale) {
  if (!definition) {
    return '';
  }
  return (
    definition.display_name?.[localizeKey(locale)] ||
    definition.name ||
    definition.id ||
    definition.type ||
    ''
  );
}

export function defaultValueForTemplate(template) {
  if (template?.default !== undefined && template?.default !== null) {
    return template.default;
  }
  const type = String(template?.value_type || 'string').toLowerCase();
  if (type.includes('bool')) {
    return false;
  }
  if (type.includes('int') || type.includes('float') || type.includes('number')) {
    return 0;
  }
  if (type.includes('dict') || type.includes('object')) {
    return {};
  }
  if (type.includes('list') || type.includes('array')) {
    return [];
  }
  return '';
}

export function mergeTemplateDefaults(templateMap, current = {}, base = {}) {
  const next = { ...base, ...current };
  Object.entries(templateMap || {}).forEach(([key, template]) => {
    if (next[key] === undefined) {
      next[key] = defaultValueForTemplate(template);
    }
  });
  return next;
}

export function filterCompatibleComponents(existingComponents, definition) {
  const compatibleSet = new Set(definition?.compatible_components || []);
  const filtered = (existingComponents || []).filter((c) => compatibleSet.has(c));
  if (filtered.length > 0) {
    return filtered;
  }
  return defaultComponentsForDefinition(definition);
}

export function defaultInitialPositionForDefinition(definition, index) {
  const normalized = normalizeAgentTypeToken(definition?.id || definition?.name);
  const basePosition = [40 + index * 40, 60 + index * 20, 10];
  if (normalized.includes('station')) {
    return [basePosition[0], basePosition[1], 0];
  }
  if (normalized.includes('drone')) {
    return [basePosition[0], basePosition[1], 30];
  }
  return basePosition;
}

export function buildDefinitionRef(kind, definition) {
  if (!definition || definition.source !== 'custom') {
    return null;
  }
  return {
    kind,
    definition_id: definition.id,
    version: definition.version || null,
    source: 'custom',
  };
}

export function cloneValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => cloneValue(item));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneValue(item)])
    );
  }
  return value;
}

export function resolveAgentPosition(agent) {
  const rawPosition = agent?.initial_position || agent?.properties?.position || [0, 0, 0];
  return [0, 1, 2].map((index) => {
    const numeric = Number(rawPosition?.[index] ?? 0);
    return Number.isFinite(numeric) ? numeric : 0;
  });
}

export function resolveWorkflowFlightAltitude(agent) {
  const position = resolveAgentPosition(agent);
  const altitude = Number(position?.[2] ?? 0);
  return altitude > 0 ? altitude : 30;
}

export function withAltitude(position, altitude) {
  const normalized = resolveAgentPosition({ initial_position: position });
  return [normalized[0], normalized[1], altitude];
}

export function isCoordinate3d(value) {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((item) => typeof item === 'number' && !Number.isNaN(item))
  );
}

export function isCoordinateCollection(value) {
  return Array.isArray(value) && value.length > 0 && value.every((item) => isCoordinate3d(item));
}

export function isPayloadCollection(value) {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (item) =>
        item &&
        typeof item === 'object' &&
        !Array.isArray(item) &&
        typeof item.id === 'string' &&
        item.id
    )
  );
}

export function isCompatibleWorkflowOwner(definition, agent) {
  const workflowType = normalizeWorkflowTypeToken(definition?.id || definition?.name);
  const agentType = normalizeAgentTypeToken(agent?.type);
  if (workflowType === 'logistics') {
    return agentType === 'deliverydrone' || agentType === 'delivery';
  }
  if (['inspection', 'charging', 'imageprocessing'].includes(workflowType)) {
    return agentType === 'drone' || agentType === 'deliverydrone';
  }
  return true;
}

export function selectWorkflowOwnerId(definition, agents = [], preferredAgentId = '') {
  const preferred = (agents || []).find((agent) => agent.id === preferredAgentId);
  if (preferred && isCompatibleWorkflowOwner(definition, preferred)) {
    return preferred.id;
  }
  const compatible = (agents || []).find((agent) => isCompatibleWorkflowOwner(definition, agent));
  if (compatible) {
    return compatible.id;
  }
  return preferred?.id || agents?.[0]?.id || '';
}

export function selectLogisticsStations(agents = []) {
  const deliveryStations = (agents || []).filter(
    (agent) => normalizeAgentTypeToken(agent?.type) === 'deliverystation'
  );
  if (deliveryStations.length >= 1) {
    return {
      source: deliveryStations[0],
      target: deliveryStations[1] || deliveryStations[0],
    };
  }
  const stations = (agents || []).filter((agent) =>
    normalizeAgentTypeToken(agent?.type).includes('station')
  );
  return {
    source: stations[0] || null,
    target: stations[1] || stations[0] || null,
  };
}

export function buildWorkflowProperties(
  definition,
  workflowAgentId,
  agents = [],
  index = 0,
  currentProperties = {}
) {
  const properties = mergeTemplateDefaults(definition.property_templates || {}, currentProperties);
  const normalized = normalizeWorkflowTypeToken(definition?.id || definition?.name);
  const workflowAgent = (agents || []).find((agent) => agent.id === workflowAgentId) || null;

  if (normalized === 'inspection') {
    if (isCoordinateCollection(currentProperties.inspection_points)) {
      return properties;
    }
    const [x, y, z] = resolveAgentPosition(workflowAgent);
    return {
      ...properties,
      inspection_points: [
        [x + 40, y, z || 30],
        [x + 40, y + 40, z || 30],
      ],
    };
  }

  if (normalized === 'imageprocessing') {
    if (isCoordinateCollection(currentProperties.sensing_locations)) {
      return properties;
    }
    const [x, y, z] = resolveAgentPosition(workflowAgent);
    return {
      ...properties,
      sensing_locations: [
        [x + 30, y, z || 30],
        [x + 60, y + 20, z || 30],
      ],
    };
  }

  if (normalized !== 'logistics') {
    return properties;
  }

  const altitude = resolveWorkflowFlightAltitude(workflowAgent);
  const { source, target } = selectLogisticsStations(agents);
  const sourceAgentId =
    (agents || []).some((agent) => agent.id === currentProperties.source_agent_id)
      ? currentProperties.source_agent_id
      : source?.id || '';
  const targetAgentId =
    (agents || []).some((agent) => agent.id === currentProperties.target_agent_id)
      ? currentProperties.target_agent_id
      : target?.id || '';
  const sourceAgent = (agents || []).find((agent) => agent.id === sourceAgentId) || source || null;
  const targetAgent = (agents || []).find((agent) => agent.id === targetAgentId) || target || null;

  return {
    ...properties,
    pickup_location: isCoordinate3d(currentProperties.pickup_location)
      ? cloneValue(currentProperties.pickup_location)
      : withAltitude(resolveAgentPosition(sourceAgent || workflowAgent), altitude),
    delivery_location: isCoordinate3d(currentProperties.delivery_location)
      ? cloneValue(currentProperties.delivery_location)
      : withAltitude(resolveAgentPosition(targetAgent || workflowAgent), altitude),
    payloads: isPayloadCollection(currentProperties.payloads)
      ? cloneValue(currentProperties.payloads)
      : [
          {
            id: `payload_${index + 1}`,
            description: 'Starter logistics payload',
          },
        ],
    source_agent_id: sourceAgentId,
    target_agent_id: targetAgentId,
  };
}

export function buildAgentInstance(definition, index) {
  const definitionRef = buildDefinitionRef('agents', definition);
  const initialPosition = defaultInitialPositionForDefinition(definition, index);
  const properties = mergeTemplateDefaults(
    definition.state_templates || {},
    {},
    definition.default_properties || {}
  );
  if (properties.position === undefined) {
    properties.position = initialPosition;
  }
  return {
    id: `agent_${index + 1}`,
    name: resolveDefinitionName(definition, 'en-US') || `Agent ${index + 1}`,
    type: definition.id,
    source: definition.source || 'builtin',
    version: definition.version || null,
    definition_ref: definitionRef,
    registry_ref: definitionRef,
    initial_position: initialPosition,
    initial_battery: 100,
    components: defaultComponentsForDefinition(definition),
    properties,
  };
}

export function buildWorkflowInstance(definition, agents = [], index = 0, preferredAgentId = '') {
  const definitionRef = buildDefinitionRef('workflows', definition);
  const workflowAgentId = selectWorkflowOwnerId(definition, agents, preferredAgentId);
  return {
    id: `workflow_${index + 1}`,
    name: resolveDefinitionName(definition, 'en-US') || `Workflow ${index + 1}`,
    type: definition.id,
    source: definition.source || 'builtin',
    version: definition.version || null,
    definition_ref: definitionRef,
    registry_ref: definitionRef,
    agent_id: workflowAgentId,
    enabled: true,
    properties: buildWorkflowProperties(definition, workflowAgentId, agents, index),
  };
}

export function isNumeric(valueType) {
  const type = String(valueType || '').toLowerCase();
  return type.includes('int') || type.includes('float') || type.includes('number');
}

export function isBoolean(valueType) {
  return String(valueType || '').toLowerCase().includes('bool');
}

export function isStructured(valueType) {
  const type = String(valueType || '').toLowerCase();
  return (
    type.includes('list') ||
    type.includes('array') ||
    type.includes('dict') ||
    type.includes('object')
  );
}
