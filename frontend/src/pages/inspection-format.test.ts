import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { entityLabel, stateValue, displayLabel, displayUnit, readableLabel } from './inspection-format';
import type { FieldInfo } from '../contracts/viewer-feed';
import { displayTime } from './display-time';
it('formats display precision and time without changing exact recorded values', () => {
  expect(displayLabel('he.aircraft.position_enu_m','he.aircraft.position_enu_m')).toBe('Position (east / north / up)');
  expect(displayLabel('计算节点 / Compute node','oo:ComputeNode')).toBe('Compute node');
  expect(stateValue(84.73465076268933)).toBe('84.73');
  expect(stateValue([{$number:'84.73465076268933'},0])).toBe('84.73 · 0');
  expect(stateValue(undefined)).toBeUndefined();
  expect(stateValue(['001','2'])).toBeUndefined();
  expect(displayTime('49066666667')).toBe('00:49.1');
  expect(displayTime('59999999999')).toBe('01:00.0');
  const entity={key:{id:'uav.bravo',generation:0},typeId:'aas:UAV',fields:new Map()};
  expect(entityLabel(entity)).toBe('UAV Bravo');
  entity.fields.set('label',{value:'Bravo · survey aircraft'});
  expect(entityLabel(entity)).toBe('Bravo · survey aircraft');
});

it('localizes object groups and prominent state fields without changing identifiers', () => {
 expect(readableLabel('lowalt:ChargingPad', 'en-US')).toBe('Charging Pad');
 expect(readableLabel('lowalt:ChargingPad', 'zh-CN')).toBe('充电坪');
 expect(readableLabel('aas:BehaviourInstance', 'zh-CN')).toBe('行为实例');
 expect(displayLabel(undefined, 'lowalt.weather_held', 'zh-CN')).toBe('因天气暂停');
 for (const [field, label] of [['returning_to_charge', '返航充电'], ['motion_energy_j', '运动能量'], ['energy_used_j', '已用能量'], ['distance_m', '行进距离'], ['capacity_j', '能量容量'], ['payload_kg', '当前载荷'], ['payload_capacity_kg', '载荷容量'], ['vehicle_class', '载具类别'], ['wind_mps', '风速'], ['charging', '充电中']]) {
  expect(displayLabel(undefined, `lowalt.${field}`, 'zh-CN')).toBe(label);
 }
 expect(displayLabel(undefined, 'lowalt.charging', 'en-US')).toBe('Charging');
 expect(displayLabel('Charging', 'lowalt.charging', 'zh-CN')).toBe('充电中');
 expect(displayLabel(undefined, 'lowalt.charging_waiting', 'zh-CN')).toBe('等待充电');
 expect(displayLabel(undefined, 'lowalt.charging_pad', 'zh-CN')).toBe('充电坪');
 expect(displayLabel(undefined, 'traffic.actor.inside_zones', 'zh-CN')).toBe('所在区域');
});

