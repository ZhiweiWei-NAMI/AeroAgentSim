/**
 * Independently authored, dependency-free console contracts.
 * This is a desired configuration plan, never an AERO_BENCH resolved scenario.
 * All defaults below are synthetic demonstration choices, not server settings.
 */

import { validateNetworkStudy, deriveStudyQuantities } from './network-study.js';

export const CONFIG_SCHEMA = 'aero-console.config/v1';
export const INITIAL_SOURCE_CURSOR = Object.freeze({
  afterTransition: -1,
  afterSceneTick: 0,
  afterEventSequence: -1,
});

export const DEFAULT_CONFIG = {
  schema_version: CONFIG_SCHEMA,
  metadata: {
    id: 'authored-campus-demo',
    name: 'Campus delivery lab',
    version: '1.0.0',
    description: 'Synthetic ENU scenario for configuration and local demonstration. No server data.',
  },
  scenario: { duration_s: 60, step_ms: 100, seed: 42, frame: 'ENU', vertical_datum: 'local', scene_id: 'synthetic-campus' },
  entities: [
    { id: 'uav-alpha', type: 'uav', count: 1, provider: 'local-demo', position_enu_m: [-80, -45, 35], radio_profile_id: 'demo-wifi', compute_profile_id: 'onboard' },
    { id: 'uav-beta', type: 'uav', count: 1, provider: 'local-demo', position_enu_m: [65, 50, 45], radio_profile_id: 'demo-wifi', compute_profile_id: 'onboard' },
    { id: 'vehicle', type: 'vehicle', count: 3, provider: 'local-demo', position_enu_m: [-90, 0, 0], radio_profile_id: 'demo-wifi', compute_profile_id: null },
    { id: 'pedestrian', type: 'pedestrian', count: 4, provider: 'local-demo', position_enu_m: [-35, -30, 0], radio_profile_id: null, compute_profile_id: null },
    { id: 'station-west', type: 'base_station', count: 1, provider: 'static', position_enu_m: [-100, 30, 12], radio_profile_id: 'demo-wifi', compute_profile_id: null },
    { id: 'edge-west', type: 'edge', count: 1, provider: 'static', position_enu_m: [-105, 35, 0], radio_profile_id: null, compute_profile_id: 'edge-demo' },
    { id: 'cloud-demo', type: 'cloud', count: 1, provider: 'static', position_enu_m: [120, 95, 0], radio_profile_id: null, compute_profile_id: 'cloud-demo' },
  ],
  mobility: {
    sumo: { enabled: false, network_asset: 'authored-campus.net.xml', route_asset: 'authored-campus.rou.xml', step_ms: 100 },
    uav: { model: 'waypoint', cruise_speed_mps: 8, altitude_m: 35 },
  },
  network: {
    enabled: true,
    provider: 'local-demo',
    // New fixtures share R1 pilot radio inputs; saved/imported profiles are retained.
    // This is an authored research choice, not regulatory or hardware approval.
    radio_profiles: [{ id: 'demo-wifi', wifi_standard: '802.11n', frequency_ghz: 2.412, channel_width_mhz: 20, tx_power_dbm: 16, rx_sensitivity_dbm: -95 }],
    link: { configured_rate_mbps: 24, propagation_delay_ms: 2 },
  },
  compute: {
    provider: 'local-demo',
    profiles: [
      { id: 'onboard', cpu_cores: 6, gpu_units: 0.5, memory_mb: 3072, queue_limit: 8, deadline_ms: 800 },
      { id: 'edge-demo', cpu_cores: 12, gpu_units: 2, memory_mb: 12288, queue_limit: 32, deadline_ms: 500 },
      { id: 'cloud-demo', cpu_cores: 24, gpu_units: 4, memory_mb: 49152, queue_limit: 96, deadline_ms: 1500 },
    ],
  },
  semantics: {
    adapter: 'atlas',
    execution: 'not_connected',
    bindings: [{ id: 'moving-alpha', target: 'hu.predicate.actor_moving', entity_id: 'uav-alpha', parameters: {} }],
  },
  provenance: {
    kind: 'authored_synthetic',
    source: 'independent-console-demo',
    note: 'Synthetic authored values; no private source, maps, raw data, or actual server configuration included.',
  },
};

const ID = /^[a-z][a-z0-9_.-]*$/;
const HASH = /^[a-f0-9]{64}$/;
const ENTITY_TYPES = ['uav', 'vehicle', 'pedestrian', 'base_station', 'edge', 'cloud'];
const DYNAMIC_TYPES = ['uav', 'vehicle', 'pedestrian'];
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const vector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
const issue = (path, message) => ({ path, message });

