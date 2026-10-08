/** Legacy authoring helpers; never used by the descriptor-driven viewport. */
export function normalizeToken(value, ...stripWords) {
  let normalized = String(value || '').toLowerCase();
  ['_', '-', ' '].forEach((marker) => {
    normalized = normalized.split(marker).join('');
  });
  stripWords.forEach((word) => {
    normalized = normalized.split(String(word || '').toLowerCase()).join('');
  });
  return normalized;
}

export function normalizeAgentTypeToken(agentType) {
  return normalizeToken(agentType, 'agent');
}

export function normalizeWorkflowTypeToken(workflowType) {
  return normalizeToken(workflowType, 'workflow');
}

export function isObjectLike(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null);
}

export function defaultComponentsForDefinition(definition) {
  const compatible = new Set(definition?.compatible_components || []);
  const normalized = normalizeAgentTypeToken(definition?.id || definition?.name);
  let preferred = [];
  if (normalized.includes('station')) {
    preferred = [];
  } else if (normalized === 'deliverydrone' || normalized === 'delivery') {
    preferred = ['MoveToComponent', 'LogisticsComponent', 'ChargingComponent'];
  } else if (normalized === 'drone') {
    preferred = ['MoveToComponent', 'ChargingComponent'];
  }
  const filtered = preferred.filter((componentName) => compatible.has(componentName));
  if (filtered.length > 0 || preferred.length === 0) {
    return filtered;
  }
  return (definition?.compatible_components || []).slice(0, 2);
}