// Read shipped declarations, including templates' additional snapshot fields.
// The overlays use block enum lists and scalar metadata units. Schema aliases
// do not declare additional enums or metadata units.
function shippedFields() {
 const fields: Array<{ id: string; unit?: string; enums: string[]; labels?: string[] }> = [];
 const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
 for (const path of ['../../../src/aeroagentsim/packs/lowalt/registry.overlay.yaml', '../../../scenarios/demos/traffic-accident/registry.overlay.yaml']) {
  const sections = source(path).split(/^([a-z_]+):\s*$/m), position = sections.indexOf('fields');
  if (position < 0) throw Error(`Missing fields in ${path}`);
  for (const block of sections[position + 1].split(/(?=^- id: )/m)) {
   const id = block.match(/^- id: ([^\s]+)$/m)?.[1];
   if (!id) continue;
   const unit = block.match(/^    unit: ['"]?([^'"\n]+)['"]?$/m)?.[1];
   const enums = [...(block.match(/^    enum:\n((?:    - [^\n]+\n)+)/m)?.[1] ?? '').matchAll(/^    - (.+)$/gm)].map(match => match[1]);
   fields.push({ id, unit, enums });
  }
 }
 const snapshots = import.meta.glob([
  '../../../src/aeroagentsim/packs/lowalt/registry.snapshot.json',
  '../../../scenarios/templates/*/registry.snapshot.json',
  '../../../scenarios/demos/traffic-accident/registry.snapshot.json',
 ], { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
 for (const text of Object.values(snapshots)) {
  const snapshot = JSON.parse(text);
  for (const field of snapshot.registry.fields) {
   const unit = field.metadata?.unit, raw = field.metadata?.raw;
   fields.push({ id: field.id, unit: typeof unit === 'string' ? unit : unit?.symbol, enums: field.schema.enum ?? [],
    labels: [raw?.displayName, raw?.displayNameEn, raw?.sourceDisplayName].filter((label): label is string => typeof label === 'string') });
  }
 }
 return fields;
}

it('localizes every shipped lowalt/traffic field ID and authored display label in Chinese', () => {
 const fields = shippedFields();
 for (const field of ['binding_id', 'children', 'revision', 'roles', 'state', 'status', 'template_id']) fields.push({ id: `aas.behaviour.${field}`, enums: [] });
 expect(new Set(fields.map(field => field.id)).size).toBeGreaterThan(130);
 for (const { id, labels } of fields) {
  const english = displayLabel(undefined, id, 'en-US');
  expect(english, id).toMatch(/[a-z]/i);
  // ID-only headers and headers with friendly English labels take different
  // branches in displayLabel. Both must retain translated field names.
  for (const label of [undefined, id, english, ...(labels ?? [])]) expect(displayLabel(label, id, 'zh-CN'), `${id}: ${label}`).toMatch(/[\u3400-\u9fff]/);
 }
 expect(displayLabel(undefined, 'lowalt.site_position', 'zh-CN')).toBe('站点位置');
 expect(displayLabel(undefined, 'lowalt.site_open', 'zh-CN')).toBe('站点开放');
 expect(displayLabel(undefined, 'lowalt.site_capacity', 'zh-CN')).toBe('站点容量');
});

it('localizes every shipped field enum without rewriting recorded strings or arbitrary IDs', () => {
 const enums = shippedFields().flatMap(field => field.enums.map(value => ({ id: field.id, value })));
 expect(enums.length).toBeGreaterThan(25);
 for (const { id, value } of enums) {
  expect(stateValue(value, 'zh-CN'), `${id}: ${value}`).toMatch(/[\u3400-\u9fff]/);
  expect(stateValue(value, 'en-US'), id).toBe(value);
 }
 expect(stateValue('restaurant-east', 'zh-CN')).toBe('restaurant-east');
 expect(stateValue('My research label', 'zh-CN')).toBe('My research label');
});

it('localizes all shipped dimensional units while retaining exact English symbols and unitless fields', () => {
 const fields = shippedFields();
 expect(new Set(fields.map(field => field.unit))).toContain('ns');
 for (const { id, unit } of fields) {
  const field: FieldInfo = { fieldId: id, displayName: id, valueType: 'number', unit };
  expect(displayUnit(field, 'en-US'), id).toBe(unit === '1' ? undefined : unit);
  const chinese = displayUnit(field, 'zh-CN');
  if (unit && unit !== '1' && unit !== '%') expect(chinese, `${id}: ${unit}`).toMatch(/[\u3400-\u9fff]/);
  else expect(chinese, id).toBe(unit === '1' ? undefined : unit);
 }
 const schemaUnit: FieldInfo = { fieldId: 'unit', displayName: 'Unit', valueType: 'number', schema: { unit: { symbol: 'm/s' } } };
 expect(displayUnit(schemaUnit, 'zh-CN')).toBe('米/秒');
 expect(displayUnit(schemaUnit)).toBe('m/s');
 expect(displayUnit({ ...schemaUnit, schema: { unit: '1' } }, 'zh-CN')).toBeUndefined();
 expect(displayUnit({ ...schemaUnit, unit: 'custom/unit' }, 'zh-CN')).toBe('custom/unit');
});

it('spells out abbreviated field names in English and keeps the Chinese dictionary', () => {
  expect(readableLabel('lowalt.soc')).toBe('State of charge');
  expect(readableLabel('lowalt.soc', 'zh-CN')).toBe('电量比例');
  expect(readableLabel('traffic.coordinator_candidate', 'zh-CN')).toBe('协调候选对象');
});
