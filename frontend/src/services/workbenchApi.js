import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || '/api';
const WS_BASE_URL =
  process.env.REACT_APP_WS_BASE_URL ||
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;
const ENABLE_LOCAL_FALLBACK = process.env.REACT_APP_ENABLE_MOCK_FALLBACK !== 'false';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 10000,
});

export const MOCK_CONFIG_ID = 'default';

const LS_CONFIGS_KEY = 'aeroagentsim.configs';
const LS_REGISTRY_KEY = 'aeroagentsim.registry';
const CONNECTIVITY_ERROR_CODES = new Set(['ERR_NETWORK', 'ECONNABORTED', 'ETIMEDOUT']);
const SUPPORTED_WORKBENCH_WORKFLOW_TOKENS = new Set([
  'inspection',
  'charging',
  'logistics',
  'imageprocessing',
]);

const defaultConfig = {
  config_id: MOCK_CONFIG_ID,
  name: 'AeroAgentSim Logistics Config',
  coordinate_mode: 'simulation_plane',
  metadata: {
    name: 'AeroAgentSim Logistics Config',
    updated_at: new Date().toISOString(),
  },
  traffic: {},
  agents: [
    {
      id: 'station_source',
      name: 'Source Station',
      type: 'delivery_station',
      registry_ref: null,
      initial_position: [20, 40, 0],
      initial_battery: 100,
      components: [],
      properties: {
        position: [20, 40, 0],
        storage_capacity: 20,
        service_radius: 120,
      },
    },
    {
      id: 'station_target',
      name: 'Target Station',
      type: 'delivery_station',
      registry_ref: null,
      initial_position: [240, 180, 0],
      initial_battery: 100,
      components: [],
      properties: {
        position: [240, 180, 0],
        storage_capacity: 20,
        service_radius: 120,
      },
    },
    {
      id: 'delivery_drone_alpha',
      name: 'Delivery Drone Alpha',
      type: 'delivery_drone_agent',
      registry_ref: null,
      initial_position: [120, 20, 30],
      initial_battery: 92,
      components: ['MoveToComponent', 'LogisticsComponent', 'ChargingComponent'],
      properties: {
        battery_level: 92,
        max_payload_weight: 5,
        max_payload_volume: 1,
      },
    },
  ],
  workflows: [
    {
      id: 'workflow_logistics_1',
      name: 'Logistics Path Alpha',
      type: 'logistics_workflow',
      registry_ref: null,
      agent_id: 'delivery_drone_alpha',
      enabled: true,
      properties: {
        pickup_location: [20, 40, 30],
        delivery_location: [240, 180, 30],
        payloads: [
          {
            id: 'payload_demo',
            weight: 1.2,
            dimensions: [0.25, 0.15, 0.1],
            description: 'Starter logistics payload',
          },
        ],
        source_agent_id: 'station_source',
        target_agent_id: 'station_target',
      },
    },
  ],
};

const defaultRegistry = {
  agents: [],
  tasks: [],
  workflows: [],
};

