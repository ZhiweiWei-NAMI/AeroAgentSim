import { expect, it } from 'vitest';
import { entityLabel, stateValue, displayLabel } from './inspection-format';
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