// Decimal text avoids floating-point tolerance accepting sub-millisecond input.
function exactMilliseconds(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const [coefficient, exponent = '0'] = String(seconds).toLowerCase().split('e');
  const [whole, fraction = ''] = coefficient.split('.');
  const digits = BigInt(whole + fraction);
  const shift = 3 + Number(exponent) - fraction.length;
  let scaled;
  if (shift >= 0) scaled = digits * (10n ** BigInt(shift));
  else {
    const divisor = 10n ** BigInt(-shift);
    if (digits % divisor !== 0n) return null;
    scaled = digits / divisor;
  }
  return scaled > 0n && scaled <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(scaled) : null;
}

/** Copy JSON-shaped input without sharing mutable nested values. */
export function clone(value) {
  if (typeof structuredClone === 'function') return structuredClone(value);
  if (Array.isArray(value)) return value.map(clone);
  if (isObject(value)) return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, clone(entry)]));
  return value;
}

function instanceIds(entity) {
  return entity.count === 1 ? [entity.id] : Array.from({ length: entity.count }, (_, i) => `${entity.id}-${i + 1}`);
}

/** Validate authored inputs. Warnings do not grant unavailable backend capabilities. */
export function validateConfig(config) {
  const errors = [];
  const warnings = [];
  const error = (path, message) => errors.push(issue(path, message));
  const warn = (path, message) => warnings.push(issue(path, message));
  // Inspect even unsupported fields: exported JSON must not turn Infinity into null.
  let inspected = 0;
  let budgetExceeded = false;
  const inspectJSON = (value, path, ancestors = new Set(), depth = 0) => {
    if (budgetExceeded) return;
    if (++inspected > 100000) { error('$', 'Configuration exceeds the 100,000-value local authoring limit.'); budgetExceeded = true; return; }
    if (depth > 64) { error(path, 'Configuration nesting exceeds the local limit of 64 levels.'); return; }
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
    if (typeof value === 'number') { if (!Number.isFinite(value)) error(path, 'Every numeric JSON value must be finite, including unsupported fields.'); return; }
    if (!isObject(value) && !Array.isArray(value)) { error(path, 'Configuration must contain JSON values only.'); return; }
    if (ancestors.has(value)) { error(path, 'Configuration cannot contain circular references.'); return; }
    ancestors.add(value);
    for (const [key, child] of Object.entries(value)) {
      if (budgetExceeded) break;
      inspectJSON(child, Array.isArray(value) ? `${path}[${key}]` : path === '$' ? key : `${path}.${key}`, ancestors, depth + 1);
    }
    ancestors.delete(value);
  };
  inspectJSON(config, '$');
  if (budgetExceeded) return { valid: false, errors, warnings };
  const object = (value, path) => {
    if (!isObject(value)) { error(path, 'Must be an object.'); return {}; }
    return value;
  };
  const array = (value, path) => {
    if (!Array.isArray(value)) { error(path, 'Must be an array.'); return []; }
    return value;
  };
  const string = (value, path) => {
    if (typeof value !== 'string' || !value.trim()) error(path, 'Must be a nonempty string.');
  };
  const id = (value, path) => {
    if (typeof value !== 'string' || !ID.test(value)) error(path, 'Use a lowercase identifier starting with a letter; allowed characters: a-z, 0-9, _, ., -.');
  };
  const number = (value, path, { min = -Infinity, integer = false, positive = false } = {}) => {
    if (!Number.isFinite(value) || value < min || (positive && value <= 0) || (integer && !Number.isSafeInteger(value))) {
      error(path, `Must be a finite ${integer ? 'safe integer' : 'number'}${positive ? ' greater than zero' : Number.isFinite(min) ? ` at least ${min}` : ''}.`);
      return false;
    }
    return true;
  };
  const choice = (value, path, options) => {
    if (!options.includes(value)) error(path, `Supported values: ${options.join(', ')}.`);
  };
  const bool = (value, path) => { if (typeof value !== 'boolean') error(path, 'Must be true or false.'); };
  const keys = (value, path, allowed) => {
    for (const key of Object.keys(value)) {
      if (!allowed.includes(key)) warn(path ? `${path}.${key}` : key, 'Unsupported field is preserved in authored input but is not mapped to a backend.');
    }
  };
  const c = object(config, '$');
  keys(c, '', ['schema_version', 'metadata', 'scenario', 'entities', 'mobility', 'network', 'compute', 'semantics', 'provenance']);
  choice(c.schema_version, 'schema_version', [CONFIG_SCHEMA]);
  const m = object(c.metadata, 'metadata');
  keys(m, 'metadata', ['id', 'name', 'version', 'description']);
  id(m.id, 'metadata.id');
  string(m.name, 'metadata.name');
  string(m.version, 'metadata.version');
  if (typeof m.description !== 'string') error('metadata.description', 'Must be a string.');

  const s = object(c.scenario, 'scenario');
  keys(s, 'scenario', ['duration_s', 'step_ms', 'seed', 'frame', 'vertical_datum', 'scene_id']);
  const durationValid = number(s.duration_s, 'scenario.duration_s', { positive: true });
  const stepValid = number(s.step_ms, 'scenario.step_ms', { positive: true, integer: true });
  number(s.seed, 'scenario.seed', { min: 0, integer: true });
  choice(s.frame, 'scenario.frame', ['ENU']);
  choice(s.vertical_datum, 'scenario.vertical_datum', ['local']);
  id(s.scene_id, 'scenario.scene_id');
  if (durationValid && stepValid) {
    const durationMs = exactMilliseconds(s.duration_s);
    if (durationMs === null) {
      error('scenario.duration_s', 'Duration must resolve to a positive exact safe whole number of milliseconds.');
    } else if (durationMs < s.step_ms || durationMs % s.step_ms !== 0) {
      error('scenario.duration_s', 'Duration must contain an integer number of scenario steps.');
    }
  }

  const network = object(c.network, 'network');
  keys(network, 'network', ['enabled', 'provider', 'radio_profiles', 'link', 'study']);
  bool(network.enabled, 'network.enabled');
  choice(network.provider, 'network.provider', ['local-demo', 'ns3']);
  const radioProfiles = array(network.radio_profiles, 'network.radio_profiles');
  const radioIds = new Set();
  radioProfiles.forEach((entry, index) => {
    const path = `network.radio_profiles[${index}]`;
    const p = object(entry, path);
    keys(p, path, ['id', 'wifi_standard', 'frequency_ghz', 'channel_width_mhz', 'tx_power_dbm', 'rx_sensitivity_dbm']);
    id(p.id, `${path}.id`);
    if (radioIds.has(p.id)) error(`${path}.id`, 'Radio profile IDs must be unique.');
    radioIds.add(p.id);
    choice(p.wifi_standard, `${path}.wifi_standard`, ['802.11n', '802.11ac', '802.11ax']);
    number(p.frequency_ghz, `${path}.frequency_ghz`, { positive: true });
    choice(p.channel_width_mhz, `${path}.channel_width_mhz`, [20, 40, 80, 160]);
    number(p.tx_power_dbm, `${path}.tx_power_dbm`);
    number(p.rx_sensitivity_dbm, `${path}.rx_sensitivity_dbm`);
    if (p.wifi_standard === '802.11n' && p.channel_width_mhz > 40) error(`${path}.channel_width_mhz`, '802.11n supports only 20 or 40 MHz in this authoring contract.');
    if (Number.isFinite(p.frequency_ghz) && (p.frequency_ghz < 2.4 || p.frequency_ghz > 7.125)) warn(`${path}.frequency_ghz`, 'Frequency lies outside common Wi-Fi bands; provider/regulatory acceptance is unverified.');
  });
  if (network.enabled === true && radioProfiles.length === 0) error('network.radio_profiles', 'An enabled network needs at least one radio profile.');
  const link = object(network.link, 'network.link');
  keys(link, 'network.link', ['configured_rate_mbps', 'propagation_delay_ms']);
  number(link.configured_rate_mbps, 'network.link.configured_rate_mbps', { positive: true });
  number(link.propagation_delay_ms, 'network.link.propagation_delay_ms', { min: 0 });
  if (Number.isFinite(link.propagation_delay_ms) && !Number.isSafeInteger(link.propagation_delay_ms * 1e6)) error('network.link.propagation_delay_ms', 'Delay must convert to a safe integer number of nanoseconds.');
  if (Number.isFinite(link.configured_rate_mbps) && !Number.isSafeInteger(link.configured_rate_mbps * 1e6)) error('network.link.configured_rate_mbps', 'Rate must convert to a safe integer number of bits per second.');
  if (network.provider === 'ns3') warn('network.provider', 'ns3 is a desired external provider. This console has no ns3 process, RPC attachment, or verified execution.');
  if (network.provider === 'local-demo') warn('network.provider', 'The local demonstration is an authored approximation, not ns3 or measured radio telemetry.');
  const studyValidation = validateNetworkStudy(network.study, radioProfiles);
  errors.push(...studyValidation.errors);
  warnings.push(...studyValidation.warnings);

  const compute = object(c.compute, 'compute');
  keys(compute, 'compute', ['provider', 'profiles']);
  choice(compute.provider, 'compute.provider', ['local-demo']);
  const profiles = array(compute.profiles, 'compute.profiles');
  const computeIds = new Set();
  profiles.forEach((entry, index) => {
    const path = `compute.profiles[${index}]`;
    const p = object(entry, path);
    keys(p, path, ['id', 'cpu_cores', 'gpu_units', 'memory_mb', 'queue_limit', 'deadline_ms']);
    id(p.id, `${path}.id`);
    if (computeIds.has(p.id)) error(`${path}.id`, 'Compute profile IDs must be unique.');
    computeIds.add(p.id);
    number(p.cpu_cores, `${path}.cpu_cores`, { positive: true });
    number(p.gpu_units, `${path}.gpu_units`, { min: 0 });
    number(p.memory_mb, `${path}.memory_mb`, { positive: true, integer: true });
    number(p.queue_limit, `${path}.queue_limit`, { min: 0, integer: true });
    number(p.deadline_ms, `${path}.deadline_ms`, { positive: true });
  });

  const mobility = object(c.mobility, 'mobility');
  keys(mobility, 'mobility', ['sumo', 'uav']);
  const sumo = object(mobility.sumo, 'mobility.sumo');
  keys(sumo, 'mobility.sumo', ['enabled', 'network_asset', 'route_asset', 'step_ms']);
  bool(sumo.enabled, 'mobility.sumo.enabled');
  string(sumo.network_asset, 'mobility.sumo.network_asset');
  string(sumo.route_asset, 'mobility.sumo.route_asset');
  number(sumo.step_ms, 'mobility.sumo.step_ms', { integer: true, positive: true });
  if (sumo.enabled && stepValid && sumo.step_ms !== s.step_ms) error('mobility.sumo.step_ms', 'SUMO must advance exactly one matching BENCH step per barrier; step_ms must equal scenario.step_ms.');
  if (sumo.enabled) warn('mobility.sumo', 'SUMO assets are references only. This console neither verifies those files nor launches a SUMO clock.');
  const uav = object(mobility.uav, 'mobility.uav');
  keys(uav, 'mobility.uav', ['model', 'cruise_speed_mps', 'altitude_m']);
  choice(uav.model, 'mobility.uav.model', ['waypoint', 'hold']);
  number(uav.cruise_speed_mps, 'mobility.uav.cruise_speed_mps', { min: 0 });
  number(uav.altitude_m, 'mobility.uav.altitude_m');

  const entities = array(c.entities, 'entities');
  if (entities.length === 0) error('entities', 'At least one entity template is required.');
  const templateIds = new Set();
  const expandedIds = new Set();
  let total = 0;
  entities.forEach((entry, index) => {
    const path = `entities[${index}]`;
    const e = object(entry, path);
    keys(e, path, ['id', 'type', 'count', 'provider', 'position_enu_m', 'radio_profile_id', 'compute_profile_id']);
    id(e.id, `${path}.id`);
    if (templateIds.has(e.id)) error(`${path}.id`, 'Entity template IDs must be unique.');
    templateIds.add(e.id);
    choice(e.type, `${path}.type`, ENTITY_TYPES);
    const countValid = number(e.count, `${path}.count`, { min: 1, integer: true });
    if (countValid) {
      total += e.count;
      // Bound authoring expansion to keep malformed imports from freezing a browser.
      if (e.count > 10000) error(`${path}.count`, 'The local authoring limit is 10,000 instances per template.');
      else if (total <= 10000 && typeof e.id === 'string') {
        for (const instanceId of instanceIds(e)) {
          if (expandedIds.has(instanceId)) error(`${path}.id`, `Expanded entity ID collides: ${instanceId}.`);
          expandedIds.add(instanceId);
        }
      }
    }
    choice(e.provider, `${path}.provider`, ['local-demo', 'sumo', 'static']);
    if (e.provider === 'sumo') {
      if (!['vehicle', 'pedestrian'].includes(e.type)) error(`${path}.provider`, 'SUMO entity provider supports vehicle/pedestrian templates only.');
      if (!sumo.enabled) error(`${path}.provider`, 'Enable SUMO before assigning entities to it.');
    }
    if (!DYNAMIC_TYPES.includes(e.type) && e.provider !== 'static') error(`${path}.provider`, 'Infrastructure templates use the static provider.');
    if (!vector(e.position_enu_m)) error(`${path}.position_enu_m`, 'Must be exactly three finite metre values [east, north, up].');
    else if (e.position_enu_m.some(value => Math.abs(value) > 1e9)) error(`${path}.position_enu_m`, 'The local authoring limit is an absolute coordinate magnitude of 1,000,000,000 metres.');
    for (const [field, ids] of [['radio_profile_id', radioIds], ['compute_profile_id', computeIds]]) {
      if (e[field] !== null) {
        id(e[field], `${path}.${field}`);
        if (!ids.has(e[field])) error(`${path}.${field}`, 'Referenced profile does not exist. Use null for no profile.');
      }
    }
    if (!network.enabled && e.radio_profile_id !== null) warn(`${path}.radio_profile_id`, 'Profile reference is retained while networking is disabled.');
  });
  if (total > 10000) error('entities', 'The local authoring limit is 10,000 expanded entities.');

  const semantics = object(c.semantics, 'semantics');
  keys(semantics, 'semantics', ['adapter', 'execution', 'bindings']);
  choice(semantics.adapter, 'semantics.adapter', ['atlas']);
  choice(semantics.execution, 'semantics.execution', ['not_connected']);
  const bindings = array(semantics.bindings, 'semantics.bindings');
  const bindingIds = new Set();
  const inspectParameters = (value, path, seen = new Set(), depth = 0) => {
    if (depth > 64) { error(path, 'Parameter nesting exceeds the local limit of 64 levels.'); return; }
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
    if (typeof value === 'number') { number(value, path); return; }
    if (!isObject(value) && !Array.isArray(value)) { error(path, 'Parameters must contain only JSON values.'); return; }
    if (seen.has(value)) { error(path, 'Parameters cannot contain circular references.'); return; }
    seen.add(value);
    for (const [key, child] of Object.entries(value)) inspectParameters(child, `${path}.${key}`, seen, depth + 1);
    seen.delete(value);
  };
  bindings.forEach((entry, index) => {
    const path = `semantics.bindings[${index}]`;
    const b = object(entry, path);
    keys(b, path, ['id', 'target', 'entity_id', 'parameters']);
    id(b.id, `${path}.id`);
    if (bindingIds.has(b.id)) error(`${path}.id`, 'Binding IDs must be unique.');
    bindingIds.add(b.id);
    id(b.target, `${path}.target`);
    id(b.entity_id, `${path}.entity_id`);
    if (!expandedIds.has(b.entity_id)) error(`${path}.entity_id`, 'Binding must name an exact expanded entity ID, not a group alias.');
    const params = object(b.parameters, `${path}.parameters`);
    inspectParameters(params, `${path}.parameters`);
    if (typeof b.target === 'string' && /pair|conflict/.test(b.target)) warn(`${path}.target`, 'Pair/clearance targets require explicit relational bindings and body geometry. This single-actor plan does not supply them.');
  });
  warn('semantics.execution', 'Atlas is not connected. Bindings are desired configuration only; no predicates or events have been evaluated.');
  const provenance = object(c.provenance, 'provenance');
  if (typeof provenance.kind !== 'string' || !provenance.kind.trim()) error('provenance.kind', 'Declare the source kind, such as authored_synthetic.');
  return { valid: errors.length === 0, errors, warnings };
}

