/**
 * Research-only network design profiles. Independently authored summaries, not
 * executable ns-3 configuration or a calibration result. Public references only.
 */
export const STUDY_VERSION = '2026-10-05.1';
export const STUDY_SOURCES = {
  ns3_wifi: { title: 'ns-3 Wi-Fi model design', url: 'https://www.nsnam.org/docs/models/html/wifi-design.html', scope: 'Model behavior; resolved runtime attributes must be checked separately.' },
  ns3_channels: { title: 'ns-3 Wi-Fi channel configuration', url: 'https://www.nsnam.org/docs/models/html/wifi-user.html', scope: 'Standard/channel/width configuration; no field calibration.' },
  ns3_rates: { title: 'ns-3 HT nominal rate example', url: 'https://www.nsnam.org/docs/release/3.26/doxygen/wifi-spectrum-saturation-example_8cc_source.html', scope: 'Conditional HT rate arithmetic; not a current API prescription.' },
  ns3_propagation: { title: 'ns-3 propagation models', url: 'https://www.nsnam.org/docs/models/html/propagation.html', scope: 'Model definitions and applicability; no local measured channel.' },
  nxp: { title: 'NXP IW416 data sheet', url: 'https://www.nxp.com/docs/en/data-sheet/IW416.pdf', scope: 'Example commercial 1×1 HT20/40 radio; installed hardware is unidentified.' },
  low_height: { title: 'Low-height UAV channel measurements (2020)', url: 'https://arxiv.org/html/2007.11502', scope: 'External campus/suburban measurements; height, frequency and antenna transfer need review.' },
  urban: { title: 'Urban A2G channel measurements (2026 preprint)', url: 'https://arxiv.org/html/2607.00541v1', scope: 'External urban measurements at different height/bands; preprint, not local calibration.' },
  service: { title: 'ETSI / 3GPP TS 22.125 V19.2.0', url: 'https://www.etsi.org/deliver/etsi_ts/122100_122199/122125/19.02.00_60/ts_122125v190200p.pdf', scope: 'Application requirements; not guaranteed Wi-Fi performance.' },
  miit_uav: { title: 'MIIT civilian UAV radio rules', url: 'https://wap.miit.gov.cn/jgsj/wgj/wjfb/art/2024/art_1c3a092d3bcd4d41abe69c369ba7bab6.html', scope: 'Research frequency boundary; not proof of device compliance or flight permission.' },
  miit_rf: { title: 'MIIT UAV radio technical annex', url: 'https://wap.miit.gov.cn/cms_files/filemanager/1226211233/attach/202312/9f49b4a3029a4e93b21ca06877d2b4ba.pdf', scope: 'Category-dependent limits; conducted power is distinct from EIRP.' },
  miit_wlan: { title: 'MIIT 2.4/5.1/5.8 GHz radio management', url: 'https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/art/2021/art_e4ae71252eab42928daf0ea620976e4e.html', scope: 'Includes indoor restriction for 5150–5350 MHz; do not infer outdoor authorization.' },
};

