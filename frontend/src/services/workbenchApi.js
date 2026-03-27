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
const LS_RUNS_KEY = 'aeroagentsim.runs';
const LS_REGISTRY_KEY = 'aeroagentsim.registry';

const defaultConfig = {
  config_id: MOCK_CONFIG_ID,
  name: 'AeroAgentSim Config',
  coordinate_mode: 'simulation_plane',
  metadata: {
    name: 'AeroAgentSim Config',
    updated_at: new Date().toISOString(),
  },
  traffic: {},
  agents: [
    {
      id: 'agent_drone_1',
      name: 'Survey Drone A1',
      type: 'drone',
      registry_ref: null,
      initial_position: [120, 240, 40],
      initial_battery: 87,
      components: ['MoveToComponent', 'ChargingComponent'],
      properties: {
        battery_level: 87,
      },
    },
  ],
  workflows: [
    {
      id: 'workflow_inspection_1',
      name: 'Inspection Path Alpha',
      type: 'inspection',
      registry_ref: null,
      agent_id: 'agent_drone_1',
      enabled: true,
      properties: {
        inspection_points: [
          [120, 240, 40],
          [180, 260, 60],
          [240, 220, 50],
        ],
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
    {
      id: 'contract_workflow',
      name: 'ContractWorkflow',
      description: 'Contract workflow managing multi-task execution flows',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        contract_id: { value_type: 'str', required: true },
        tasks: { value_type: 'list', required: true },
      },
      states: ['idle', 'executing', 'completed', 'failed'],
      start_state: 'idle',
    },
    {
      id: 'order_execution_workflow',
      name: 'OrderExecutionWorkflow',
      description: 'Order execution workflow managing logistics order generation, assignment and delivery monitoring',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        payload_properties: { value_type: 'dict', required: true },
        delivery_location: { value_type: 'list', required: true },
        assigned_drone: { value_type: 'str', required: false },
        target_agent_id: { value_type: 'str', required: false },
        logistics_workflow_id: { value_type: 'str', required: false },
      },
      states: ['pending', 'assigned', 'in_transit', 'delivered', 'cancelled'],
      start_state: 'pending',
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
      contract_workflow: ['drone_agent', 'delivery_agent', 'delivery_drone_agent'],
      order_execution_workflow: ['delivery_agent', 'delivery_drone_agent'],
    },
    workflow_tasks: {
      inspection_workflow: ['move_to_task'],
      charging_workflow: ['charging_task', 'move_to_task', 'request_charging_station_task'],
      logistics_workflow: ['handover_task', 'move_to_task', 'pickup_task'],
      image_processing_workflow: ['file_collect_task', 'file_compute_task', 'file_transfer_task', 'move_to_task'],
      contract_workflow: [],
      order_execution_workflow: [],
    },
  },
};

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

function getLocalRuns() {
  return readLocalJson(LS_RUNS_KEY, []);
}

function saveLocalRuns(runs) {
  writeLocalJson(LS_RUNS_KEY, runs);
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
    version: meta.version || 'v1',
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
    return data;
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
  return result;
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
    agents: (data.agents || []).map((agent) => ({
      id: agent.id,
      name: agent.name,
      type: agent.type,
      source: agent.source || (agent.definition_ref?.source || 'builtin'),
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
    })),
    workflows: (data.workflows || []).map((workflow) => ({
      id: workflow.id,
      name: workflow.name,
      type: workflow.type,
      source: workflow.source || (workflow.definition_ref?.source || 'builtin'),
      version: workflow.version || workflow.definition_ref?.version || null,
      definition_ref: workflow.definition_ref || workflow.registry_ref || null,
      registry_ref: workflow.definition_ref || workflow.registry_ref || null,
      agent_id: workflow.agent_id,
      enabled: workflow.enabled !== false,
      properties: workflow.properties || workflow.parameters || workflow.details || {},
    })),
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
  return {
    id: log.id || `${log.recorded_at || log.timestamp || Date.now()}_${index}`,
    run_id: log.run_id || null,
    level: log.level || 'info',
    source: log.source || 'system',
    sim_time: log.time ?? log.sim_time ?? 0,
    message: log.message || JSON.stringify(log),
    timestamp: log.timestamp || log.recorded_at || new Date().toISOString(),
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
      task_id: agent.current_task || agent.task_id || null,
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

  (configData.agents || []).forEach((agent) => {
    if (!agent.id) {
      issues.push({ level: 'error', category: 'errors', message: 'Agent id is required.' });
    } else if (agentIds.has(agent.id)) {
      issues.push({ level: 'error', category: 'errors', message: `Duplicate agent id: ${agent.id}` });
    }
    agentIds.add(agent.id);
  });

  (configData.workflows || []).forEach((workflow) => {
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
  });

  return {
    valid: issues.every((item) => item.level !== 'error'),
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
  normalized.isConnectivityError = !response;
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
      !error?.response;
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
    return (Array.isArray(data) ? data : []).map((item) => normalizeCatalogItem(item, 'workflows'));
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
    const data = await requestWithFallback(
      () => apiClient.post(`/registry/${kind}`, normalized).then((response) => response.data),
      () => {
        const registry = ensureLocalRegistry();
        const next = [...(registry[kind] || [])];
        const existingIndex = next.findIndex(
          (item) =>
            item.id === normalized.meta.id &&
            item.version === normalized.meta.version
        );
        const flattened = flattenRegistryDefinition(kind, normalized);
        if (existingIndex >= 0) {
          next[existingIndex] = flattened;
        } else {
          next.push(flattened);
        }
        saveLocalRegistry({ ...registry, [kind]: next });
        return flattened;
      }
    );
    return flattenRegistryDefinition(kind, data);
  },
  async delete(kind, definitionId, version) {
    return requestWithFallback(
      () => apiClient.delete(`/registry/${kind}/${definitionId}`, { params: { version } }).then((response) => response.data),
      () => {
        const registry = ensureLocalRegistry();
        const next = (registry[kind] || []).filter(
          (item) => item.id !== definitionId || (version && item.version !== version)
        );
        saveLocalRegistry({ ...registry, [kind]: next });
        return { status: 'deleted' };
      }
    );
  },
  async validate(kind, definitionId, payload, version) {
    const normalized = payload ? toRegistryPayload(kind, payload) : undefined;
    const data = await requestWithFallback(
      () =>
        apiClient
          .post(`/registry/${kind}/${definitionId}/validate`, normalized, { params: { version } })
          .then((response) => response.data),
      () => ({
        is_valid: true,
        errors: [],
        warnings: [],
      })
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
    const data = await requestWithFallback(
      () => apiClient.put(`/configs/${configId}`, payload).then((response) => response.data),
      () => saveLocalConfig(configId, configData)
    );
    return normalizeConfig(data);
  },
  async validate(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestWithFallback(
      () =>
        apiClient
          .post(`/configs/${configId}/validate`, payload)
          .then((response) => response.data),
      () => buildValidationFromConfig(configData || ensureLocalConfig(configId))
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
  registryApi,
  runApi,
  systemApi,
};
