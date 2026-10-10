import './algorithms.css';
import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Select, Space, Tag } from 'antd';
import { Details } from '../console/Details';
import { TemplateApi, type AlgorithmDescriptor, type AlgorithmSlot } from './api';
import { algorithmSlots, builtinAlgorithms, readableName } from '../studio/guided-model';

/**
 * AlgorithmsPanel — plain-language selector for the four algorithm plug-in slots
 * (allocation, path planning, conflict resolution, charging).
 *
 * Honesty rules:
 * - The option list comes only from `GET /v1/algorithms`. If the endpoint is
 *   unavailable (older deployment, 404, network error) the panel says so and
 *   offers retry; it never invents a built-in catalog that the server did not
 *   advertise.
 * - Selections already authored in the scenario (including unknown ids) are
 *   preserved and shown verbatim in Details > Technical ids.
 */

export interface AlgorithmsPanelProps {
  base:string;
  algorithms:Partial<Record<AlgorithmSlot,string>>;
  onChange:(next:Partial<Record<AlgorithmSlot,string>>)=>void;
}

export function AlgorithmsPanel({base,algorithms,onChange}:AlgorithmsPanelProps) {
  const api=useMemo(()=>new TemplateApi(base),[base]);
  const [catalog,setCatalog]=useState<AlgorithmDescriptor[]>();
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');
    api.algorithms().then(rows=>{if(active){setCatalog(rows);setError('');}}).catch(problem=>{if(active){setCatalog(undefined);setError(String(problem?.message ?? problem));}}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[api,attempt]);
  const bySlot=useMemo(()=>{
    const map:Partial<Record<AlgorithmSlot,AlgorithmDescriptor[]>>={};
    for(const row of catalog??[]){if(algorithmSlots.includes(row.slot))(map[row.slot]??=[]).push(row);}
    return map;
  },[catalog]);
  const describe=(slot:AlgorithmSlot):string|undefined=>bySlot[slot]?.find(row=>row.id===algorithms[slot])?.description;
  const set=(slot:AlgorithmSlot,value?:string)=>{const next={...algorithms};if(value)next[slot]=value;else delete next[slot];onChange(next);};
  const unknown=algorithmSlots.filter(slot=>algorithms[slot]&&!(bySlot[slot]??[]).some(row=>row.id===algorithms[slot]));
  return <div className="guided-algorithms" data-testid="studio-algorithms">
    <Space direction="vertical" style={{width:'100%'}} size="small">
      {error&&<Alert type="warning" showIcon data-testid="studio-algorithms-error" message="Algorithm catalog is unavailable"
        description={<Space direction="vertical" size="small">The simulation service did not answer the algorithm catalog request{error&&<Details title="Catalog request"><p>{error}</p></Details>}. Your saved choices are kept. Retry when the service is available.<Button size="small" onClick={()=>setAttempt(n=>n+1)}>Retry</Button></Space>}/>}
      {catalog&&!catalog.length&&<Alert type="info" showIcon message="No algorithm plug-ins are registered on this service yet."/>}
      {catalog&&catalog.length>0&&unknown.map(slot=><Alert key={slot} type="info" showIcon message={`An authored ${readableName(slot).toLowerCase()} choice is not in the current catalog. Validation will check whether it can run.`}/>)}
      <div className="guided-algorithm-grid">{algorithmSlots.map(slot=>{
        const options=(bySlot[slot]??[]).map(row=>({value:row.id,label:<span>{row.label}<small className="guided-algorithm-description"> — {row.description}</small></span>,title:row.description}));
        const chosen=algorithms[slot];
        return <label key={slot} className="guided-algorithm-slot">
          <span className="guided-label">{{allocation:'Task assignment',path_planning:'Finding a route',conflict_resolution:'Avoiding conflicts',charging:'When to recharge'}[slot]}</span>
          <Select
            data-testid={`algorithm-${slot}`}
            aria-label={readableName(slot)}
            style={{width:'100%'}}
            allowClear
            loading={loading}
            placeholder={catalog?'Choose an algorithm':'Catalog unavailable'}
            value={chosen}
            disabled={!!error||loading}
            options={chosen&&!options.some(row=>row.value===chosen)?[...options,{value:chosen,label:<span>Authored choice</span>,title:'Authored choice'}]:options}
            onChange={value=>set(slot,value)}
          />
          {describe(slot)&&<small className="guided-algorithm-description">{describe(slot)}</small>}
          {chosen&&!describe(slot)&&!error&&<small className="guided-algorithm-description">This saved choice is not described in the catalog.</small>}

        </label>;
      })}</div>
      <Details title="Technical ids" buttonLabel="Technical ids">
        <pre>{JSON.stringify(algorithms,null,2)}</pre>
        {algorithmSlots.map(slot=><p key={slot} className="guided-muted">Built-in ids for {readableName(slot)}: {builtinAlgorithms[slot].join(', ')}</p>)}
      </Details>
    </Space>
  </div>;
}

function builtinsNote(slot:AlgorithmSlot) {
  const expected=builtinAlgorithms[slot];
  return <Tag className="guided-algorithm-builtin">Built-ins: {expected.join(' · ')}</Tag>;
}

export default AlgorithmsPanel;