export const STUDY_EVIDENCE_CLASSES = {
  project_truth: 'Reported configuration',
  derived_quantity: 'Analytical quantity',
  research_assumption: 'Uncalibrated assumption',
  unresolved_runtime_setting: 'Runtime setting unresolved',
  hardware_example: 'Example hardware',
  external_measurement_anchor: 'External measurement reference',
  official_standard_reference: 'Application standard reference',
  official_regulatory_bound: 'Regulatory reference',
};
const meta = (label, unit, evidence_class, source_ids, note) => ({ label, unit, evidence_class, source_ids, note });
/** radio.* paths refer to the selected radio; all other paths are study-relative. */
export const STUDY_FIELD_METADATA = {
  'radio.wifi_standard': meta('Wi-Fi 制式', null, 'project_truth', ['ns3_wifi'], 'R0/R1 retain the reported ad-hoc 802.11n model. This is not LTE/NR.'),
  'radio.frequency_ghz': meta('中心频率', 'GHz', 'project_truth', ['miit_uav', 'miit_wlan', 'ns3_channels'], 'A channel center, not channel width or application data rate. R4 is a separate band study.'),
  'radio.channel_width_mhz': meta('信道带宽', 'MHz', 'project_truth', ['nxp', 'ns3_channels'], '20 MHz is spectrum width. It is not 20 Mbps. 802.11n uses 20/40 MHz here.'),
  'radio.tx_power_dbm': meta('传导发射功率', 'dBm', 'project_truth', ['nxp', 'miit_rf'], 'Conducted power; antenna gain and feeder loss must be listed separately to derive EIRP.'),
  'radio.rx_sensitivity_dbm': meta('接收筛选阈值', 'dBm', 'project_truth', ['ns3_wifi', 'nxp'], 'Receiver filter only. This is not a PER curve or hard communication radius.'),
  'phy.mcs': meta('固定 PHY MCS', null, 'project_truth', ['ns3_channels', 'ns3_rates'], 'Fixed MCS experiments do not emulate real hardware automatic rate selection.'),
  'phy.nominal_rate_mbps': meta('名义 PHY 速率', 'Mbps', 'derived_quantity', ['ns3_rates'], 'Conditional on MCS, width, spatial streams and GI; not payload goodput or offered load.'),
  'phy.spatial_streams': meta('空间流数', 'streams', 'project_truth', ['nxp'], 'Single-stream reference; additional streams need matching hardware and model assumptions.'),
  'phy.guard_interval_ns': meta('保护间隔', 'ns', 'research_assumption', ['ns3_channels'], '800 ns is an assumed long-GI reference. The baseline actual GI remains unverified.'),
  'propagation.reference_distance_m': meta('参考距离', 'm', 'project_truth', ['ns3_propagation'], 'The close-in reference distance; behavior below it needs a declared policy.'),
  'propagation.reference_loss_db': meta('参考路径损耗 L₀', 'dB', 'derived_quantity', ['ns3_propagation'], 'R1 derives 1 m loss from frequency. R0 deliberately preserves a known mismatched value.'),
  'propagation.path_loss_exponent': meta('基线距离指数', 'dimensionless', 'project_truth', ['ns3_propagation'], 'The retained value 3 is not a calibrated city model. R2 LOS/NLOS priors remain separate unsupported hints.'),
  'propagation.noise_figure_db': meta('接收噪声系数', 'dB', 'project_truth', ['ns3_wifi'], 'NF=7 dB is retained, not measured. Interference cannot be represented by arbitrarily increasing NF.'),
  'traffic.offered_load_mbps': meta('每源业务负载', 'Mbps/source', 'research_assumption', [], 'Explicit per-source offered payload demand. Null means unspecified; no active sender count is inferred.'),
  'traffic.payload_bytes': meta('应用包大小', 'bytes', 'research_assumption', [], 'Payload size needs an explicit workload. A 1200-byte video chunk would be a modeling choice.'),
  'traffic.observation_ttl_ms': meta('观察 TTL', 'ms', 'project_truth', [], 'Legacy success is strictly RX < first_tx + TTL. This is not IP TTL, which counts hops.'),
  'traffic.application_deadline_ms': meta('应用期限', 'ms', 'research_assumption', ['service'], 'Separate generation-time application deadline. Null means no selected application requirement.'),
  'traffic.ttl_origin': meta('TTL 起算点', null, 'project_truth', [], 'first_tx is preserved for the legacy observation contract.'),
  'traffic.application_deadline_origin': meta('应用期限起算点', null, 'official_standard_reference', ['service'], 'generation_time includes pre-transmission waiting; MAC ACK does not establish an application ACK.'),
  'queue.max_packets': meta('MAC 队列上限', 'packets', 'project_truth', [], 'Reported 500-packet setting, not a measured hardware buffer capacity.'),
  'queue.max_delay_ms': meta('MAC 队列寿命', 'ms', 'project_truth', [], '500 ms can exceed a 200 ms observation TTL. Changing expiry is a policy change, not a metric correction.'),
};