// ---------------------------------------------------------------------------
// Builtin catalog fallback — used when the backend is unreachable so that the
// Workflow Studio can still add / edit agents, workflows, etc.
// ---------------------------------------------------------------------------
const defaultCatalog = {
  agents: [
    {
      id: 'drone_agent',
      name: 'DroneAgent',
      description: 'Drone agent with intelligent task planning, inspection and charging workflows',
      source: 'builtin',
      version: 'builtin',
      state_templates: {
        position: { value_type: 'list', required: true },
        speed: { value_type: 'float', required: false },
        battery_level: { value_type: 'float', required: true },
        moving_status: { value_type: 'str', required: true, default: 'idle' },
        direction: { value_type: 'tuple', required: false },
        distance_traveled: { value_type: 'float', required: false, default: 0 },
        altitude: { value_type: 'float', required: false },
        max_allowed_speed: { value_type: 'float', required: false },
        battery_capacity: { value_type: 'float', required: false },
        charge_cycles: { value_type: 'int', required: false, default: 0 },
        computation_load: { value_type: 'float', required: false, default: 0 },
        external_force: { value_type: 'list', required: false },
        mobility_power_consumption: { value_type: 'float', required: false, default: 0 },
        computing_power_consumption: { value_type: 'float', required: false, default: 0 },
        relay_power_consumption: { value_type: 'float', required: false, default: 0 },
        logistics_power_consumption: { value_type: 'float', required: false, default: 0 },
        inspection_power_consumption: { value_type: 'float', required: false, default: 0 },
        communication_power: { value_type: 'float', required: false, default: 0 },
      },
      compatible_components: [
        'MoveToComponent',
        'ChargingComponent',
        'CPUComponent',
        'ComputationComponent',
        'CommunicationComponent',
        'ImageSensingComponent',
        'LogisticsComponent',
        'EMSensingComponent',
        'ObjectSensorComponent',
      ],
    },
    {
      id: 'delivery_agent',
      name: 'DeliveryAgent',
      description: 'Delivery agent for logistics tasks with pickup, transport and delivery workflows',
      source: 'builtin',
      version: 'builtin',
      state_templates: {
        position: { value_type: 'list', required: true },
        speed: { value_type: 'float', required: false },
        battery_level: { value_type: 'float', required: true },
        moving_status: { value_type: 'str', required: true, default: 'idle' },
        payload_ids: { value_type: 'list', required: false, default: [] },
        current_payload_weight: { value_type: 'float', required: true, default: 0 },
        max_payload_weight: { value_type: 'float', required: true },
        current_payload_volume: { value_type: 'float', required: true, default: 0 },
        max_payload_volume: { value_type: 'float', required: true },
        delivery_status: { value_type: 'str', required: true, default: 'idle' },
      },
      compatible_components: [
        'MoveToComponent',
        'ChargingComponent',
        'LogisticsComponent',
      ],
    },
    {
      id: 'delivery_drone_agent',
      name: 'DeliveryDroneAgent',
      description: 'Delivery drone agent combining drone and delivery capabilities',
      source: 'builtin',
      version: 'builtin',
      state_templates: {
        position: { value_type: 'list', required: true },
        speed: { value_type: 'float', required: false },
        battery_level: { value_type: 'float', required: true },
        moving_status: { value_type: 'str', required: true, default: 'idle' },
        altitude: { value_type: 'float', required: false },
        payload_ids: { value_type: 'list', required: false, default: [] },
        current_payload_weight: { value_type: 'float', required: true, default: 0 },
        max_payload_weight: { value_type: 'float', required: true },
        current_payload_volume: { value_type: 'float', required: true, default: 0 },
        max_payload_volume: { value_type: 'float', required: true },
        delivery_status: { value_type: 'str', required: true, default: 'idle' },
      },
      compatible_components: [
        'MoveToComponent',
        'ChargingComponent',
        'CPUComponent',
        'ComputationComponent',
        'CommunicationComponent',
        'ImageSensingComponent',
        'LogisticsComponent',
        'EMSensingComponent',
        'ObjectSensorComponent',
      ],
    },
    {
      id: 'delivery_station',
      name: 'DeliveryStation',
      description: 'Stationary logistics hub that stores payloads and acts as a logistics source or sink',
      source: 'builtin',
      version: 'builtin',
      state_templates: {
        position: { value_type: 'list', required: true },
        storage_capacity: { value_type: 'int', required: true, default: 100 },
        current_storage: { value_type: 'int', required: true, default: 0 },
        service_radius: { value_type: 'float', required: true, default: 50 },
        registered_logistics_drones: { value_type: 'list', required: false, default: [] },
        payload_generation_model: { value_type: 'dict', required: false, default: { properties: {} } },
      },
      compatible_components: [],
      default_properties: {
        storage_capacity: 100,
        current_storage: 0,
        service_radius: 50,
        registered_logistics_drones: [],
        payload_generation_model: { properties: {} },
      },
    },
  ],
  components: [
    {
      id: 'move_to_component',
      name: 'MoveToComponent',
      description: 'MoveToComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['speed', 'energy_consumption', 'direction'],
      monitored_states: ['battery_level', 'external_force', 'moving_status', 'max_allowed_speed'],
    },
    {
      id: 'charging_component',
      name: 'ChargingComponent',
      description: 'ChargingComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['charging_rate', 'request_processing_time'],
      monitored_states: ['battery_capacity', 'position', 'charging_station.current_allocations', 'charging_station.power_level'],
    },
    {
      id: 'cpu_component',
      name: 'CPUComponent',
      description: 'CPUComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['processing_power'],
      monitored_states: ['battery_level', 'cpu_usage', 'memory_usage'],
    },
    {
      id: 'computation_component',
      name: 'ComputationComponent',
      description: 'ComputationComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['processing_power', 'computation_efficiency'],
      monitored_states: ['battery_level', 'cpu_usage', 'memory_usage', 'computing_status'],
    },
    {
      id: 'communication_component',
      name: 'CommunicationComponent',
      description: 'CommunicationComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['signal_strength', 'bandwidth', 'latency', 'transmission_rate', 'communication_quality'],
      monitored_states: ['battery_level', 'position', 'trans_target_agent_id', 'status', 'transmitting_status'],
    },
    {
      id: 'image_sensing_component',
      name: 'ImageSensingComponent',
      description: 'ImageSensingComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['sensing_capability', 'sensing_efficiency', 'sensing_range'],
      monitored_states: ['battery_level', 'image_sensing_status', 'position'],
    },
    {
      id: 'logistics_component',
      name: 'LogisticsComponent',
      description: 'LogisticsComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['pickup_processing_time', 'handover_processing_time'],
      monitored_states: ['position', 'battery_level', 'payload_ids', 'current_payload_weight', 'max_payload_weight', 'current_payload_volume', 'max_payload_volume'],
    },
    {
      id: 'em_sensing_component',
      name: 'EMSensingComponent',
      description: 'EMSensingComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['em_sensor_sensitivity', 'em_sensor_power_consumption', 'em_sensor_accuracy_identification', 'em_sensor_accuracy_power', 'em_sensor_accuracy_frequency', 'em_sensor_accuracy_direction'],
      monitored_states: ['position', 'status', 'battery_level'],
    },
    {
      id: 'object_sensor_component',
      name: 'ObjectSensorComponent',
      description: 'ObjectSensorComponent component',
      source: 'builtin',
      version: 'builtin',
      produced_metrics: ['sensor_accuracy_position', 'sensor_accuracy_classification', 'sensor_power_consumption'],
      monitored_states: ['position', 'status'],
    },
  ],
  tasks: [
    {
      id: 'move_to_task',
      name: 'MoveToTask',
      description: 'MoveToTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['speed'],
      produced_states: ['position', 'direction', 'distance_traveled', 'altitude', 'battery_level', 'status', 'moving_status'],
    },
    {
      id: 'request_charging_station_task',
      name: 'RequestChargingStationTask',
      description: 'RequestChargingStationTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['request_processing_time'],
      produced_states: ['status'],
    },
    {
      id: 'charging_task',
      name: 'ChargingTask',
      description: 'ChargingTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['charging_rate'],
      produced_states: ['battery_level', 'status', 'charge_cycles'],
    },
    {
      id: 'file_compute_task',
      name: 'FileComputeTask',
      description: 'FileComputeTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['processing_power', 'computation_efficiency'],
      produced_states: ['computing_status', 'compute_progress', 'compute_speed'],
    },
    {
      id: 'file_transfer_task',
      name: 'FileTransferTask',
      description: 'FileTransferTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['transmission_rate', 'latency', 'communication_quality'],
      produced_states: ['trans_target_agent_id', 'transmission_progress', 'transmission_speed', 'transmitting_status'],
    },
    {
      id: 'file_collect_task',
      name: 'FileCollectTask',
      description: 'FileCollectTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['sensing_capability', 'sensing_efficiency'],
      produced_states: ['image_sensing_status', 'sensing_progress', 'sensing_speed'],
    },
    {
      id: 'pickup_task',
      name: 'PickupTask',
      description: 'PickupTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['pickup_processing_time'],
      produced_states: ['status', 'delivery_status', 'payload_ids', 'current_payload_weight', 'current_payload_volume'],
    },
    {
      id: 'handover_task',
      name: 'HandoverTask',
      description: 'HandoverTask task',
      source: 'builtin',
      version: 'builtin',
      necessary_metrics: ['handover_processing_time'],
      produced_states: ['status', 'delivery_status', 'payload_ids', 'current_payload_weight', 'current_payload_volume'],
    },
  ],
  workflows: [
    {
      id: 'inspection_workflow',
      name: 'InspectionWorkflow',
      description: 'Inspection workflow guiding drones to visit inspection points in sequence',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        inspection_points: { value_type: 'list', required: true },
      },
      states: ['idle', 'moving_to_point', 'inspecting', 'returning', 'completed'],
      start_state: 'idle',
    },
    {
      id: 'charging_workflow',
      name: 'ChargingWorkflow',
      description: 'Charging workflow monitoring battery and guiding drones to charging stations',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        battery_threshold: { value_type: 'float', required: true, default: 20 },
        target_charge_level: { value_type: 'float', required: true, default: 90 },
      },
      states: ['monitoring', 'requesting_station', 'moving_to_station', 'charging', 'charged'],
      start_state: 'monitoring',
    },
    {
      id: 'logistics_workflow',
      name: 'LogisticsWorkflow',
      description: 'Logistics workflow managing pickup, transport and delivery of goods',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        pickup_location: { value_type: 'list', required: true },
        delivery_location: { value_type: 'list', required: true },
        payloads: { value_type: 'list', required: true },
        source_agent_id: { value_type: 'str', required: true },
        target_agent_id: { value_type: 'str', required: true },
      },
      states: ['idle', 'moving_to_pickup', 'picking_up', 'transporting', 'delivering', 'completed'],
      start_state: 'idle',
    },
    {
      id: 'image_processing_workflow',
      name: 'ImageProcessingWorkflow',
      description: 'Image processing workflow for environment sensing and data processing',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        sensing_locations: { value_type: 'list', required: true },
        image_resolution: { value_type: 'str', required: false, default: '1920x1080' },
        image_format: { value_type: 'str', required: false, default: 'jpeg' },
      },
      states: ['idle', 'moving_to_location', 'sensing', 'processing', 'transferring', 'completed'],
      start_state: 'idle',
    },
  ],
  compatibility: {
    agent_components: {
      drone_agent: [
        'ChargingComponent', 'CommunicationComponent', 'CPUComponent',
        'ComputationComponent', 'EMSensingComponent', 'ImageSensingComponent',
        'LogisticsComponent', 'MoveToComponent', 'ObjectSensorComponent',
      ],
      delivery_agent: ['ChargingComponent', 'LogisticsComponent', 'MoveToComponent'],
      delivery_drone_agent: [
        'ChargingComponent', 'CommunicationComponent', 'CPUComponent',
        'ComputationComponent', 'EMSensingComponent', 'ImageSensingComponent',
        'LogisticsComponent', 'MoveToComponent', 'ObjectSensorComponent',
      ],
      delivery_station: [],
    },
    component_tasks: {
      MoveToComponent: ['move_to_task'],
      ChargingComponent: ['charging_task', 'request_charging_station_task'],
      CPUComponent: [],
      ComputationComponent: ['file_compute_task'],
      CommunicationComponent: ['file_transfer_task'],
      ImageSensingComponent: ['file_collect_task'],
      LogisticsComponent: ['pickup_task', 'handover_task'],
      EMSensingComponent: [],
      ObjectSensorComponent: [],
    },
    workflow_agents: {
      inspection_workflow: ['drone_agent', 'delivery_drone_agent'],
      charging_workflow: ['drone_agent', 'delivery_drone_agent'],
      logistics_workflow: ['delivery_agent', 'delivery_drone_agent'],
      image_processing_workflow: ['drone_agent', 'delivery_drone_agent'],
    },
    workflow_tasks: {
      inspection_workflow: ['move_to_task'],
      charging_workflow: ['charging_task', 'move_to_task', 'request_charging_station_task'],
      logistics_workflow: ['handover_task', 'move_to_task', 'pickup_task'],
      image_processing_workflow: ['file_collect_task', 'file_compute_task', 'file_transfer_task', 'move_to_task'],
    },
  },
};