/** Deterministic structural leaf diff. Array positions are explicit, never identity joins. */
export function diffConfig(before, after) {
  const changes = [];
  const visit = (a, b, path) => {
    if (Object.is(a, b)) return;
    if (Array.isArray(a) && Array.isArray(b)) {
      for (let i = 0; i < Math.max(a.length, b.length); i++) visit(a[i], b[i], `${path}[${i}]`);
    } else if (isObject(a) && isObject(b)) {
      for (const key of [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()) visit(a[key], b[key], path ? `${path}.${key}` : key);
    } else {
      changes.push({ path: path || '$', before: clone(a), after: clone(b) });
    }
  };
  visit(before, after, '');
  return changes;
}

/** Compile a reviewable desired plan without creating processes or claiming backend execution. */
export function compileConfig(config) {
  const validation = validateConfig(config);
  const diagnostics = [
    ...validation.errors.map(item => ({ severity: 'error', code: 'invalid_config', ...item })),
    ...validation.warnings.map(item => ({ severity: 'warning', code: 'capability_limit', ...item })),
  ];
  if (!validation.valid) return { ok: false, manifest: null, diagnostics };
  const c = clone(config);
  const mappings = [
    { source: 'scenario.step_ms', target: 'desired_clock.step_ns', status: 'desired_plan', conversion: 'integer milliseconds × 1,000,000; BENCH alone owns physical ticking' },
    { source: 'entities[].id/count', target: 'entities[].entity_id', status: 'desired_plan', conversion: 'count 1 retains ID; count >1 appends -1 … -N; no source identity inference' },
    { source: 'entities[].position_enu_m', target: 'initial_position_enu_m', status: 'desired_plan', conversion: 'ENU metres; render projection [east, up, -north] only at display boundary' },
    { source: 'network.radio_profiles[]', target: 'ResolvedScenario.network.radio_profiles', status: 'requires_bench_compiler', conversion: 'id → radio_profile_id; provider_id, topology and resolved resources still required' },
    { source: 'network.link.configured_rate_mbps', target: 'data_rate_bps', status: 'desired_plan', conversion: 'Mbps × 1,000,000; configured capacity, never measured throughput' },
    { source: 'network.link.propagation_delay_ms', target: 'propagation_delay_ns', status: 'desired_plan', conversion: 'ms × 1,000,000; propagation delay, never measured end-to-end latency' },
    { source: 'mobility.sumo', target: 'SUMO provider attachment', status: 'not_connected', conversion: 'asset references only; no asset validation or second SUMO clock' },
    { source: 'compute.profiles[]', target: 'local authored compute model', status: 'local_only', conversion: 'CPU cores / normalized GPU units / MB; no verified external compute-stage adapter' },
    { source: 'semantics.bindings[]', target: 'Atlas binding-isolated contexts', status: 'not_connected', conversion: 'intent only; exact field_map, runtime, catalogue validation and evaluation remain required' },
  ];
  diagnostics.push({ severity: 'info', code: 'desired_plan_only', path: '$', message: 'Compilation creates an authored desired plan. It is not an AERO_BENCH resolved configuration, provider receipt, or verified execution.' });
  diagnostics.push({ severity: 'warning', code: 'unsupported_extent', path: 'entities', message: 'No mobile body dimensions are supplied. Body clearance and geometry-dependent Atlas claims are unsupported.' });
  diagnostics.push({ severity: 'warning', code: 'compute_local_only', path: 'compute', message: 'Compute profiles are local demonstration parameters; no real CPU/GPU measurement or external compute provider is connected.' });
  if (c.network.study) {
    mappings.push({ source: 'network.study', target: 'future versioned research adapter', status: 'unsupported_research_only', conversion: 'Preserved with source profile/version and explicit units; PHY, offered load, propagation, observation TTL, application deadline and queue remain distinct.' });
    diagnostics.push({ severity: 'warning', code: 'network_study_not_executable', path: 'network.study', message: 'Research design is retained without execution. No calibrated propagation, PHY, traffic, queue, geometry or antenna backend has been supplied.' });
    for (const hint of c.network.study.research_hints) diagnostics.push({ severity: 'warning', code: 'unsupported_study_hint', path: 'network.study.research_hints', message: `${hint.label ?? hint.id ?? 'Model hint'} remains unimplemented.` });
  }
  const manifest = {
    schema_version: 'aero-console.desired-plan/v1',
    artifact_kind: 'desired_configuration_plan',
    execution_status: 'not_started',
    is_bench_resolved_config: false,
    metadata: c.metadata,
    scenario: c.scenario,
    desired_clock: {
      physical_authority: 'AERO_BENCH_when_connected',
      local_demo_authority: 'authored_demo_only',
      step_ns: (BigInt(c.scenario.step_ms) * 1000000n).toString(),
      duration_ns: (BigInt(exactMilliseconds(c.scenario.duration_s)) * 1000000n).toString(),
      total_ticks: exactMilliseconds(c.scenario.duration_s) / c.scenario.step_ms,
    },
    entities: c.entities.flatMap(entity => instanceIds(entity).map(entityId => ({
      entity_id: entityId,
      template_id: entity.id,
      type: entity.type,
      provider: entity.provider,
      initial_position_enu_m: clone(entity.position_enu_m),
      radio_profile_id: entity.radio_profile_id,
      compute_profile_id: entity.compute_profile_id,
    }))),
    mobility: c.mobility,
    network: {
      ...c.network,
      configured_link: {
        data_rate_bps: Math.round(c.network.link.configured_rate_mbps * 1e6),
        propagation_delay_ns: String(Math.round(c.network.link.propagation_delay_ms * 1e6)),
        quantity_kind: 'configured',
        measured_throughput_bps: null,
        measured_end_to_end_latency_ms: null,
      },
      connection_status: c.network.provider === 'ns3' ? 'not_connected' : 'local_demonstration',
      ...(c.network.study ? { research_preview: deriveStudyQuantities(c) } : {}),
    },
    compute: { ...c.compute, connection_status: 'local_demonstration' },
    semantics: { ...c.semantics, evaluated_count: 0 },
    provenance: c.provenance,
    source_mapping: mappings,
    unsupported: ['BENCH resolved scenario emission', 'SUMO/ns3 live execution', 'external compute provider execution', 'Atlas runtime evaluation', 'mobile body extents and pair clearance', 'measured network/compute telemetry', ...(c.network.study ? ['network research profile execution and calibration', ...c.network.study.research_hints.map(hint => hint.label ?? hint.id ?? 'research model')] : [])],
    source_cursor: clone(INITIAL_SOURCE_CURSOR),
  };
  return { ok: true, manifest, diagnostics };
}

function preciseUnsigned(value) {
  if (typeof value === 'number') return Number.isSafeInteger(value) && value >= 0 ? String(value) : null;
  if (typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value)) return value;
  return null;
}

