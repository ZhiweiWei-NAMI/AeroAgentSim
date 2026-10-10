import './algorithms.css';
import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Select, Space, Tag } from 'antd';
import { Details } from '../console/Details';
import { TemplateApi, type AlgorithmDescriptor, type AlgorithmSlot } from './api';
import { algorithmSlots, readableName } from '../studio/guided-model';

/**
 * AlgorithmsPanel — plain-language selector for the four algorithm plug-in slots
 * (allocation, path planning, conflict resolution, charging).
 *
 * Honesty rules:
 * - The option list comes only from `GET /v1/algorithms`. If the endpoint is
 *   unavailable (older deployment, 404, network error) the panel says so and
 *   offers retry; it never invents a built-in catalog that the server did not
 *   advertise.
 * - Applicability comes from the caller, which inspects the real scenario
 *   engines: a scenario without the low-altitude task pack never shows active
 *   dropdowns and never fetches the catalog ("Not used by this scenario").
 * - Effective values: a slot the scenario does not override shows the
 *   documented low-altitude default (from
 *   src/aeroagentsim/packs/lowalt/template.py `DEFAULT_ALGORITHMS`) with a
 *   "(default)" marker. No dropdown is ever left on an empty placeholder.
 * - Resetting a slot removes the authored override (the scenario keeps no key
 *   for it) so the display falls back to the documented default; the default
 *   id itself is never written into the scenario by this panel.
 * - Selections already authored in the scenario (including unknown ids) are
 *   preserved and shown verbatim in Details > Technical ids.
 */

// Documented backend defaults, mirrored verbatim from
// src/aeroagentsim/packs/lowalt/template.py:DEFAULT_ALGORITHMS. These are only
// shown as the effective value for scenarios that use the low-altitude task
// pack; nothing is invented beyond that pack.
const documentedDefaults:Partial<Record<AlgorithmSlot,string>>={
  allocation:'nearest',
  path_planning:'corridor_astar',
  conflict_resolution:'altitude_layers',
  charging:'threshold_return',
};

const slotLabels:Record<AlgorithmSlot,string>={
  allocation:'Task assignment',
  path_planning:'Finding a route',
  conflict_resolution:'Avoiding conflicts',
  charging:'When to recharge',
};

export interface AlgorithmsPanelProps {
  base:string;
  algorithms:Partial<Record<AlgorithmSlot,string>>;
  onChange:(next:Partial<Record<AlgorithmSlot,string>>)=>void;
  /** Whether the scenario's real engines use the low-altitude task pack. When omitted the panel keeps the previous always-applicable behaviour. */
  applicable?:boolean;
}