function normalizeToken(value, ...stripWords) {
  let normalized = String(value || '').toLowerCase();
  ['_', '-', ' '].forEach((marker) => {
    normalized = normalized.split(marker).join('');
  });
  stripWords.forEach((word) => {
    normalized = normalized.split(String(word || '').toLowerCase()).join('');
  });
  return normalized;
}

function normalizeAgentTypeToken(agentType) {
  return normalizeToken(agentType, 'agent');
}

function normalizeWorkflowTypeToken(workflowType) {
  return normalizeToken(workflowType, 'workflow');
}

function isObjectLike(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null);
}

function isSupportedWorkbenchWorkflowReference(workflowRef, source = 'builtin') {
  if (!workflowRef) {
    return false;
  }
  if (source === 'custom' || String(workflowRef).includes('@')) {
    return true;
  }
  return SUPPORTED_WORKBENCH_WORKFLOW_TOKENS.has(normalizeWorkflowTypeToken(workflowRef));
}

function filterSupportedWorkflowDefinitions(definitions) {
  return (definitions || []).filter((definition) =>
    isSupportedWorkbenchWorkflowReference(
      definition?.id || definition?.type || definition?.name,
      definition?.source || 'builtin'
    )
  );
}

function isCoordinate3d(value) {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((item) => typeof item === 'number' && !Number.isNaN(item))
  );
}

