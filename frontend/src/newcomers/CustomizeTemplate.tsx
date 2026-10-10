import {useEffect,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {InputNumber,Select,Slider,Switch} from 'antd';
import {Details} from '../console/Details';
import {TemplateApi,templateRunUrl,type AlgorithmDescriptor,type AlgorithmSlot,type KnobValue,type ScenarioTemplate,type TemplateKnob} from './api';
import './gallery.css';
export function knobAlgorithmSlot(knob:TemplateKnob):AlgorithmSlot|undefined{
 const slot=knob.pointer?.match(/^\/algorithms\/([^/]+)$/)?.[1];
 return slot&&['allocation','path_planning','conflict_resolution','charging'].includes(slot)?slot as AlgorithmSlot:undefined;
}
export function TemplateKnobControl({knob,value,onChange,algorithms}:{knob:TemplateKnob;value:KnobValue;onChange:(value:KnobValue)=>void;algorithms?:AlgorithmDescriptor[]}){
 const id=`knob-${knob.id}`;
 const names=new Map(algorithms?.map(row=>[row.id,row]));
 return <div className="template-knob-row"><label id={`${id}-label`}>{knob.label}</label>
  {knob.type==='choice'?<Select aria-labelledby={`${id}-label`} data-testid={id} value={String(knob.options!.indexOf(value))} options={knob.options!.map((option,index)=>({value:String(index),label:typeof option==='string'?names.get(option)?.label??({scripted:'Recorded decisions',live:'Live model'} as Record<string,string>)[option]??option.replace(/_/g,' '):String(option),title:typeof option==='string'?names.get(option)?.description:undefined}))} onChange={index=>onChange(knob.options![Number(index)])}/>
   :knob.type==='boolean'?<Switch aria-labelledby={`${id}-label`} data-testid={id} checked={value as boolean} onChange={onChange}/>
   :<div className="template-number-control">{knob.min!==undefined&&knob.max!==undefined&&<Slider aria-labelledby={`${id}-label`} data-testid={`${id}-slider`} min={knob.min} max={knob.max} step={knob.type==='integer'?1:0.1} value={value as number} onChange={onChange}/>}
     <InputNumber aria-labelledby={`${id}-label`} data-testid={id} min={knob.min} max={knob.max} precision={knob.type==='integer'?0:undefined} step={knob.type==='integer'?1:0.1} value={value as number} onChange={next=>{if(next!==null)onChange(next);}}/>
    </div>}
  {(typeof value==='string'?names.get(value)?.description:undefined)||knob.description?<span className="template-knob-hint">{(typeof value==='string'?names.get(value)?.description:undefined)??knob.description}</span>:null}
 </div>;
}
export default function CustomizeTemplate({template,api,apiBase}:{template:ScenarioTemplate;api:TemplateApi;apiBase:string}){
 const navigate=useNavigate(),[knobs,setKnobs]=useState<Record<string,KnobValue>>(()=>Object.fromEntries(template.knobs.map(knob=>[knob.id,knob.default]))),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [algorithms,setAlgorithms]=useState<AlgorithmDescriptor[]>(),[catalogError,setCatalogError]=useState('');
 useEffect(()=>{if(!template.knobs.some(knob=>knobAlgorithmSlot(knob)))return;const abort=new AbortController();void api.algorithms(abort.signal).then(setAlgorithms).catch(error=>{if(!abort.signal.aborted)setCatalogError(String(error));});return()=>abort.abort();},[api,template.id]);
 const start=async(studio=false)=>{setBusy(true);setError('');try{if(studio){const receipt=await api.workspace(template.id,knobs);navigate(`/studio?workspace=${encodeURIComponent(receipt.workspace_id)}&api=${encodeURIComponent(apiBase)}`);}else navigate(templateRunUrl(await api.start(template.id,knobs),apiBase,template.id));}catch(problem){setError(String(problem));setBusy(false);}};
 return <div className="template-customize" data-testid={`customize-panel-${template.id}`}>
  <p className="template-card-summary">{template.summary}</p>
  <section aria-label="Scenario settings">{template.knobs.map(knob=><TemplateKnobControl key={knob.id} knob={knob} value={knobs[knob.id]} algorithms={algorithms?.filter(row=>row.slot===knobAlgorithmSlot(knob))} onChange={value=>setKnobs(current=>({...current,[knob.id]:value}))}/>)}{!template.knobs.length&&<p>This scenario comes with fixed settings. Open it in Studio to edit its rules.</p>}{catalogError&&<p>Algorithm descriptions could not be loaded. The template's declared choices are shown.<Details title="Algorithm catalog"><p>{catalogError}</p></Details></p>}</section>
  {!!template.try.length&&<section className="template-hints"><h3>Things to try</h3><ul>{template.try.map(hint=><li key={hint}>{hint}</li>)}</ul></section>}
  {!!template.observe.length&&<section className="template-hints"><h3>What to watch</h3><ul>{template.observe.map(hint=><li key={hint}>{hint}</li>)}</ul></section>}
  <div className="template-card-actions"><button className="console-btn console-btn-primary" data-testid={`custom-run-${template.id}`} disabled={busy} onClick={()=>void start()}>{busy?'Starting…':'Run with these settings'}</button><button className="console-btn" disabled={busy} onClick={()=>void start(true)}>Open in Studio</button></div>
  {error&&<div role="alert" className="template-error-card"><strong>Could not start with these settings.</strong><p>Review the settings or try again.</p><Details title="Request error"><p>{error}</p></Details></div>}
 </div>;
}
