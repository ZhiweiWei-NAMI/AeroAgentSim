import {expect,it} from 'vitest';
import {businessProjection,resultsAt} from './projection';
import {emptyCommit} from '../behaviours/extension-fixture';
import {validateCommit} from '../feeds/http';
it('replay events and results obey the exact committed cut, retaining missing values',()=>{
 const first={...emptyCommit(1),at:{ns:'10',microstep:0},events:[{at_ns:'10',category:'logistics' as const,severity:'info' as const,text:'A drone picked up a parcel.',entities:['drone']}],kpis:{tasks_completed:{value:0,label:'Delivered',unit:'tasks'}}};
 const second={...emptyCommit(2),at:{ns:'10',microstep:1},events:[{at_ns:'10',category:'logistics' as const,severity:'notice' as const,text:'The parcel was delivered.',entities:['drone']}],kpis:{tasks_completed:{value:1,label:'Delivered',unit:'tasks'},mean_task_latency_s:{value:null,label:'Delivery time',unit:'s'}}};
 validateCommit(first);validateCommit(second);
 expect(businessProjection([first,second],1,'10').events.map(row=>row.text)).toEqual(['A drone picked up a parcel.']);
 expect(businessProjection([first,second],2,'10').values.mean_task_latency_s.value).toBeNull();
 expect(resultsAt({run_id:'run',final:second.kpis,series:[{commitIndex:1,at_ns:'10',kpis:first.kpis},{commitIndex:2,at_ns:'10',kpis:second.kpis}]},'10',false,1).values.tasks_completed.value).toBe(0);
 expect(()=>validateCommit({...first,kpis:{tasks_completed:{value:undefined,unit:'tasks',label:'Delivered'}}})).toThrow('Invalid result');
});