function defaultComponentsForDefinition(definition) {
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

function resolveCatalogIdByToken(items, rawValue, normalizeValue) {
  if (!rawValue) {
    return rawValue;
  }
  const exactMatch = (items || []).find((item) =>
    [item?.id, item?.type, item?.name].includes(rawValue)
  );
  if (exactMatch?.id) {
    return exactMatch.id;
  }
  const normalized = normalizeValue(rawValue);
  const tokenMatch = (items || []).find(
    (item) => normalizeValue(item?.id || item?.type || item?.name) === normalized
  );
  return tokenMatch?.id || rawValue;
}

function normalizeBuiltinAgentTypeId(agentType) {
  return resolveCatalogIdByToken(defaultCatalog.agents, agentType, normalizeAgentTypeToken);
}

function normalizeBuiltinWorkflowTypeId(workflowType) {
  return resolveCatalogIdByToken(defaultCatalog.workflows, workflowType, normalizeWorkflowTypeToken);
}

function findBuiltinAgentDefinition(agentType) {
  const normalized = normalizeAgentTypeToken(agentType);
  return (
    defaultCatalog.agents.find(
      (definition) => normalizeAgentTypeToken(definition.id || definition.name) === normalized
    ) || null
  );
}

function readLocalJson(key, fallbackValue) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      return fallbackValue;
    }
    return JSON.parse(raw);
  } catch (_error) {
    return fallbackValue;
  }
}

function writeLocalJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function ensureLocalConfig(configId = MOCK_CONFIG_ID) {
  const allConfigs = readLocalJson(LS_CONFIGS_KEY, {});
  if (!allConfigs[configId]) {
    allConfigs[configId] = {
      ...defaultConfig,
      config_id: configId,
      metadata: {
        ...defaultConfig.metadata,
        updated_at: new Date().toISOString(),
      },
    };
    writeLocalJson(LS_CONFIGS_KEY, allConfigs);
  }
  return allConfigs[configId];
}

function saveLocalConfig(configId, configData) {
  const allConfigs = readLocalJson(LS_CONFIGS_KEY, {});
  allConfigs[configId] = {
    ...configData,
    config_id: configId,
    metadata: {
      ...(configData.metadata || {}),
      updated_at: new Date().toISOString(),
    },
  };
  writeLocalJson(LS_CONFIGS_KEY, allConfigs);
  return allConfigs[configId];
}

function ensureLocalRegistry() {
  const registry = readLocalJson(LS_REGISTRY_KEY, null);
  if (registry) {
    return registry;
  }
  writeLocalJson(LS_REGISTRY_KEY, defaultRegistry);
  return defaultRegistry;
}

function saveLocalRegistry(registry) {
  writeLocalJson(LS_REGISTRY_KEY, registry);
  return registry;
}