export function AlgorithmsPanel({base,algorithms,onChange,applicable=true}:AlgorithmsPanelProps) {
  const api=useMemo(()=>new TemplateApi(base),[base]);
  const [catalog,setCatalog]=useState<AlgorithmDescriptor[]>();
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    if(!applicable)return;
    let active=true;
    setLoading(true);setError('');
    api.algorithms().then(rows=>{if(active){setCatalog(rows);setError('');}}).catch(problem=>{if(active){setCatalog(undefined);setError(String(problem?.message ?? problem));}}).finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[api,attempt,applicable]);
  const bySlot=useMemo(()=>{
    const map:Partial<Record<AlgorithmSlot,AlgorithmDescriptor[]>>={};
    for(const row of catalog??[]){if(algorithmSlots.includes(row.slot))(map[row.slot]??=[]).push(row);}
    return map;
  },[catalog]);
  const describe=(slot:AlgorithmSlot,id:string|undefined):string|undefined=>id?(bySlot[slot]??[]).find(row=>row.id===id)?.description:undefined;
  const set=(slot:AlgorithmSlot,value?:string)=>{const next={...algorithms};if(value)next[slot]=value;else delete next[slot];onChange(next);};
  const unknown=algorithmSlots.filter(slot=>algorithms[slot]&&!(bySlot[slot]??[]).some(row=>row.id===algorithms[slot]));
  if(!applicable)return <div className="guided-algorithms" data-testid="studio-algorithms">
    <Alert type="info" showIcon data-testid="studio-algorithms-not-applicable"
      message="These algorithm choices are not used by this scenario"
      description="This scenario follows its own authored rules. Choose a delivery or other task-based fleet scenario to compare task assignment, routing, conflict avoidance and charging choices."/>
  </div>;
  return <div className="guided-algorithms" data-testid="studio-algorithms">
    <Space direction="vertical" style={{width:'100%'}} size="small">
      {error&&<Alert type="warning" showIcon data-testid="studio-algorithms-error" message="Algorithm catalog is unavailable"
        description={<Space direction="vertical" size="small">The simulation service did not answer the algorithm catalog request{error&&<Details title="Catalog request"><p>{error}</p></Details>}. Your saved choices are kept. Retry when the service is available.<Button size="small" onClick={()=>setAttempt(n=>n+1)}>Retry</Button></Space>}/>}
      {catalog&&!catalog.length&&<Alert type="info" showIcon message="No algorithm plug-ins are registered on this service yet."/>}
      {catalog&&catalog.length>0&&unknown.map(slot=><Alert key={slot} type="info" showIcon message={`An authored ${readableName(slot).toLowerCase()} choice is not in the current catalog. Validation will check whether it can run.`}/>)}
      <div className="guided-algorithm-grid">{algorithmSlots.map(slot=>{
        const override=algorithms[slot];
        const effective=override??documentedDefaults[slot];
        const isDefault=!override;
        const chosenLabel=effective
          ?(bySlot[slot]??[]).find(row=>row.id===effective)?.label??(isDefault?'Documented default':'Saved choice')
          :undefined;
        const options=(bySlot[slot]??[]).map(row=>({value:row.id,label:`${row.label}${isDefault&&row.id===effective?' (default)':''}`,title:row.description}));
        return <label key={slot} className="guided-algorithm-slot">
          <span className="guided-label">{slotLabels[slot]}{isDefault&&effective&&<Tag className="guided-algorithm-default-tag">(default)</Tag>}</span>
          <Select
            data-testid={`algorithm-${slot}`}
            aria-label={readableName(slot)}
            style={{width:'100%'}}
            loading={loading}
            placeholder={catalog?'Choose an algorithm':'Catalog unavailable'}
            labelInValue
            value={effective?{value:effective,label:chosenLabel?`${chosenLabel}${isDefault?' (default)':''}`:effective}:undefined}
            disabled={!!error||loading}
            options={effective&&!options.some(row=>row.value===effective)?[...options,{value:effective,label:`${chosenLabel}${isDefault?' (default)':''}`,title:isDefault?'Documented default for this slot':'Authored choice'}]:options}
            onChange={value=>set(slot,value.value)}
          />
          {describe(slot,effective)&&<small className="guided-algorithm-description">{describe(slot,effective)}{isDefault&&' This documented default applies until you pick another algorithm.'}</small>}
          {override&&!describe(slot,override)&&!error&&<small className="guided-algorithm-description">This saved choice is not described in the catalog.</small>}
          {override&&<Button size="small" type="text" data-testid={`algorithm-reset-${slot}`} className="guided-algorithm-reset" onClick={()=>set(slot,undefined)}>Reset to default</Button>}
        </label>;
      })}</div>
      <Details title="Technical ids" buttonLabel="Technical ids">
        <pre>{JSON.stringify(algorithms,null,2)}</pre>
        <p className="guided-muted">Slots without a saved choice run the documented low-altitude defaults: {algorithmSlots.map(slot=>`${slot}=${documentedDefaults[slot]}`).join(', ')}.</p>
      </Details>
    </Space>
  </div>;
}

export default AlgorithmsPanel;