const copy = value => typeof structuredClone === 'function' ? structuredClone(value) : JSON.parse(JSON.stringify(value));
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const baseRadio = { wifi_standard: '802.11n', frequency_ghz: 2.412, channel_width_mhz: 20, tx_power_dbm: 16, rx_sensitivity_dbm: -95 };
const baseStudy = {
  profile_id: 'R0', profile_version: STUDY_VERSION, radio_profile_id: null, variant_id: null,
  execution: 'research_only', calibrated: false,
  phy: { mcs: 'HtMcs0', nominal_rate_mbps: 6.5, spatial_streams: 1, guard_interval_ns: 800 },
  propagation: { reference_distance_m: 1, reference_loss_db: 46.6777, path_loss_exponent: 3, noise_figure_db: 7 },
  traffic: { offered_load_mbps: null, payload_bytes: null, observation_ttl_ms: 200, application_deadline_ms: null, ttl_origin: 'first_tx', application_deadline_origin: 'generation_time' },
  queue: { max_packets: 500, max_delay_ms: 500 },
  research_hints: [],
};
const geometryHints = [
  { id: 'geometry', label: 'Geometry-conditioned city propagation', status: 'unimplemented', evidence_class: 'research_assumption', source_ids: ['low_height', 'urban'], values: { building_obstruction: true, los_exponent: 2.2, nlos_exponent: 3, los_shadow_sigma_db: 4, nlos_shadow_sigma_db: 6, shadow_correlation_m: 10 }, note: 'Uncalibrated priors. Requires polygon extrusion, valid heights and spatially correlated reciprocal shadowing. No geometry or wall-loss model is executed.' },
];
function profile(id, name, summary, refs, changes = {}) {
  const study = copy(baseStudy);
  study.profile_id = id;
  if (id !== 'R0') study.propagation.reference_loss_db = 40.095329;
  if (['R2', 'R3', 'R4', 'R5'].includes(id)) study.research_hints = copy(geometryHints);
  return { id, version: STUDY_VERSION, name, summary, status: 'research_only', calibrated: false, refs: refs.map(source => ({ id: source, ...STUDY_SOURCES[source] })), radio: copy(baseRadio), study, research_hints: study.research_hints, variants: [], ...changes };
}
const r0 = profile('R0', '原样基线', '保留已报告的 2412 MHz / HT20 / MCS0 / 16 dBm 与错误 L₀；GI 等未核验设置仍是研究参考。', ['ns3_wifi', 'ns3_rates']);
const r1 = profile('R1', '频率参考修正', '与 R0 成对比较，仅将 1 m 参考损耗从 46.6777 改为 40.095329 dB；未运行、未校准。', ['ns3_propagation', 'ns3_channels']);
const r2 = profile('R2', '城市几何先验', '在 R1 上声明 LOS/NLOS、楼宇与相关阴影研究先验；本控制台不执行这些模型。', ['low_height', 'urban']);
const r3 = profile('R3', 'PHY 敏感性', '在相同几何研究条件下分别比较 MCS3 与 MCS7；26/65 Mbps 均为名义 PHY 速率。', ['ns3_rates', 'nxp']);
r3.variants = [
  { id: 'mcs3', label: 'HtMcs3 · HT20 · 26 Mbps', phy: { mcs: 'HtMcs3', nominal_rate_mbps: 26 } },
  { id: 'mcs7', label: 'HtMcs7 · HT20 · 65 Mbps', phy: { mcs: 'HtMcs7', nominal_rate_mbps: 65 } },
];
r3.study.phy = { ...r3.study.phy, ...r3.variants[0].phy };
r3.study.variant_id = 'mcs3';
const r4 = profile('R4', '频段与带宽敏感性', '独立比较 5745 MHz / 20 MHz 与 5755 MHz / 40 MHz；重算参考损耗与噪声，保持总 EIRP 和业务条件。', ['ns3_channels', 'miit_uav', 'miit_wlan', 'miit_rf']);
r4.variants = [
  { id: '5745-20', label: '5745 MHz · 20 MHz', radio: { frequency_ghz: 5.745, channel_width_mhz: 20 }, propagation: { reference_loss_db: 47.633584 }, phy: { nominal_rate_mbps: 6.5 } },
  { id: '5755-40', label: '5755 MHz · 40 MHz · channel 151', radio: { frequency_ghz: 5.755, channel_width_mhz: 40 }, propagation: { reference_loss_db: 47.648690 }, phy: { nominal_rate_mbps: 13.5 }, research_hints: [{ id: 'operating-channel', label: 'Operating-channel candidate', status: 'unimplemented', evidence_class: 'research_assumption', source_ids: ['ns3_channels'], values: { channel_number: 151 }, note: 'Validate the actual provider operating-channel tuple; this hint configures no backend.' }] },
];
r4.radio = { ...r4.radio, ...r4.variants[0].radio };
r4.study.propagation = { ...r4.study.propagation, ...r4.variants[0].propagation };
r4.study.variant_id = '5745-20';
const r5 = profile('R5', '硬件约束候选', '17 dBm conducted + 2 dBi peak − 1 dB feeder = 18 dBm modeled EIRP；实际硬件与天线未确定。', ['nxp', 'miit_rf']);
r5.radio.tx_power_dbm = 17;
r5.study.research_hints.push({ id: 'antenna-hardware', label: 'Unidentified hardware / antenna candidate', status: 'unimplemented', evidence_class: 'hardware_example', source_ids: ['nxp', 'miit_rf'], values: { conducted_tx_dbm: 17, antenna_peak_gain_dbi: 2, feeder_loss_each_end_db: 1, modeled_eirp_dbm: 18 }, note: 'A scalar arithmetic example, not a measured EIRP or installed 3D antenna pattern. Count feeder/antenna effects once.' });