function normalizeTemplateMap(templateMap) {
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

function normalizeTemplateList(templateMap) {
  return Object.entries(normalizeTemplateMap(templateMap)).map(([key, value]) => ({
    key,
    value_type: value?.value_type || 'any',
    required: Boolean(value?.required),
    default: value?.default,
    description: value?.description || '',
  }));
}

function normalizeRegistryMeta(meta = {}) {
  return {
    id: meta.id || '',
    version: meta.version || '1.0.0',
    schema_version: meta.schema_version || '1.0',
    source: meta.source || 'custom',
    display_name: meta.display_name || { zh_CN: '', en_US: '' },
    description: meta.description || { zh_CN: '', en_US: '' },
    created_at: meta.created_at || null,
    updated_at: meta.updated_at || null,
    enabled: meta.enabled !== false,
  };
}

function inferComponentFromAdapter(adapterType) {
  const mapping = {
    MoveToTask: 'MoveToComponent',
    ChargingTask: 'ChargingComponent',
    RequestChargingStationTask: 'ChargingComponent',
    PickupTask: 'MoveToComponent',
    HandoverTask: 'MoveToComponent',
    FileCollectTask: 'MoveToComponent',
    FileTransferTask: 'MoveToComponent',
    FileComputeTask: 'MoveToComponent',
  };
  return mapping[adapterType] || 'MoveToComponent';
}

function flattenRegistryDefinition(kind, item) {
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

function toRegistryPayload(kind, payload) {
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
      base_agent_type: payload.base_agent_type || 'DroneAgent',
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
      adapter_task_type: payload.adapter_task_type || payload.adapter_type || 'MoveToTask',
      component: payload.component || inferComponentFromAdapter(payload.adapter_type || payload.adapter_task_type),
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
    start_state: payload.start_state || 'idle',
    trigger_conditions: payload.trigger_conditions || [],
    task_bindings: payload.task_bindings || [],
    critical_path: payload.critical_path || [],
  };
}

function normalizeCatalogItem(item, kind) {
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
    source: item.source || 'builtin',
    version: item.version || 'builtin',
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

function normalizeCompatibilityMatrix(data) {
  if (Array.isArray(data)) {
    return data.filter((item) => {
      const relation = String(item?.relation || '').toLowerCase();
      if (!relation.includes('workflow')) {
        return true;
      }
      return isSupportedWorkbenchWorkflowReference(item?.source);
    });
  }
  if (!data || typeof data !== 'object') {
    return [];
  }
  const result = [];
  const pushRelations = (relation, sourceMap) => {
    Object.entries(sourceMap || {}).forEach(([source, targets]) => {
      (targets || []).forEach((target) => {
        result.push({
          relation,
          source,
          target,
          score: 1,
        });
      });
    });
  };
  pushRelations('agent compatible with component', data.agent_components);
  pushRelations('component compatible with task', data.component_tasks);
  pushRelations('workflow compatible with agent', data.workflow_agents);
  pushRelations('workflow suggests task', data.workflow_tasks);
  return result.filter((item) => {
    if (!String(item?.relation || '').toLowerCase().includes('workflow')) {
      return true;
    }
    return isSupportedWorkbenchWorkflowReference(item?.source);
  });
}

function normalizeConfig(data) {
  if (!data) {
    return null;
  }
  return {
    config_id: data.config_id || MOCK_CONFIG_ID,
    name: data.name || data?.metadata?.name || 'AeroAgentSim Config',
    coordinate_mode: data.coordinate_mode || 'simulation_plane',
    metadata: {
      name: data.name || data?.metadata?.name || 'AeroAgentSim Config',
      created_at: data.created_at || data?.metadata?.created_at || null,
      updated_at: data.updated_at || data?.metadata?.updated_at || null,
    },
    traffic: data.traffic || {},
    agents: (data.agents || []).map((agent) => {
      const source = agent.source || agent.definition_ref?.source || 'builtin';
      return {
        id: agent.id,
        name: agent.name,
        type: source === 'builtin' ? normalizeBuiltinAgentTypeId(agent.type) : agent.type,
        source,
        version: agent.version || agent.definition_ref?.version || null,
        definition_ref: agent.definition_ref || agent.registry_ref || null,
        registry_ref: agent.definition_ref || agent.registry_ref || null,
        initial_position: agent.initial_position || agent.position || [0, 0, 0],
        initial_battery:
          agent.initial_battery ??
          agent.battery ??
          agent.properties?.battery_level ??
          100,
        components: agent.components || [],
        properties: agent.properties || {},
      };
    }),
    workflows: (data.workflows || []).map((workflow) => {
      const source = workflow.source || workflow.definition_ref?.source || 'builtin';
      return {
        id: workflow.id,
        name: workflow.name,
        type: source === 'builtin' ? normalizeBuiltinWorkflowTypeId(workflow.type) : workflow.type,
        source,
        version: workflow.version || workflow.definition_ref?.version || null,
        definition_ref: workflow.definition_ref || workflow.registry_ref || null,
        registry_ref: workflow.definition_ref || workflow.registry_ref || null,
        agent_id: workflow.agent_id,
        enabled: workflow.enabled !== false,
        properties: workflow.properties || workflow.parameters || workflow.details || {},
      };
    }),
  };
}

function serializeConfigForApi(configData) {
  return {
    config_id: configData.config_id,
    name: configData?.metadata?.name || configData?.name || 'AeroAgentSim Config',
    coordinate_mode: configData.coordinate_mode || 'simulation_plane',
    traffic: configData.traffic || {},
    agents: (configData.agents || []).map((agent) => ({
      id: agent.id,
      name: agent.name,
      type: agent.type,
      source: agent.source || (agent.definition_ref?.source || 'builtin'),
      version: agent.version || agent.definition_ref?.version || undefined,
      definition_ref: agent.definition_ref || agent.registry_ref || undefined,
      initial_position: agent.initial_position || [0, 0, 0],
      initial_battery:
        agent.initial_battery ??
        agent.properties?.battery_level ??
        agent.battery ??
        100,
      components: agent.components || [],
      properties: agent.properties || {},
    })),
    workflows: (configData.workflows || []).map((workflow) => ({
      id: workflow.id,
      name: workflow.name,
      type: workflow.type,
      source: workflow.source || (workflow.definition_ref?.source || 'builtin'),
      version: workflow.version || workflow.definition_ref?.version || undefined,
      definition_ref: workflow.definition_ref || workflow.registry_ref || undefined,
      agent_id: workflow.agent_id,
      enabled: workflow.enabled !== false,
      properties: workflow.properties || {},
    })),
  };
}

function normalizeValidation(data) {
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
    valid: typeof data.valid === 'boolean' ? data.valid : Boolean(data.is_valid),
    issues: [
      ...(Array.isArray(data.issues) ? data.issues : []),
      ...issues,
    ],
    checked_at: new Date().toISOString(),
    source: 'api',
    blockers: (data.errors || []).slice(),
    missing_dependencies: data.missing_dependencies || [],
    compatibility_gaps: data.compatibility_gaps || [],
    unresolved_proxies: data.unresolved_proxies || [],
    version_conflicts: data.version_conflicts || [],
    graph_warnings: data.graph_warnings || [],
  };
}

function normalizePreflight(data) {
  if (!data) {
    return {
      is_ready: false,
      errors: [],
      warnings: [],
      checks: [],
    };
  }
  return {
    is_ready: Boolean(data.is_ready),
    errors: data.errors || [],
    warnings: data.warnings || [],
    checks: data.checks || [],
  };
}

function normalizeGraph(data) {
  if (!data) {
    return {
      nodes: [],
      edges: [],
      trigger_conditions: [],
      task_bindings: [],
      critical_path: [],
      warnings: [],
    };
  }
  return {
    nodes: data.nodes || [],
    edges: data.edges || [],
    trigger_conditions: data.trigger_conditions || [],
    task_bindings: data.task_bindings || [],
    critical_path: data.critical_path || [],
    warnings: data.warnings || [],
  };
}

function normalizeRun(run) {
  if (!run) {
    return null;
  }
  return {
    run_id: run.run_id,
    config_id: run.config_id,
    config_name: run.config_name,
    status: String(run.status || 'UNKNOWN').toUpperCase(),
    started_at: run.started_at || run.created_at || null,
    updated_at: run.ended_at || run.started_at || run.created_at || null,
    simulation_time: run.latest_sim_time ?? run.simulation_time ?? 0,
    speed: run.latest_speed ?? run.speed ?? 1,
    coordinate_mode: run.coordinate_mode || 'simulation_plane',
    registry_references: run.registry_references || [],
  };
}

function normalizeHealth(data) {
  if (!data) {
    return {
      backend_available: false,
      db_writable: false,
      simulation_status: 'UNKNOWN',
      active_run_id: null,
      recent_startup_error: null,
      timestamp: null,
    };
  }
  return {
    backend_available: data.backend_available !== false,
    db_writable: Boolean(data.db_writable),
    simulation_status: String(data.simulation_status || 'UNKNOWN').toUpperCase(),
    active_run_id: data.active_run_id || null,
    recent_startup_error: data.recent_startup_error || null,
    timestamp: data.timestamp || null,
  };
}

function normalizeLog(log, index = 0) {
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
    id: raw.id || `${raw.recorded_at || raw.timestamp || Date.now()}_${index}`,
    run_id: raw.run_id || null,
    level: raw.level || 'info',
    source: raw.source || sourceId || 'system',
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
    sim_time: raw.time ?? raw.sim_time ?? 0,
    message: raw.message || JSON.stringify(raw),
    timestamp: raw.timestamp || raw.recorded_at || new Date().toISOString(),
    raw,
  };
}

