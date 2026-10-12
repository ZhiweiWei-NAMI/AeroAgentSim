import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { entityLabel, stateValue, displayLabel, readableLabel } from './inspection-format';
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
 expect(displayLabel(undefined, 'traffic.actor.inside_zones', 'zh-CN')).toBe('所在区域');
});

it('localizes state labels from the shipped lowalt and traffic field declarations', () => {
 const ids = new Set<string>();
 const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
 for (const path of ['../../../src/aeroagentsim/packs/lowalt/registry.overlay.yaml', '../../../scenarios/demos/traffic-accident/registry.overlay.yaml']) {
  const sections = source(path).split(/^([a-z_]+):\s*$/m);
  const position = sections.indexOf('fields');
  expect(position).toBeGreaterThan(0);
  const declarations = [...sections[position + 1].matchAll(/^- id: ([^\s]+)$/gm)].map(match => match[1]);
  expect(declarations.length).toBeGreaterThan(0);
  declarations.forEach(id => ids.add(id));
 }
 const snapshot = JSON.parse(source('../../../scenarios/demos/traffic-accident/registry.snapshot.json'));
 snapshot.registry.fields.forEach((field: { id: string }) => ids.add(field.id));
 for (const field of ['binding_id', 'children', 'revision', 'roles', 'state', 'status', 'template_id']) ids.add(`aas.behaviour.${field}`);
 expect(ids.size).toBeGreaterThan(130);
 for (const id of ids) {
  expect(displayLabel(undefined, id, 'zh-CN'), id).toMatch(/[\u3400-\u9fff]/);
  expect(displayLabel(undefined, id, 'en-US'), id).toMatch(/[a-z]/i);
 }
 expect(displayLabel(undefined, 'lowalt.site_position', 'zh-CN')).toBe('站点位置');
 expect(displayLabel(undefined, 'lowalt.site_open', 'zh-CN')).toBe('站点开放');
 expect(displayLabel(undefined, 'lowalt.site_capacity', 'zh-CN')).toBe('站点容量');
});