/** Public profile catalogue is immutable; Apply always returns a detached copy. */
function freeze(value) { if (value && typeof value === 'object' && !Object.isFrozen(value)) { Object.freeze(value); Object.values(value).forEach(freeze); } return value; }
export const STUDY_PROFILES = freeze([r0, r1, r2, r3, r4, r5]);

function differences(before, after, path = '') {
  if (Object.is(before, after)) return [];
  if (Array.isArray(before) && Array.isArray(after)) return Array.from({ length: Math.max(before.length, after.length) }, (_, i) => differences(before[i], after[i], `${path}[${i}]`)).flat();
  if (record(before) && record(after)) return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort().flatMap(key => differences(before[key], after[key], path ? `${path}.${key}` : key));
  return [{ path: path || '$', before: before === undefined ? undefined : copy(before), after: after === undefined ? undefined : copy(after) }];
}

/** Apply is an explicit draft edit. It does not change provider, link rates or traffic automatically. */
export function applyStudyProfile(config, id, options = {}) {
  const selected = STUDY_PROFILES.find(item => item.id === id);
  if (!selected) throw new TypeError(`Unknown network research profile: ${id}.`);
  if (!record(config?.network) || !Array.isArray(config.network.radio_profiles) || !config.network.radio_profiles.length) throw new TypeError('Create a radio profile before applying a network study.');
  const changed = copy(config);
  const radioId = options.radio_profile_id ?? changed.network.radio_profiles[0].id;
  const radio = changed.network.radio_profiles.find(item => item.id === radioId);
  if (!radio) throw new TypeError(`Selected radio profile does not exist: ${radioId}.`);
  const study = copy(selected.study);
  const radioValues = copy(selected.radio);
  const variantId = options.variant_id ?? selected.study.variant_id;
  if (variantId !== null && variantId !== undefined) {
    const variant = selected.variants.find(item => item.id === variantId);
    if (!variant) throw new TypeError(`Unknown variant ${variantId} for ${id}.`);
    Object.assign(radioValues, variant.radio ?? {});
    Object.assign(study.phy, variant.phy ?? {});
    Object.assign(study.propagation, variant.propagation ?? {});
    if (variant.research_hints) study.research_hints.push(...copy(variant.research_hints));
    study.variant_id = variant.id;
  }
  Object.assign(radio, radioValues);
  study.radio_profile_id = radio.id;
  changed.network.study = study;
  return {
    config: changed,
    diff: differences(config, changed),
    profile: copy(selected),
    warnings: [
      'Research-only draft edit. No radio, queue, traffic generator, geometry model or ns-3 process was executed.',
      'Reported R0 fields are configuration values, not RF measurements. 800 ns GI is an unverified reference assumption.',
      'Offered load, packet size and application deadline are intentionally unspecified until a workload is selected.',
      ...(study.research_hints.length ? ['Geometry, channel and hardware hints are unsupported backend requirements, not resolved provider settings.'] : []),
    ],
  };
}

