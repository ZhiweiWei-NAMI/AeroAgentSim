import {test,expect,vi} from 'vitest';
import {issueStep,registryLabel,validationIssues} from './guided-model';
vi.mock('../i18n/messages', async () => ({ default: { 'zh-CN': (await import('../i18n/studio-messages')).default } }));
test('compiler paths link to the relevant guided step and retain the authored diagnostic',()=>{
 const issues=validationIssues({valid:false,errors:['compiler failed'],issues:[{source:'traffic package',path:'$.predicates.incident.ast',message:'Unknown field'},{source:'scenario',path:'$.engines.decisions.config.provider',message:'Model required'}]});
 expect(issues.map(issueStep)).toEqual(['predicates','agents']);
 expect(issues[0].message).toBe('Unknown field');
 expect(validationIssues({valid:false,errors:['Invalid registry']})).toEqual([{path:'$',message:'Invalid registry'}]);
});
test('known registry vocabulary is bilingual while authored IDs remain recognizable',()=>{
 expect(registryLabel('oo:UAV')).toBe('UAV');
 expect(registryLabel('lowalt:UAV','zh-CN')).toBe('无人机');
 expect(registryLabel('lowalt:Vertiport','zh-CN')).toBe('起降场');
 expect(registryLabel('lowalt:ChargingPad','zh-CN')).toBe('充电桩');
 expect(registryLabel('NoFlyZone','zh-CN')).toBe('禁飞区');
 expect(registryLabel('custom:LabSensor','zh-CN')).toBe('Lab Sensor');
 expect(validationIssues({valid:false} as any)).toEqual([]);
});
