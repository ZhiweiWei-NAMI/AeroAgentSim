import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_CONFIG, clone, validateConfig, compileConfig, diffConfig } from '../src/config.js';
import { STUDY_PROFILES, STUDY_SOURCES, STUDY_FIELD_METADATA, STUDY_VERSION, applyStudyProfile, deriveStudyQuantities, studyFieldRows, validateNetworkStudy } from '../src/network-study.js';

const applied = (id = 'R1', options) => applyStudyProfile(clone(DEFAULT_CONFIG), id, options).config;

test('catalogue contains versioned R0–R5, public references and no executable/calibration claim', () => {
  assert.deepEqual(STUDY_PROFILES.map(profile => profile.id), ['R0', 'R1', 'R2', 'R3', 'R4', 'R5']);
  for (const profile of STUDY_PROFILES) {
    assert.equal(profile.version, STUDY_VERSION);
    assert.equal(profile.calibrated, false);
    assert.equal(profile.status, 'research_only');
    for (const reference of profile.refs) assert.match(reference.url, /^https:\/\//);
  }
  for (const source of Object.values(STUDY_SOURCES)) assert.match(source.url, /^https:\/\//);
  assert.equal(Object.isFrozen(STUDY_PROFILES[0].study.phy), true);
});

test('new fixture matches R1 radio inputs without claiming an applied study', () => {
  assert.equal(DEFAULT_CONFIG.network.study, undefined);
  const {id,...radio}=DEFAULT_CONFIG.network.radio_profiles[0];
  assert.deepEqual(radio,STUDY_PROFILES.find(p=>p.id==='R1').radio);
  assert.equal(validateConfig(DEFAULT_CONFIG).valid, true);
  assert.equal(validateNetworkStudy(undefined).valid, true);
  assert.deepEqual(studyFieldRows(DEFAULT_CONFIG), []);
  assert.equal(deriveStudyQuantities(DEFAULT_CONFIG), null);
});

test('Apply changes only chosen radio and study, returning detached exact diff', () => {
  const input = clone(DEFAULT_CONFIG);
  input.network.radio_profiles[0].frequency_ghz=5.2;
  input.network.radio_profiles.push({ ...input.network.radio_profiles[0], id: 'keep-other' });
  const result = applyStudyProfile(input, 'R1');
  assert.deepEqual(result.diff, diffConfig(input, result.config));
  assert.ok(result.diff.every(item => item.path.startsWith('network.radio_profiles[0]') || item.path.startsWith('network.study')));
  assert.deepEqual(result.config.network.radio_profiles[1], input.network.radio_profiles[1]);
  assert.deepEqual(result.config.network.link, input.network.link);
  assert.equal(result.config.network.provider, input.network.provider);
  assert.equal(result.config.network.radio_profiles[0].frequency_ghz, 2.412);
  assert.equal(result.config.network.study.radio_profile_id, 'demo-wifi');
  result.config.scenario.seed = 17;
  assert.equal(input.scenario.seed, 42);
  assert.equal(input.network.study, undefined);
  const second = applyStudyProfile(input, 'R0', { radio_profile_id: 'keep-other' });
  assert.equal(second.config.network.radio_profiles[0].frequency_ghz, 5.2);
  assert.equal(second.config.network.radio_profiles[1].frequency_ghz, 2.412);
});

test('R1 changes only reference loss against R0 physical design; workload remains unspecified', () => {
  const r0 = applied('R0');
  const r1 = applyStudyProfile(r0, 'R1').config;
  const physicalChanges = diffConfig(r0, r1).filter(item => !['network.study.profile_id'].includes(item.path));
  assert.deepEqual(physicalChanges.map(item => item.path), ['network.study.propagation.reference_loss_db']);
  assert.equal(r0.network.study.propagation.reference_loss_db, 46.6777);
  assert.equal(r1.network.study.propagation.reference_loss_db, 40.095329);
  const traffic = r1.network.study.traffic;
  assert.equal(traffic.offered_load_mbps, null);
  assert.equal(traffic.payload_bytes, null);
  assert.equal(traffic.application_deadline_ms, null);
  assert.equal(traffic.observation_ttl_ms, 200);
  assert.equal(traffic.ttl_origin, 'first_tx');
  assert.equal(traffic.application_deadline_origin, 'generation_time');
  assert.deepEqual(r1.network.study.queue, { max_packets: 500, max_delay_ms: 500 });
});

test('every default profile and independent variant validates', () => {
  for (const profile of STUDY_PROFILES) {
    const result = validateConfig(applied(profile.id));
    assert.equal(result.valid, true, `${profile.id}: ${JSON.stringify(result.errors)}`);
    for (const variant of profile.variants) {
      const variantResult = validateConfig(applied(profile.id, { variant_id: variant.id }));
      assert.equal(variantResult.valid, true, `${profile.id}/${variant.id}: ${JSON.stringify(variantResult.errors)}`);
    }
  }
});

test('R3 and R4 keep PHY/channel/frequency variants independent and recompute reference quantities', () => {
  const mcs7 = applied('R3', { variant_id: 'mcs7' });
  assert.equal(mcs7.network.study.phy.nominal_rate_mbps, 65);
  assert.equal(mcs7.network.radio_profiles[0].channel_width_mhz, 20);
  const wide = applied('R4', { variant_id: '5755-40' });
  assert.equal(wide.network.radio_profiles[0].frequency_ghz, 5.755);
  assert.equal(wide.network.radio_profiles[0].channel_width_mhz, 40);
  assert.equal(wide.network.study.phy.nominal_rate_mbps, 13.5);
  assert.equal(wide.network.study.propagation.reference_loss_db, 47.64869);
  assert.ok(wide.network.study.research_hints.some(hint => hint.values.channel_number === 151));
  const narrowNoise = deriveStudyQuantities(applied('R4')).integrated_noise_dbm;
  const wideNoise = deriveStudyQuantities(wide).integrated_noise_dbm;
  assert.ok(Math.abs(wideNoise - narrowNoise - 3.0102999566) < 1e-9);
});

test('analytical previews never expose fake goodput, latency or calibration', () => {
  const derived = deriveStudyQuantities(applied('R1'));
  assert.ok(Math.abs(derived.free_space_reference_loss_db - 40.095329) < 1e-6);
  assert.ok(Math.abs(derived.integrated_noise_dbm - (-93.98970004336)) < 1e-8);
  assert.equal(derived.kind, 'analytical_reference_only');
  assert.equal(derived.calibrated, false);
  assert.equal(derived.delivered_goodput_mbps, null);
  assert.equal(derived.measured_latency_ms, null);
});

test('compiler retains research design/version and emits unsupported model diagnostics', () => {
  const value = applied('R5');
  const result = compileConfig(value);
  assert.equal(result.ok, true);
  assert.deepEqual(result.manifest.network.study, value.network.study);
  assert.equal(result.manifest.network.study.profile_version, STUDY_VERSION);
  assert.equal(result.manifest.network.study.execution, 'research_only');
  assert.ok(result.diagnostics.some(item => item.code === 'network_study_not_executable'));
  assert.ok(result.diagnostics.filter(item => item.code === 'unsupported_study_hint').length >= 2);
  assert.ok(result.manifest.source_mapping.some(item => item.source === 'network.study' && item.status === 'unsupported_research_only'));
  assert.equal(result.manifest.network.configured_link.measured_end_to_end_latency_ms, null);
  assert.equal(result.manifest.network.research_preview.calibrated, false);
});

test('field rows carry units/source classes and mark edits as assumptions', () => {
  const value = applied('R1');
  const initialRows = studyFieldRows(value);
  assert.equal(initialRows.find(row => row.path === 'phy.guard_interval_ns').evidence_class, 'research_assumption');
  assert.equal(initialRows.find(row => row.path === 'propagation.reference_loss_db').evidence_class, 'derived_quantity');
  assert.equal(STUDY_FIELD_METADATA['radio.channel_width_mhz'].unit, 'MHz');
  assert.equal(STUDY_FIELD_METADATA['phy.nominal_rate_mbps'].unit, 'Mbps');
  assert.equal(STUDY_FIELD_METADATA['traffic.offered_load_mbps'].unit, 'Mbps/source');
  value.network.study.queue.max_packets = 100;
  const edited = studyFieldRows(value).find(row => row.path === 'queue.max_packets');
  assert.equal(edited.modified_from_profile, true);
  assert.equal(edited.evidence_class, 'research_assumption');
});

test('nominal PHY rate cannot be replaced by offered load or an incompatible width', () => {
  const value = applied('R1');
  value.network.study.phy.nominal_rate_mbps = 6;
  assert.equal(validateConfig(value).valid, false);
  value.network.study.phy.nominal_rate_mbps = 6.5;
  value.network.radio_profiles[0].channel_width_mhz = 40;
  assert.equal(validateConfig(value).valid, false);
  value.network.study.phy.nominal_rate_mbps = 13.5;
  assert.equal(validateConfig(value).valid, true);
});

test('TTL, application deadline, queue and calibration semantics validate separately', () => {
  for (const mutate of [
    s => { s.traffic.ttl_origin = 'generation_time'; },
    s => { s.traffic.application_deadline_origin = 'first_tx'; },
    s => { s.traffic.observation_ttl_ms = 0; },
    s => { s.traffic.application_deadline_ms = -1; },
    s => { s.traffic.payload_bytes = 1.5; },
    s => { s.queue.max_packets = 1.5; },
    s => { s.phy.guard_interval_ns = 400; },
    s => { s.calibrated = true; },
    s => { s.execution = 'running'; },
  ]) {
    const value = applied(); mutate(value.network.study);
    assert.equal(validateConfig(value).valid, false);
  }
  const value = applied();
  value.network.study.traffic.offered_load_mbps = 6.5;
  const result = validateConfig(value);
  assert.equal(result.valid, true);
  assert.ok(result.warnings.some(item => item.path === 'network.study.traffic.offered_load_mbps'));
  assert.ok(result.warnings.some(item => item.path === 'network.study.queue.max_delay_ms'));
});

test('profile versions never silently upgrade and invalid references fail', () => {
  const value = applied();
  value.network.study.profile_version = 'older-revision';
  const result = validateConfig(value);
  assert.equal(result.valid, true);
  assert.ok(result.warnings.some(item => item.path === 'network.study.profile_version'));
  assert.equal(compileConfig(value).manifest.network.study.profile_version, 'older-revision');
  value.network.study.radio_profile_id = 'missing';
  assert.equal(validateConfig(value).valid, false);
  assert.throws(() => applyStudyProfile(DEFAULT_CONFIG, 'R6'), TypeError);
  assert.throws(() => applyStudyProfile(DEFAULT_CONFIG, 'R4', { variant_id: 'unknown' }), TypeError);
  assert.throws(() => applyStudyProfile(DEFAULT_CONFIG, 'R1', { radio_profile_id: 'missing' }), TypeError);
  assert.throws(() => applyStudyProfile({ network: { radio_profiles: [] } }, 'R1'), TypeError);
});

test('malformed nested study inputs do not throw and unsupported hints stay non-executable', () => {
  for (const study of [null, [], {}, { phy: null, propagation: [], traffic: 1, queue: true, research_hints: [null] }]) {
    assert.equal(validateNetworkStudy(study, DEFAULT_CONFIG.network.radio_profiles).valid, false);
  }
  const value = applied('R2');
  value.network.study.research_hints[0].status = 'implemented';
  assert.equal(validateConfig(value).valid, false);
});

test('analytical preview guards huge finite inputs and never emits Infinity or NaN', () => {
  const value = applied();
  value.network.radio_profiles[0].frequency_ghz = Number.MAX_VALUE;
  value.network.radio_profiles[0].channel_width_mhz = Number.MAX_VALUE;
  value.network.study.propagation.reference_distance_m = Number.MAX_VALUE;
  value.network.study.propagation.noise_figure_db = Number.MAX_VALUE;
  const derived = deriveStudyQuantities(value);
  assert.equal(Number.isFinite(derived.free_space_reference_loss_db), true);
  assert.equal(Number.isFinite(derived.integrated_noise_dbm), true);
  value.network.radio_profiles[0].frequency_ghz = Infinity;
  value.network.study.propagation.noise_figure_db = NaN;
  const invalid = deriveStudyQuantities(value);
  assert.equal(invalid.free_space_reference_loss_db, null);
  assert.equal(invalid.integrated_noise_dbm, null);
});

test('legacy frequency caution does not require an applied study',()=>{const result=validateNetworkStudy(undefined,[{frequency_ghz:5.2}]);assert.equal(result.valid,true);assert.equal(result.warnings[0].path,'network.radio_profiles[0].frequency_ghz');assert.match(result.warnings[0].message,/not an outdoor-UAV default/);});