/**
 * Normalize only the verified SceneState motion projection. This is not full BENCH
 * schema validation. Missing/invalid motion evidence is rejected, never zero-filled.
 * Registry may be an array, Map, or object keyed by exact source entity_id.
 */
export function normalizeSceneState(scene, entities) {
  const diagnostics = [];
  const error = (path, message) => diagnostics.push({ severity: 'error', code: 'invalid_scene_evidence', path, message });
  const warning = (path, message) => diagnostics.push({ severity: 'warning', code: 'missing_provenance', path, message });
  if (!isObject(scene)) return { ok: false, frame: null, diagnostics: [{ severity: 'error', code: 'invalid_scene_evidence', path: '$', message: 'SceneState must be an object.' }] };
  if (scene.schema_version !== 'aero-bench.scene-state/v1') error('schema_version', 'Expected aero-bench.scene-state/v1.');
  const tick = scene.at?.tick;
  const timeNs = preciseUnsigned(scene.at?.sim_time_ns);
  if (!Number.isSafeInteger(tick) || tick < 1) error('at.tick', 'A SceneState motion frame starts at tick 1 and uses safe integer ticks.');
  if (timeNs === null) error('at.sim_time_ns', 'Use nonnegative safe integer ns, or a decimal string for larger exact ns.');
  for (const field of ['run_id', 'scenario_digest', 'scene_state_digest']) {
    if (typeof scene[field] !== 'string' || !HASH.test(scene[field])) error(field, 'Expected a 64-character lowercase SHA-256 identifier/digest.');
  }
  if (!isObject(scene.stage_barrier)) error('stage_barrier', 'The source motion barrier is required; a motion sample alone does not establish a committed scene.');
  else diagnostics.push({ severity: 'warning', code: 'barrier_unverified', path: 'stage_barrier', message: 'Barrier substructure and digests are retained but not validated. This motion projection does not establish a verified commit.' });
  const registry = new Map();
  const registryProvided = entities !== undefined && entities !== null;
  if (Array.isArray(entities)) {
    entities.forEach((entity, index) => {
      const id = entity?.entity_id ?? entity?.id;
      if (typeof id !== 'string' || !ID.test(id)) { error(`entities[${index}]`, 'Registry requires explicit entity_id or id.'); return; }
      if (registry.has(id)) error(`entities[${index}]`, `Duplicate registry entity ID: ${id}.`);
      registry.set(id, entity);
    });
  } else if (entities instanceof Map) {
    for (const [id, entity] of entities) registry.set(id, entity);
  } else if (isObject(entities)) {
    for (const [id, entity] of Object.entries(entities)) registry.set(id, entity);
  } else if (registryProvided) error('entities', 'Registry must be an array, Map, or exact-ID-keyed object.');
  for (const [id, entity] of registry) {
    if (typeof id !== 'string' || !ID.test(id) || !isObject(entity)) {
      error('entities', 'Registry keys must be valid entity IDs and values must be entity records.');
    } else if ((entity.entity_id !== undefined && entity.entity_id !== id) || (entity.entity_id === undefined && entity.id !== undefined && entity.id !== id)) {
      error('entities', `Registry key ${id} does not match its explicit entity identity.`);
    }
  }
  if (!registryProvided) warning('entities', 'No static identity registry supplied. Source IDs are preserved; roles and ownership are unavailable.');
  const declared = Array.isArray(scene.declared_entity_ids) ? scene.declared_entity_ids : [];
  if (!declared.length || declared.some(id => typeof id !== 'string' || !ID.test(id))) error('declared_entity_ids', 'Supply a nonempty array of exact valid identifiers.');
  if (new Set(declared).size !== declared.length) error('declared_entity_ids', 'Declared entity IDs must be unique.');
  if (!Array.isArray(scene.samples) || scene.samples.length === 0) error('samples', 'SceneState.samples must be a nonempty array.');
  const seen = new Set();
  const normalized = [];
  for (const [index, sample] of (Array.isArray(scene.samples) ? scene.samples : []).entries()) {
    const path = `samples[${index}]`;
    if (!isObject(sample)) { error(path, 'Sample must be an object.'); continue; }
    const id = sample.entity_id;
    if (typeof id !== 'string' || !ID.test(id)) error(`${path}.entity_id`, 'Expected an exact valid entity identifier.');
    if (seen.has(id)) error(`${path}.entity_id`, 'Duplicate entity sample.');
    seen.add(id);
    if (!declared.includes(id)) error(`${path}.entity_id`, 'Sample entity is absent from declared_entity_ids.');
    if (registryProvided && !registry.has(id)) error(`${path}.entity_id`, 'No exact entity registry match. No prefix or array-position matching is allowed.');
    if (sample.schema_version !== 'aero-bench.state-sample/v1') error(`${path}.schema_version`, 'Expected aero-bench.state-sample/v1.');
    if (sample.stage !== 'motion') error(`${path}.stage`, 'SceneState samples must belong to the motion stage.');
    if (!['static', 'dynamic'].includes(sample.sample_kind)) error(`${path}.sample_kind`, 'Expected static or dynamic source sample kind.');
    if (sample.run_id !== scene.run_id || sample.scenario_digest !== scene.scenario_digest) error(path, 'Sample run/scenario identity differs from the containing SceneState.');
    if (sample.at?.tick !== tick || preciseUnsigned(sample.at?.sim_time_ns) !== timeNs) error(`${path}.at`, 'Sample and SceneState must share the exact tick and ns timestamp.');
    if (typeof sample.provider_id !== 'string' || !ID.test(sample.provider_id)) error(`${path}.provider_id`, 'A valid source provider ID is required.');
    if (typeof sample.sample_digest !== 'string' || !HASH.test(sample.sample_digest)) error(`${path}.sample_digest`, 'A SHA-256 source sample digest is required.');
    const pos = sample.pose?.position?.enu;
    const velocity = sample.linear_velocity_enu;
    const position = [pos?.east_m, pos?.north_m, pos?.up_m];
    const linearVelocity = [velocity?.east_mps, velocity?.north_mps, velocity?.up_mps];
    if (!vector(position)) error(`${path}.pose.position.enu`, 'Required finite ENU position fields are east_m, north_m, up_m.');
    if (!vector(linearVelocity)) error(`${path}.linear_velocity_enu`, 'Required finite ENU velocity fields are east_mps, north_mps, up_mps.');
    const horizontalSpeed = vector(linearVelocity) ? Math.hypot(linearVelocity[0], linearVelocity[1]) : null;
    if (horizontalSpeed !== null && !Number.isFinite(horizontalSpeed)) error(`${path}.linear_velocity_enu`, 'Horizontal speed overflows the finite evidence range.');
    if (velocity?.frame_id !== undefined && velocity.frame_id !== 'ENU') error(`${path}.linear_velocity_enu.frame_id`, 'Velocity frame must be ENU.');
    const registered = registry.get(id);
    normalized.push({
      entity_id: id,
      type: registered?.kind ?? registered?.type ?? null,
      owner_kind: registered?.owner_kind ?? null,
      owner_id: registered?.owner_id ?? null,
      generation: registered?.generation ?? null,
      position_enu_m: position,
      velocity_enu_mps: linearVelocity,
      horizontal_speed_mps: horizontalSpeed,
      vertical_speed_mps: vector(linearVelocity) ? linearVelocity[2] : null,
      sample_kind: sample.sample_kind,
      provenance: { kind: 'simulated', provider_id: sample.provider_id, sample_digest: sample.sample_digest, scene_state_digest: scene.scene_state_digest, source_path: path, stage: 'motion' },
    });
  }
  for (const id of declared) if (!seen.has(id)) error('samples', `Missing sample for declared entity ${id}.`);
  if (diagnostics.some(item => item.severity === 'error')) return { ok: false, frame: null, diagnostics };
  return {
    ok: true,
    diagnostics,
    frame: {
      schema_version: 'aero-console.motion-frame/v1',
      run_id: scene.run_id,
      scenario_digest: scene.scenario_digest,
      tick,
      time_ns: timeNs,
      frame: 'ENU',
      vertical_datum: 'local',
      entities: normalized,
      source: { schema_version: scene.schema_version, scene_state_digest: scene.scene_state_digest, stage_barrier: clone(scene.stage_barrier), source_kind: 'simulated', projection_only: true, integrity_verified: false },
      readiness: { motion: 'barrier_unverified', network: 'not_established', business: 'not_established' },
      unsupported: ['orientation mapping', 'body dimensions', 'cross-domain stage readiness', 'Atlas evaluation'],
    },
  };
}