/** Free-space reference and thermal noise are analytical previews, never calibrated outputs. */
export function deriveStudyQuantities(config) {
  const study = config?.network?.study;
  const radio = config?.network?.radio_profiles?.find(item => item.id === study?.radio_profile_id);
  if (!study || !radio) return null;
  const frequencyGhz = radio.frequency_ghz;
  const d0 = study.propagation?.reference_distance_m;
  const bandwidthMhz = radio.channel_width_mhz;
  const nf = study.propagation?.noise_figure_db;
  // Work in log space so finite imported values never overflow an intermediate.
  const referenceLoss = Number.isFinite(frequencyGhz) && frequencyGhz > 0 && Number.isFinite(d0) && d0 > 0
    ? 20 * (Math.log10(4 * Math.PI / 299792458) + Math.log10(frequencyGhz) + 9 + Math.log10(d0)) : null;
  const noise = Number.isFinite(bandwidthMhz) && bandwidthMhz > 0 && Number.isFinite(nf)
    ? -174 + 10 * (Math.log10(bandwidthMhz) + 6) + nf : null;
  return {
    kind: 'analytical_reference_only', calibrated: false,
    free_space_reference_loss_db: Number.isFinite(referenceLoss) ? referenceLoss : null,
    integrated_noise_dbm: Number.isFinite(noise) ? noise : null,
    delivered_goodput_mbps: null,
    measured_latency_ms: null,
  };
}

/** Source/units rows for a profile or an applied draft. Field edits remain assumptions. */
export function studyFieldRows(config) {
  const study = config?.network?.study;
  const radio = config?.network?.radio_profiles?.find(item => item.id === study?.radio_profile_id);
  if (!study || !radio) return [];
  const base = STUDY_PROFILES.find(item => item.id === study.profile_id);
  let reference;
  try { reference = base ? applyStudyProfile(config, base.id, { variant_id: study.variant_id, radio_profile_id: study.radio_profile_id }).config : null; } catch { reference = null; }
  const expectedRadio = reference?.network.radio_profiles.find(item => item.id === study.radio_profile_id);
  const get = (object, path) => path.split('.').reduce((value, key) => value?.[key], object);
  return Object.entries(STUDY_FIELD_METADATA).map(([path, metadata]) => {
    const isRadio = path.startsWith('radio.');
    const value = get(isRadio ? radio : study, isRadio ? path.slice(6) : path);
    const expected = get(isRadio ? expectedRadio : reference?.network.study, isRadio ? path.slice(6) : path);
    const changed = reference !== null && !Object.is(value, expected);
    let evidence = metadata.evidence_class;
    if (study.profile_id === 'R0' && path === 'propagation.reference_loss_db') evidence = 'project_truth';
    if (['R3', 'R4', 'R5'].includes(study.profile_id) && (isRadio || path === 'phy.mcs')) evidence = 'research_assumption';
    if (changed) evidence = 'research_assumption';
    return { path, ...copy(metadata), value, evidence_class: evidence, modified_from_profile: changed, sources: metadata.source_ids.map(id => ({ id, ...STUDY_SOURCES[id] })) };
  });
}

