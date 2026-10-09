import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Details } from '../console/Details';
import { parseLosslessJson } from '../feeds/lossless-json';
import { mapping, type Draft } from './model';
interface EntityChoice {label:string;value:unknown;typeId:string;ancestors:string[]}
interface Props { entityChoices?:EntityChoice[]; schema: unknown; value: unknown; onChange:(value:unknown)=>void; definitions?:Draft; path?:string; onValidityChange?:(valid:boolean)=>void }
const PayloadValidity=createContext<(path:string,error:string)=>void>(()=>{});
export function TypedPayloadForm(props:Props) {
  const [errors,setErrors]=useState<Record<string,string>>({});
  const report=useCallback((path:string,error:string)=>setErrors(previous=>{if(previous[path]===error||(!error&&!(path in previous)))return previous;const next={...previous};if(error)next[path]=error;else delete next[path];return next;}),[]);
  const valid=Object.keys(errors).length===0;useEffect(()=>props.onValidityChange?.(valid),[valid,props.onValidityChange]);
  return <PayloadValidity.Provider value={report}><PayloadFields {...props}/></PayloadValidity.Provider>;
}
function PayloadFields({schema,value,onChange,definitions={},path='Payload',entityChoices}:Props) {
  const [raw,setRaw]=useState(JSON.stringify(value,null,2)??''),[error,setError]=useState('');useEffect(()=>setRaw(JSON.stringify(value,null,2)??''),[value]);
  const report=useContext(PayloadValidity);useEffect(()=>{report(path,error);return()=>report(path,'');},[report,path,error]);
  const descriptor = typeof schema==='string'?definitions[schema]:mapping(schema)&&typeof schema.schema_ref==='string'?definitions[schema.schema_ref]:schema;
  if(mapping(descriptor)&&descriptor.type==='record'&&mapping(descriptor.members)) {
    const row=mapping(value)?value:{};
    return <fieldset><legend>{path==='Payload'?'Event details':path.split('.').at(-1)}</legend>{Object.entries(descriptor.members).map(([key,child])=><PayloadFields key={key} schema={child} value={row[key]} onChange={next=>onChange({...row,[key]:next})} definitions={definitions} entityChoices={entityChoices} path={`${path}.${key}`}/>)}
      <Details title="Complete payload / extra members" buttonLabel="Extra payload fields"><textarea aria-label={`${path} JSON`} value={raw} onChange={event=>{setRaw(event.target.value);try{const next=parseLosslessJson(event.target.value,true);if(!mapping(next))throw Error('Record payload must be an object');onChange(next);setError('');}catch(problem){setError(String(problem));}}}/>{error&&<p role="alert">{error}</p>}</Details></fieldset>;
  }
  const label=path.split('.').at(-1)?.replace(/_/g,' ').replace(/^./,letter=>letter.toUpperCase());
  if(mapping(descriptor)&&descriptor.type==='ref'&&entityChoices){
    const compatible=entityChoices.filter(choice=>choice.typeId===descriptor.target_type||choice.ancestors.includes(String(descriptor.target_type)));
    const reference=mapping(value)&&mapping(value.$ref)?value.$ref:undefined;
    return <label>{label}<select aria-label={path} value={reference?`${reference.id}:${mapping(reference.generation)?reference.generation.$integer:reference.generation}`:''} onChange={event=>onChange(compatible.find(choice=>mapping(choice.value)&&mapping(choice.value.$ref)&&`${choice.value.$ref.id}:${mapping(choice.value.$ref.generation)?choice.value.$ref.generation.$integer:choice.value.$ref.generation}`===event.target.value)?.value)}>
      <option value="">Optional · choose an entity</option>{compatible.map(choice=>{const ref=mapping(choice.value)&&mapping(choice.value.$ref)?choice.value.$ref:undefined;return ref?<option key={`${ref.id}:${mapping(ref.generation)?ref.generation.$integer:ref.generation}`} value={`${ref.id}:${mapping(ref.generation)?ref.generation.$integer:ref.generation}`}>{choice.label}</option>:null;})}
    </select></label>;
  }
  if(mapping(descriptor)&&['string','boolean','number','integer'].includes(String(descriptor.type))) {
    const kind=String(descriptor.type);
    const numeric = mapping(value)?value.$integer??value.$number:value;
    return <label>{label}{kind==='boolean'?<select aria-label={path} value={typeof value==='boolean'?String(value):''} onChange={event=>onChange(event.target.value===''?undefined:event.target.value==='true')}><option value="">Unset</option><option>true</option><option>false</option></select>:<input aria-label={path} value={typeof numeric==='number'||typeof numeric==='string'?String(numeric):''} onChange={event=>{
      const text=event.target.value;if(kind==='string'){onChange(text);return;}if(!text){onChange(undefined);setError('');return;}
      if(kind==='integer'&&/^-?(0|[1-9]\d*)$/.test(text)){onChange({$integer:text});setError('');}
      else if(kind==='number'&&Number.isFinite(Number(text))){onChange({$number:text});setError('');}
      else setError(`Enter a typed ${kind}`);
    }}/>} {error&&<span role="alert">{error}</span>}</label>;
  }
  return <Details title={`${path} JSON`} buttonLabel={`${path.split('.').at(-1)} details`}><label>{path} · {mapping(descriptor)?String(descriptor.type):'unsupported schema'} JSON<textarea aria-label={`${path} JSON`} rows={3} value={raw} onChange={event=>{setRaw(event.target.value);try{onChange(parseLosslessJson(event.target.value,true));setError('');}catch(problem){setError(String(problem));}}}/>{error&&<span role="alert">{error}</span>}</label></Details>;
}