/**
 * Advance exactly one live SSE family cursor without regressing others. Accepts a
 * BENCH JSON envelope, or {type:'scene.state', data: envelope}. Sealed replay has
 * a separate indexed-shard contract and must never be passed here.
 */
export function advanceSourceCursor(cursor = INITIAL_SOURCE_CURSOR, event) {
  if (!isObject(cursor)) throw new TypeError('Source cursor must be an object.');
  const next = { ...INITIAL_SOURCE_CURSOR, ...clone(cursor) };
  for (const [key, min] of [['afterTransition', -1], ['afterSceneTick', 0], ['afterEventSequence', -1]]) {
    if (!Number.isSafeInteger(next[key]) || next[key] < min) throw new TypeError(`Invalid source cursor: ${key}.`);
  }
  if (!isObject(event)) throw new TypeError('SSE event must be an object.');
  let envelope = own(event, 'data') ? event.data : event;
  if (typeof envelope === 'string') {
    try { envelope = JSON.parse(envelope); } catch { throw new TypeError('SSE event data must be JSON.'); }
  }
  if (!isObject(envelope)) throw new TypeError('SSE event envelope must be an object.');
  const schemaTypes = {
    'aero-bench.run-transition-event/v1': 'run.transition',
    'aero-bench.scene-state-stream-event/v1': 'scene.state',
    'aero-bench.public-run-event-stream-event/v1': 'run.event',
  };
  const schemaType = schemaTypes[envelope.schema_version];
  const wireType = event.type ?? (typeof event.event === 'string' ? event.event : undefined);
  if (schemaType && wireType && schemaType !== wireType) throw new TypeError('SSE wire family and envelope schema disagree.');
  const type = schemaType ?? wireType;
  if (envelope.schema_version?.includes('replay')) throw new TypeError('Sealed replay uses indexed shards, not live SSE cursors.');
  if (envelope.schema_version !== undefined && !schemaType) throw new TypeError('Unsupported SSE envelope schema.');
  if (next.run_id !== undefined && envelope.run_id !== next.run_id) throw new TypeError('SSE event belongs to a different run.');
  const definitions = {
    'run.transition': ['afterTransition', envelope.transition?.sequence, 0],
    'scene.state': ['afterSceneTick', envelope.scene_state?.at?.tick, 1],
    'run.event': ['afterEventSequence', envelope.event?.sequence, 0],
  };
  const definition = definitions[type];
  if (!definition) throw new TypeError('Unknown SSE event family.');
  const [key, value, min] = definition;
  if (!Number.isSafeInteger(value) || value < min) throw new TypeError(`Invalid sequence/tick for ${type}.`);
  next[key] = Math.max(next[key], value);
  return next;
}
