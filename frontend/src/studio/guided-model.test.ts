import {test,expect} from 'vitest';
import {issueStep,validationIssues} from './guided-model';
test('compiler paths link to the relevant guided step and retain the authored diagnostic',()=>{
 const issues=validationIssues({valid:false,errors:['compiler failed'],issues:[{source:'traffic package',path:'$.predicates.incident.ast',message:'Unknown field'},{source:'scenario',path:'$.engines.decisions.config.provider',message:'Model required'}]});
 expect(issues.map(issueStep)).toEqual(['predicates','agents']);
 expect(issues[0].message).toBe('Unknown field');
 expect(validationIssues({valid:false,errors:['Invalid registry']})).toEqual([{path:'$',message:'Invalid registry'}]);
});
