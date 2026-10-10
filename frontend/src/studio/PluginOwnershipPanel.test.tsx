import { expect, it, vi } from 'vitest';
import { profileDiagnostics, profileIssues } from './PluginOwnershipPanel';
it('checks actual advertised writer and command capability and dependencies', () => {
  expect(profileIssues({id:'motion',available:true,description:'',capability_descriptor:{fields:['pose'],commands:['move'],requires_plugins:['weather']}},['pose','phase'],['cancel'],['motion'])).toEqual(['Plugin does not advertise writer capability for phase','Plugin does not advertise cancel','Required configured plugin: weather']);
  expect(profileIssues({id:'motion',available:true,description:''},['pose'],['move'],[])).toEqual(['Capability descriptor unavailable; compatibility requires server validation']);
});

it('keeps blocking severity independent of the translated wording', () => {
  const t = vi.fn(() => '无法执行：能力未声明');
  expect(profileDiagnostics({ id: 'motion', available: true, description: '' }, ['pose'], [], [], t)).toEqual([{ message: '无法执行：能力未声明', blocking: false }]);
  expect(profileDiagnostics({ id: 'motion', available: true, description: '', capability_descriptor: { fields: [] } }, ['pose'], [], [], t)).toEqual([{ message: '无法执行：能力未声明', blocking: true }]);
  expect(t).toHaveBeenLastCalledWith('Plugin does not advertise writer capability for {field}', { field: 'pose' });
  expect(profileDiagnostics(undefined, [], [], [], t)[0].blocking).toBe(true);
});

it('checks required plugins against the post-replacement plugin list', () => {
  const replacement = { id: 'new', available: true, description: '', capability_descriptor: { requires_plugins: ['old'] } };
  expect(profileDiagnostics(replacement, [], [], ['new'])).toEqual([{ message: 'Required configured plugin: old', blocking: true }]);
  expect(profileDiagnostics({ ...replacement, capability_descriptor: { requires_plugins: ['new'] } }, [], [], ['new'])).toEqual([]);
});