/** Validate optional authored research design; no server/provider state is consulted. */
export function validateNetworkStudy(study, radioProfiles = []) {
  const errors = [], warnings = [];
  const error = (path, message) => errors.push({ path: `network.study${path ? `.${path}` : ''}`, message });
  const warn = (path, message) => warnings.push({ path: `network.study${path ? `.${path}` : ''}`, message });
  // Warn on retained/imported radios even when no study has been applied.
  (Array.isArray(radioProfiles) ? radioProfiles : []).forEach((radio, index) => {
    if (radio?.frequency_ghz >= 5.15 && radio.frequency_ghz <= 5.35) warnings.push({path: `network.radio_profiles[${index}].frequency_ghz`, message: 'This indoor-restricted frequency family is not an outdoor-UAV default; check applicable radio rules and device category separately.'});
  });
  if (study === undefined) return { valid: true, errors, warnings };
  if (!record(study)) { error('', 'Study must be an object when present.'); return { valid: false, errors, warnings }; }
  const object = (value, path) => { if (!record(value)) { error(path, 'Must be an object.'); return {}; } return value; };
  const number = (value, path, { positive = false, min = -Infinity, integer = false, nullable = false } = {}) => {
    if (nullable && value === null) return;
    if (!Number.isFinite(value) || value < min || (positive && value <= 0) || (integer && !Number.isSafeInteger(value))) error(path, `Must be a finite ${integer ? 'safe integer' : 'number'}${positive ? ' greater than zero' : min === 0 ? ' at least zero' : ''}${nullable ? ', or null when unspecified' : ''}.`);
  };
  const unsupportedKeys = (value, path, allowed) => Object.keys(value).forEach(key => { if (!allowed.includes(key)) warn(path ? `${path}.${key}` : key, 'Unknown study field is retained as research-only and has no executable mapping.'); });
  unsupportedKeys(study, '', ['profile_id', 'profile_version', 'radio_profile_id', 'variant_id', 'execution', 'calibrated', 'phy', 'propagation', 'traffic', 'queue', 'research_hints']);
  const selected = STUDY_PROFILES.find(item => item.id === study.profile_id);
  if (!selected) error('profile_id', 'Select R0, R1, R2, R3, R4 or R5.');
  if (typeof study.profile_version !== 'string' || !study.profile_version.trim()) error('profile_version', 'A profile version is required.');
  else if (selected && study.profile_version !== selected.version) warn('profile_version', 'This saved profile uses a different catalogue revision; values are retained, not silently upgraded.');
  const radio = Array.isArray(radioProfiles) ? radioProfiles.find(item => item?.id === study.radio_profile_id) : null;
  if (!radio) error('radio_profile_id', 'Research profile must reference an existing radio profile.');
  if (study.execution !== 'research_only') error('execution', 'Only research_only is supported; profile selection does not run a provider.');
  if (study.calibrated !== false) error('calibrated', 'No calibration evidence is bundled; calibrated must be false.');
  if (selected && selected.variants.length && !selected.variants.some(item => item.id === study.variant_id)) error('variant_id', 'Select an explicit supported profile variant.');
  if (selected && !selected.variants.length && study.variant_id !== null) error('variant_id', 'This profile has no variants; use null.');
  const phy = object(study.phy, 'phy');
  unsupportedKeys(phy, 'phy', ['mcs', 'nominal_rate_mbps', 'spatial_streams', 'guard_interval_ns']);
  if (typeof phy.mcs !== 'string' || !/^HtMcs[0-7]$/.test(phy.mcs)) error('phy.mcs', 'This research catalogue supports single-stream HtMcs0 through HtMcs7.');
  number(phy.nominal_rate_mbps, 'phy.nominal_rate_mbps', { positive: true });
  if (phy.spatial_streams !== 1) error('phy.spatial_streams', 'Only the explicitly authored single-stream reference is supported.');
  if (phy.guard_interval_ns !== 800) error('phy.guard_interval_ns', 'This catalogue uses the explicit 800 ns reference; other GI values need a separately versioned rate definition.');
  if (radio && radio.wifi_standard !== '802.11n') error('radio_profile_id', 'HtMcs study requires the selected radio to use 802.11n.');
  const rates = radio?.channel_width_mhz === 20 ? [6.5, 13, 19.5, 26, 39, 52, 58.5, 65] : radio?.channel_width_mhz === 40 ? [13.5, 27, 40.5, 54, 81, 108, 121.5, 135] : null;
  if (radio && !rates) error('radio_profile_id', 'This HT study supports 20 or 40 MHz channel width.');
  if (rates && /^HtMcs[0-7]$/.test(phy.mcs) && phy.nominal_rate_mbps !== rates[Number(phy.mcs.slice(-1))]) error('phy.nominal_rate_mbps', 'Nominal PHY rate disagrees with selected MCS, width, one stream and 800 ns GI. Do not substitute offered load or goodput.');
  const propagation = object(study.propagation, 'propagation');
  unsupportedKeys(propagation, 'propagation', ['reference_distance_m', 'reference_loss_db', 'path_loss_exponent', 'noise_figure_db']);
  number(propagation.reference_distance_m, 'propagation.reference_distance_m', { positive: true });
  number(propagation.reference_loss_db, 'propagation.reference_loss_db', { min: 0 });
  number(propagation.path_loss_exponent, 'propagation.path_loss_exponent', { positive: true });
  number(propagation.noise_figure_db, 'propagation.noise_figure_db', { min: 0 });
  const traffic = object(study.traffic, 'traffic');
  unsupportedKeys(traffic, 'traffic', ['offered_load_mbps', 'payload_bytes', 'observation_ttl_ms', 'application_deadline_ms', 'ttl_origin', 'application_deadline_origin']);
  number(traffic.offered_load_mbps, 'traffic.offered_load_mbps', { nullable: true, min: 0 });
  number(traffic.payload_bytes, 'traffic.payload_bytes', { nullable: true, positive: true, integer: true });
  number(traffic.observation_ttl_ms, 'traffic.observation_ttl_ms', { positive: true });
  number(traffic.application_deadline_ms, 'traffic.application_deadline_ms', { nullable: true, positive: true });
  if (traffic.ttl_origin !== 'first_tx') error('traffic.ttl_origin', 'Preserve the legacy observation origin first_tx. A different origin needs a separate metric contract.');
  if (traffic.application_deadline_origin !== 'generation_time') error('traffic.application_deadline_origin', 'Application deadline starts at generation_time, separately from observation TTL.');
  const queue = object(study.queue, 'queue');
  unsupportedKeys(queue, 'queue', ['max_packets', 'max_delay_ms']);
  number(queue.max_packets, 'queue.max_packets', { positive: true, integer: true });
  number(queue.max_delay_ms, 'queue.max_delay_ms', { positive: true });
  if (!Array.isArray(study.research_hints)) error('research_hints', 'Research hints must be an array.');
  else study.research_hints.forEach((hint, i) => {
    if (!record(hint) || hint.status !== 'unimplemented') error(`research_hints[${i}]`, 'Each model hint must remain explicitly unimplemented.');
  });
  warn('execution', 'All study fields are research-only desired inputs. None are wired to an executable ns-3, traffic, queue or geometry adapter.');
  warn('phy.guard_interval_ns', '800 ns GI is a reference assumption, not a verified baseline runtime setting.');
  if (queue.max_delay_ms > traffic.observation_ttl_ms) warn('queue.max_delay_ms', 'Queue lifetime exceeds observation TTL. Late delivery is possible; TTL failure must not be relabeled RF loss.');
  if (Number.isFinite(traffic.offered_load_mbps) && traffic.offered_load_mbps >= phy.nominal_rate_mbps) warn('traffic.offered_load_mbps', 'Per-source offered load meets/exceeds nominal PHY rate before MAC/IP overhead or multi-source contention. This does not predict actual goodput.');
  if (study.profile_id === 'R0') warn('propagation.reference_loss_db', 'R0 deliberately preserves the historical frequency-mismatched reference loss for paired reproduction.');
  if (Array.isArray(study.research_hints) && study.research_hints.length) warn('research_hints', 'Geometry/PHY/channel/hardware research hints remain unimplemented; profile selection does not enable those capabilities.');
  return { valid: errors.length === 0, errors, warnings };
}