function normalizeSpatialPayload(data) {
  if (!data) {
    return null;
  }
  return {
    run_id: data.run_id,
    coordinate_mode: data.coordinate_mode || 'simulation_plane',
    timestamp: data.timestamp || 0,
    agents: (data.agents || []).map((agent) => ({
      id: agent.agent_id || agent.id,
      type: agent.agent_type || agent.type,
      status: String(agent.status || 'idle').toLowerCase(),
      workflow_id: agent.current_workflow || agent.workflow_id || null,
      task_id: agent.current_task_id || agent.task_id || null,
      task_name: agent.current_task || agent.task_name || null,
      position:
        data.coordinate_mode === 'geo_osm'
          ? {
              lng: agent.display_position?.[1] ?? agent.position?.[0] ?? 0,
              lat: agent.display_position?.[0] ?? agent.position?.[1] ?? 0,
              z: agent.altitude ?? agent.position?.[2] ?? 0,
            }
          : {
              x: agent.display_position?.[1] ?? agent.position?.[0] ?? 0,
              y: agent.display_position?.[0] ?? agent.position?.[1] ?? 0,
              z: agent.altitude ?? agent.position?.[2] ?? 0,
            },
      recent_log: agent.recent_log || null,
    })),
  };
}

function normalizeTrajectoryPayload(data, coordinateMode = 'simulation_plane') {
  if (!data) {
    return { trajectories: [], coordinate_mode: coordinateMode };
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
              lng: point.display_position?.[1] ?? point.position?.[0] ?? point.lng ?? 0,
              lat: point.display_position?.[0] ?? point.position?.[1] ?? point.lat ?? 0,
              z: point.position?.[2] ?? point.z ?? 0,
            }
          : {
              x: point.display_position?.[1] ?? point.position?.[0] ?? point.x ?? 0,
              y: point.display_position?.[0] ?? point.position?.[1] ?? point.y ?? 0,
              z: point.position?.[2] ?? point.z ?? 0,
            }
      ),
    })),
  };
}

function buildGraphFromConfig(configData) {
  const nodes = [];
  const edges = [];
  (configData.agents || []).forEach((agent) => {
    nodes.push({
      id: `agent:${agent.id}`,
      label: agent.name || agent.id,
      kind: 'agent',
      group: agent.type,
      metadata: {
        agent_id: agent.id,
        agent_type: agent.type,
        source: agent.registry_ref?.source || 'builtin',
        version: agent.registry_ref?.version || 'builtin',
      },
    });
  });
  (configData.workflows || []).forEach((workflow) => {
    nodes.push({
      id: `workflow:${workflow.id}`,
      label: workflow.name || workflow.id,
      kind: 'workflow',
      group: workflow.type,
      metadata: {
        workflow_id: workflow.id,
        agent_id: workflow.agent_id,
        source: workflow.registry_ref?.source || 'builtin',
        version: workflow.registry_ref?.version || 'builtin',
      },
    });
    edges.push({
      id: `edge:${workflow.id}:${workflow.agent_id}:binds`,
      source: `workflow:${workflow.id}`,
      target: `agent:${workflow.agent_id}`,
      kind: 'workflow uses agent',
      label: 'workflow uses agent',
      metadata: {},
    });
  });
  return {
    nodes,
    edges,
    trigger_conditions: [],
    task_bindings: [],
    critical_path: [],
    warnings: [],
  };
}

function buildValidationFromConfig(configData) {
  const issues = [];
  const agentIds = new Set();
  const workflowIds = new Set();
  const agentConfigs = new Map();

  (configData.agents || []).forEach((agent) => {
    if (!agent.id) {
      issues.push({ level: 'error', category: 'errors', message: 'Agent id is required.' });
    } else if (agentIds.has(agent.id)) {
      issues.push({ level: 'error', category: 'errors', message: `Duplicate agent id: ${agent.id}` });
    }
    agentIds.add(agent.id);
    const definition = findBuiltinAgentDefinition(agent.type);
    const hasExplicitComponents = Object.prototype.hasOwnProperty.call(agent || {}, 'components');
    agentConfigs.set(agent.id, {
      ...agent,
      type: normalizeBuiltinAgentTypeId(agent.type),
      definition,
      components: hasExplicitComponents
        ? (agent.components || [])
        : defaultComponentsForDefinition(definition),
    });
  });

  (configData.workflows || []).forEach((workflow) => {
    const workflowLabel = workflow.id || workflow.name || '(unnamed workflow)';
    if (!workflow.id) {
      issues.push({ level: 'error', category: 'errors', message: 'Workflow id is required.' });
    } else if (workflowIds.has(workflow.id)) {
      issues.push({
        level: 'error',
        category: 'errors',
        message: `Duplicate workflow id: ${workflow.id}`,
      });
    }
    workflowIds.add(workflow.id);
    if (!workflow.agent_id || !agentIds.has(workflow.agent_id)) {
      issues.push({
        level: 'error',
        category: 'missing_dependencies',
        message: `Workflow ${workflow.id} references missing agent ${workflow.agent_id || '(empty)'}.`,
      });
    }

    if (normalizeWorkflowTypeToken(workflow.type) === 'logistics') {
      const properties = workflow.properties || {};
      const owner = agentConfigs.get(workflow.agent_id);
      const ownerComponents = new Set(owner?.components || []);

      if (!isCoordinate3d(properties.pickup_location)) {
        issues.push({
          level: 'error',
          category: 'schema',
          message: `Workflow ${workflowLabel} requires pickup_location as a 3D numeric coordinate.`,
        });
      }
      if (!isCoordinate3d(properties.delivery_location)) {
        issues.push({
          level: 'error',
          category: 'schema',
          message: `Workflow ${workflowLabel} requires delivery_location as a 3D numeric coordinate.`,
        });
      }
      if (!Array.isArray(properties.payloads) || properties.payloads.length === 0) {
        issues.push({
          level: 'error',
          category: 'schema',
          message: `Workflow ${workflowLabel} requires at least one payload.`,
        });
      } else if (
        properties.payloads.some((payload) => !payload || typeof payload !== 'object' || !payload.id)
      ) {
        issues.push({
          level: 'error',
          category: 'schema',
          message: `Workflow ${workflowLabel} payloads must be objects containing an id.`,
        });
      }
      if (!properties.source_agent_id || !agentIds.has(properties.source_agent_id)) {
        issues.push({
          level: 'error',
          category: 'missing_dependencies',
          message: `Workflow ${workflowLabel} references missing source agent ${properties.source_agent_id || '(empty)'}.`,
        });
      }
      if (!properties.target_agent_id || !agentIds.has(properties.target_agent_id)) {
        issues.push({
          level: 'error',
          category: 'missing_dependencies',
          message: `Workflow ${workflowLabel} references missing target agent ${properties.target_agent_id || '(empty)'}.`,
        });
      }
      if (owner && !ownerComponents.has('MoveToComponent')) {
        issues.push({
          level: 'error',
          category: 'compatibility',
          message: `Workflow ${workflowLabel} requires owner ${workflow.agent_id} to enable MoveToComponent.`,
        });
      }
      if (owner && !ownerComponents.has('LogisticsComponent')) {
        issues.push({
          level: 'error',
          category: 'compatibility',
          message: `Workflow ${workflowLabel} requires owner ${workflow.agent_id} to enable LogisticsComponent.`,
        });
      }
    }
  });

  return {
    valid: issues.every((item) => item.level !== 'error'),
    is_valid: issues.every((item) => item.level !== 'error'),
    errors: issues.filter((item) => item.level === 'error').map((item) => item.message),
    warnings: issues.filter((item) => item.level === 'warning').map((item) => item.message),
    issues,
    checked_at: new Date().toISOString(),
    source: 'local',
    blockers: issues.filter((item) => item.level === 'error').map((item) => item.message),
  };
}

