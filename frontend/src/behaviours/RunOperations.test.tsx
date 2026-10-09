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
