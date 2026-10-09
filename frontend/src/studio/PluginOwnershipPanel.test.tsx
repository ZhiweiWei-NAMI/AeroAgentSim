import { expect, it } from 'vitest';
import { profileIssues } from './PluginOwnershipPanel';
it('checks actual advertised writer and command capability and dependencies', () => {
  expect(profileIssues({id:'motion',available:true,description:'',capability_descriptor:{fields:['pose'],commands:['move'],requires_plugins:['weather']}},['pose','phase'],['cancel'],['motion'])).toEqual(['Plugin does not advertise writer capability for phase','Plugin does not advertise cancel','Required configured plugin: weather']);
  expect(profileIssues({id:'motion',available:true,description:''},['pose'],['move'],[])).toEqual(['Capability descriptor unavailable; compatibility requires server validation']);
});