function extractErrorPayload(data) {
  const candidate =
    data?.detail && typeof data.detail === 'object' && !Array.isArray(data.detail)
      ? data.detail
      : data;
  const detailValue =
    typeof data?.detail === 'string'
      ? data.detail
      : candidate?.detail || candidate?.message || '';
  return {
    detail: detailValue,
    errors: candidate?.errors || [],
    warnings: candidate?.warnings || [],
    checks: candidate?.checks || [],
    runId: candidate?.run_id || candidate?.runId || null,
    recentLogs: candidate?.recent_logs || candidate?.recentLogs || [],
    raw: candidate,
  };
}

function isConnectivityFailure(error, response) {
  if (response) {
    return false;
  }
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  if (CONNECTIVITY_ERROR_CODES.has(code)) {
    return true;
  }
  if (error?.request) {
    return true;
  }
  return /network|timeout|failed to fetch|load failed/i.test(String(error?.message || ''));
}

function normalizeApiError(error) {
  const response = error?.response;
  const payload = extractErrorPayload(response?.data);
  const message = payload.detail || error?.message || 'Request failed';
  const normalized = new Error(message);
  normalized.name = 'WorkbenchApiError';
  normalized.status = response?.status || null;
  normalized.detail = payload.raw;
  normalized.errors = payload.errors;
  normalized.warnings = payload.warnings;
  normalized.checks = payload.checks;
  normalized.runId = payload.runId;
  normalized.recentLogs = (Array.isArray(payload.recentLogs) ? payload.recentLogs : []).map((item, index) =>
    normalizeLog(item, index)
  );
  normalized.isConnectivityError = isConnectivityFailure(error, response);
  normalized.response = response;
  normalized.originalError = error;
  return normalized;
}

async function requestWithFallback(request, fallbackFactory) {
  try {
    return await request();
  } catch (error) {
    const normalizedError = normalizeApiError(error);
    const shouldFallback =
      ENABLE_LOCAL_FALLBACK &&
      fallbackFactory &&
      normalizedError.isConnectivityError;
    if (!shouldFallback) {
      throw normalizedError;
    }
    return fallbackFactory(normalizedError);
  }
}

async function requestStrict(request) {
  try {
    return await request();
  } catch (error) {
    throw normalizeApiError(error);
  }
}

const catalogApi = {
  async getAgents(scope = 'all') {
    const data = await requestWithFallback(
      () => apiClient.get('/catalog/agents', { params: { scope } }).then((response) => response.data),
      () => defaultCatalog.agents
    );
    return (Array.isArray(data) ? data : []).map((item) => normalizeCatalogItem(item, 'agents'));
  },
  async getComponents() {
    const data = await requestWithFallback(
      () => apiClient.get('/catalog/components').then((response) => response.data),
      () => defaultCatalog.components
    );
    return (Array.isArray(data) ? data : []).map((item) => normalizeCatalogItem(item, 'components'));
  },
  async getTasks(scope = 'all') {
    const data = await requestWithFallback(
      () => apiClient.get('/catalog/tasks', { params: { scope } }).then((response) => response.data),
      () => defaultCatalog.tasks
    );
    return (Array.isArray(data) ? data : []).map((item) => normalizeCatalogItem(item, 'tasks'));
  },
  async getWorkflows(scope = 'all') {
    const data = await requestWithFallback(
      () => apiClient.get('/catalog/workflows', { params: { scope } }).then((response) => response.data),
      () => defaultCatalog.workflows
    );
    return filterSupportedWorkflowDefinitions(
      (Array.isArray(data) ? data : []).map((item) => normalizeCatalogItem(item, 'workflows'))
    );
  },
  async getCompatibility(scope = 'all') {
    const data = await requestWithFallback(
      () => apiClient.get('/catalog/compatibility', { params: { scope } }).then((response) => response.data),
      () => defaultCatalog.compatibility
    );
    return normalizeCompatibilityMatrix(data);
  },
};

