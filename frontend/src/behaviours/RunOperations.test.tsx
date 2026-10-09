import { expect, it } from 'vitest';
import { ingressBody, injectionPointsFromScenario } from './RunOperations';
import { stringifyLossless } from '../feeds/lossless-json';
it('sends real large timestamps and source stamps without rounding or invented defaults',()=>{
 const fields={schema:'registered',target:'behaviour',stream_id:'operator',at_ns:'9007199254740993',clock_id:'operator-clock',mapping_id:'operator-map',numerator:'9007199254740993',denominator:'1'};
 expect(stringifyLossless(ingressBody(fields,{accident:true}))).toContain('"at_ns":9007199254740993');
 expect(()=>ingressBody({...fields,clock_id:''},{})).toThrow('explicitly required');
 expect(()=>ingressBody({...fields,denominator:'0'},{})).toThrow('positive');
});

it('reads actual resolved package wrappers and rejects ambiguous operator injection points',()=>{
 const point={id:'accident',stream_id:'operator',command:'aas.runtime.inject_event',target:'behaviour',emits:'accident'};
 expect(injectionPointsFromScenario({behaviours:[{document:{injection_points:[point]},digest:'actual'}]})).toEqual([point]);
 expect(injectionPointsFromScenario({engines:{rules:{plugin:'behaviour',config:{packages:[{document:{injection_points:[point]}}]}}}})).toEqual([point]);
 expect(()=>injectionPointsFromScenario({behaviours:[{injection_points:[point]},{injection_points:[point]}]})).toThrow('ambiguous');
});

import { recordedAccidentPayload, operatorTiming } from './RunOperations';
import { TemporalFeedStore } from '../feeds/temporal-store';
it('waits for recorded incident identity and keeps kernel run, epoch and generation exact',()=>{
 const store=new TemporalFeedStore({contract:'aeroagentsim.viewer-feed/v1',runId:'service-run',kernelRunId:'kernel-run',epoch:'epoch-7',registryDigest:'actual',start:{ns:'0',microstep:0},types:[{typeId:'aas:TrafficIncident',displayName:'Incident',ancestors:[]}],fields:[],presentation:[]});
 expect(recordedAccidentPayload(store)).toBeUndefined();
 store.ingest({commitIndex:1,at:{ns:'0',microstep:0},created:[{id:'incident.actual',generation:'9007199254740993',typeId:'aas:TrafficIncident'}],removed:[],facts:[],retracted:[],edges:[],messages:[],receipts:[]});store.seek('0',1);
 expect(stringifyLossless(recordedAccidentPayload(store))).toContain('"generation":9007199254740993');
 expect(recordedAccidentPayload(store)?.incident).toEqual({$ref:{run_id:'kernel-run',epoch:'epoch-7',id:'incident.actual',generation:{$integer:'9007199254740993'},type_id:'aas:TrafficIncident'}});
});

it('takes operator progress from its actual ingress declaration, including exact large clocks',()=>{
 expect(operatorTiming({run:{advance_ns:1000000000},ingress_streams:[{id:'operator',initial_watermark_ns:{$integer:'9007199254740993'}}]})).toEqual({step:1000000000n,initial:9007199254740993n});
 expect(()=>operatorTiming({run:{advance_ns:1000000000},ingress_streams:[{id:'operator'}]})).toThrow('declared exact');
});
