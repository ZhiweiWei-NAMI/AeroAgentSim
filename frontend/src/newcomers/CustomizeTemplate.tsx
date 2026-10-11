import {useEffect,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {InputNumber,Select,Slider,Switch} from 'antd';
import {Details} from '../console/Details';
import {useI18n} from '../i18n/I18nProvider';
import {localized} from '../i18n/translate';
import {TemplateApi,templateRunUrl,type AlgorithmDescriptor,type AlgorithmSlot,type KnobValue,type ScenarioTemplate,type TemplateKnob} from './api';
import {knobOptionLabel} from './presentation';
import './gallery.css';

export function knobAlgorithmSlot(knob:TemplateKnob):AlgorithmSlot|undefined{
 const slot=knob.pointer?.match(/^\/algorithms\/([^/]+)$/)?.[1];
 return slot&&['allocation','path_planning','conflict_resolution','charging'].includes(slot)?slot as AlgorithmSlot:undefined;
}

export function TemplateKnobControl({knob,value,onChange,algorithms,onValidityChange}:{knob:TemplateKnob;value:KnobValue;onChange:(value:KnobValue)=>void;algorithms?:AlgorithmDescriptor[];onValidityChange?:(valid:boolean)=>void}){
 const {t,locale}=useI18n();
 const [invalid,setInvalid]=useState(false);
 useEffect(()=>()=>onValidityChange?.(true),[knob.id]);
 const report=(valid:boolean)=>{setInvalid(!valid);onValidityChange?.(valid);};
 const numberValid=(next:number|null)=>next!==null&&Number.isFinite(next)&&(knob.type!=='integer'||Number.isSafeInteger(next))&&(knob.min===undefined||next>=knob.min)&&(knob.max===undefined||next<=knob.max);
 const id=`knob-${knob.id}`;
 const names=new Map(algorithms?.map(row=>[row.id,row]));
 const selectedAlgorithm=typeof value==='string'?names.get(value):undefined;
 const hint=localized(selectedAlgorithm,'description',locale)??localized(knob,'description',locale);
 return <div className="template-knob-row"><label id={`${id}-label`}>{localized(knob,'label',locale)}</label>
  {knob.type==='choice'?<Select aria-labelledby={`${id}-label`} data-testid={id} value={String(knob.options!.indexOf(value))} options={knob.options!.map((option,index)=>({value:String(index),label:knobOptionLabel(knob,option,locale,algorithms),title:typeof option==='string'?localized(names.get(option),'description',locale):undefined}))} onChange={index=>onChange(knob.options![Number(index)])}/>
   :knob.type==='boolean'?<Switch aria-labelledby={`${id}-label`} data-testid={id} checked={value as boolean} checkedChildren={t('yes')} unCheckedChildren={t('no')} onChange={onChange}/>
   :<div className="template-number-control">{knob.min!==undefined&&knob.max!==undefined&&<Slider ariaLabelledByForHandle={`${id}-label`} data-testid={`${id}-slider`} min={knob.min} max={knob.max} step={knob.type==='integer'?1:0.1} value={value as number} onChange={next=>{report(true);onChange(next);}}/>}
     <InputNumber aria-labelledby={`${id}-label`} aria-invalid={invalid} data-testid={id} min={knob.min} max={knob.max} precision={knob.type==='integer'?0:undefined} step={knob.type==='integer'?1:0.1} value={value as number} onInput={raw=>report(raw.trim()!==''&&numberValid(Number(raw)))} onChange={next=>{const valid=numberValid(next);report(valid);if(valid)onChange(next!);}}/>
    </div>}
  {invalid&&<span className="wb-field-error" role="alert">{locale==='zh-CN'?'请输入范围内的有效数值。':'Enter a valid number within the bounds.'}</span>}
  {hint&&<span className="template-knob-hint">{hint}</span>}
 </div>;
}

export default function CustomizeTemplate({template,api,apiBase}:{template:ScenarioTemplate;api:TemplateApi;apiBase:string}){
 const {t,locale}=useI18n();
 const navigate=useNavigate(),[knobs,setKnobs]=useState<Record<string,KnobValue>>(()=>Object.fromEntries(template.knobs.map(knob=>[knob.id,knob.default]))),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [action,setAction]=useState<'run'|'studio'>('run');
 const [algorithms,setAlgorithms]=useState<AlgorithmDescriptor[]>(),[catalogError,setCatalogError]=useState('');
 useEffect(()=>{if(!template.knobs.some(knob=>knobAlgorithmSlot(knob)))return;const abort=new AbortController();void api.algorithms(abort.signal).then(setAlgorithms).catch(error=>{if(!abort.signal.aborted)setCatalogError(String(error));});return()=>abort.abort();},[api,template.id]);
 const start=async(studio=false)=>{setAction(studio?'studio':'run');setBusy(true);setError('');try{if(studio){const receipt=await api.workspace(template.id,knobs);navigate(`/studio?workspace=${encodeURIComponent(receipt.workspace_id)}&api=${encodeURIComponent(apiBase)}`);}else navigate(templateRunUrl(await api.start(template.id,knobs),apiBase,template.id));}catch(problem){setError(String(problem));setBusy(false);}};
 const suggestions=localized(template,'try',locale) as string[];
 const observations=localized(template,'observe',locale) as string[];
 return <div className="template-customize" data-testid={`customize-panel-${template.id}`}>
  <p className="template-card-summary">{localized(template,'summary',locale)}</p>
  <section aria-label={t('Scenario settings')}>{template.knobs.map(knob=><TemplateKnobControl key={knob.id} knob={knob} value={knobs[knob.id]} algorithms={algorithms?.filter(row=>row.slot===knobAlgorithmSlot(knob))} onChange={value=>setKnobs(current=>({...current,[knob.id]:value}))}/>)}{!template.knobs.length&&<p>{t('This scenario comes with fixed settings. Open it in Studio to edit its rules.')}</p>}{catalogError&&<div><p>{t("Algorithm descriptions could not be loaded. The template's declared choices are shown.")}</p><Details title={t('Algorithm catalog')}><p>{catalogError}</p></Details></div>}</section>
  {!!suggestions.length&&<section className="template-hints"><h3>{t('Things to try')}</h3><ul>{suggestions.map(hint=><li key={hint}>{hint}</li>)}</ul></section>}
  {!!observations.length&&<section className="template-hints"><h3>{t('What to watch')}</h3><ul>{observations.map(hint=><li key={hint}>{hint}</li>)}</ul></section>}
  <div className="template-card-actions"><button className="console-btn console-btn-primary" data-testid={`custom-run-${template.id}`} disabled={busy} onClick={()=>void start()}>{busy&&action==='run'?t('Starting…'):t('Run with these settings')}</button><button className="console-btn" disabled={busy} onClick={()=>void start(true)}>{busy&&action==='studio'?t('Opening…'):t('Open in Studio')}</button></div>
  {error&&<div role="alert" className="template-error-card"><strong>{action==='studio'?t('Could not open the scenario in Studio.'):t('Could not start with these settings.')}</strong><p>{t('Review the settings or try again.')}</p><Details title={t('Request error')}><p>{error}</p></Details></div>}
 </div>;
}