const registryApi = {
  async list(kind) {
    const data = await requestWithFallback(
      () => apiClient.get(`/registry/${kind}`).then((response) => response.data),
      () => ensureLocalRegistry()[kind] || []
    );
    return (Array.isArray(data) ? data : []).map((item) => flattenRegistryDefinition(kind, item));
  },
  async get(kind, definitionId, version) {
    const data = await requestWithFallback(
      () => apiClient.get(`/registry/${kind}/${definitionId}`, { params: { version } }).then((response) => response.data),
      () => {
        const registry = ensureLocalRegistry();
        return (registry[kind] || []).find(
          (item) => item.id === definitionId && (!version || item.version === version)
        );
      }
    );
    return data ? flattenRegistryDefinition(kind, data) : null;
  },
  async save(kind, payload) {
    const normalized = toRegistryPayload(kind, payload);
    const data = await requestStrict(() =>
      apiClient.post(`/registry/${kind}`, normalized).then((response) => response.data)
    );
    return flattenRegistryDefinition(kind, data);
  },
  async delete(kind, definitionId, version) {
    return requestStrict(() =>
      apiClient.delete(`/registry/${kind}/${definitionId}`, { params: { version } }).then((response) => response.data)
    );
  },
  async validate(kind, definitionId, payload, version) {
    const normalized = payload ? toRegistryPayload(kind, payload) : undefined;
    const data = await requestStrict(() =>
      apiClient
        .post(`/registry/${kind}/${definitionId}/validate`, normalized, { params: { version } })
        .then((response) => response.data)
    );
    return data;
  },
};

const configApi = {
  async getConfig(configId = MOCK_CONFIG_ID) {
    const data = await requestWithFallback(
      () => apiClient.get(`/configs/${configId}`).then((response) => response.data),
      () => ensureLocalConfig(configId)
    );
    return normalizeConfig(data);
  },
  async saveConfig(configId, configData) {
    const payload = serializeConfigForApi(configData);
    const data = await requestStrict(() =>
      apiClient.put(`/configs/${configId}`, payload).then((response) => response.data)
    );
    return normalizeConfig(data);
  },
  async validate(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestStrict(() =>
      apiClient
        .post(`/configs/${configId}/validate`, payload)
        .then((response) => response.data)
    );
    return normalizeValidation(data);
  },
  async preflight(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestStrict(() =>
      apiClient
        .post(`/configs/${configId}/preflight`, payload)
        .then((response) => response.data)
    );
    return normalizePreflight(data);
  },
  async getGraph(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestWithFallback(
      () =>
        payload
          ? apiClient.post(`/configs/${configId}/graph`, payload).then((response) => response.data)
          : apiClient.get(`/configs/${configId}/graph`).then((response) => response.data),
      () => buildGraphFromConfig(configData || ensureLocalConfig(configId))
    );
    return normalizeGraph(data);
  },
};

const runApi = {
  async listRuns() {
    const data = await requestStrict(() => apiClient.get('/runs').then((response) => response.data));
    return (Array.isArray(data) ? data : []).map((run) => normalizeRun(run)).filter(Boolean);
  },
  async startRun(configId = MOCK_CONFIG_ID) {
    const data = await requestStrict(() =>
      apiClient.post('/runs', { config_id: configId }).then((response) => response.data)
    );
    return normalizeRun(data);
  },
  async getStatus(runId) {
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/status`).then((response) => response.data)
    );
    return normalizeRun(data);
  },
  async pauseRun(runId) {
    return requestStrict(() => apiClient.post(`/runs/${runId}/pause`).then((response) => response.data));
  },
  async resumeRun(runId) {
    return requestStrict(() => apiClient.post(`/runs/${runId}/resume`).then((response) => response.data));
  },
  async resetRun(runId) {
    return requestStrict(() => apiClient.post(`/runs/${runId}/reset`).then((response) => response.data));
  },
  async deleteRun(runId) {
    return requestStrict(() => apiClient.delete(`/runs/${runId}`).then((response) => response.data));
  },
  async getLogs(runId) {
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/logs`).then((response) => response.data)
    );
    return (Array.isArray(data) ? data : []).map((item, index) => normalizeLog(item, index));
  },
  async getSpatial(runId) {
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/spatial`).then((response) => response.data)
    );
    return normalizeSpatialPayload(data);
  },
  async getTrajectories(runId) {
    const run = await this.getStatus(runId);
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/trajectories`).then((response) => response.data)
    );
    return normalizeTrajectoryPayload(data, run?.coordinate_mode || 'simulation_plane');
  },
};

const systemApi = {
  async getHealth() {
    const data = await requestStrict(() => apiClient.get('/health').then((response) => response.data));
    return normalizeHealth(data);
  },
  async resetRuntime() {
    return requestStrict(() => apiClient.post('/runtime/reset').then((response) => response.data));
  },
};

function createWorkbenchSocket(handlers = {}) {
  if (ENABLE_LOCAL_FALLBACK && typeof WebSocket === 'undefined') {
    return { close() {} };
  }
  const socket = new WebSocket(WS_BASE_URL);
  socket.addEventListener('open', () => handlers.onOpen?.());
  socket.addEventListener('close', () => handlers.onClose?.());
  socket.addEventListener('error', () => handlers.onClose?.());
  socket.addEventListener('message', (event) => {
    try {
      const payload = JSON.parse(event.data);
      switch (payload.type) {
        case 'sim_status':
          handlers.onStatus?.({
            ...payload,
            simulation_time: payload.simulation_time ?? payload.time ?? 0,
            updated_at: payload.updated_at || new Date().toISOString(),
          });
          break;
        case 'log_event':
          handlers.onLog?.(normalizeLog(payload));
          break;
        case 'spatial_snapshot':
          handlers.onSpatial?.(normalizeSpatialPayload(payload));
          break;
        case 'workflow_state_diff':
          handlers.onWorkflowState?.(payload);
          break;
        default:
          handlers.onMessage?.(payload);
      }
    } catch (_error) {
      handlers.onMessage?.(event.data);
    }
  });
  return socket;
}

export {
  catalogApi,
  configApi,
  createWorkbenchSocket,
  defaultComponentsForDefinition,
  normalizeAgentTypeToken,
  normalizeWorkflowTypeToken,
  registryApi,
  runApi,
  systemApi,
};
