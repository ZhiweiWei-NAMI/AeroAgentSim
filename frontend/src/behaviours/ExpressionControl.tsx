import { mapping } from './model';
/** Edit actual literals/references/records without replacing unsupported extension keys. */
export function ExpressionControl({value,onChange,label}:{value:unknown;onChange:(value:unknown)=>void;label:string}) {
  if(value===undefined||value===null) return <label>{label}<select aria-label={label} value={value===null?'null':'unset'} onChange={e=>onChange(e.target.value==='null'?null:e.target.value==='boolean'?true:e.target.value==='number'?0:'')}><option value="unset">Unset</option><option value="null">Explicit null</option><option value="string">String</option><option value="number">Number</option><option value="boolean">Boolean</option></select></label>;
  if(typeof value==='boolean')return <label>{label}<select aria-label={label} value={String(value)} onChange={e=>onChange(e.target.value==='true')}><option>true</option><option>false</option></select></label>;
  if(typeof value==='number')return <label>{label}<input aria-label={label} type="number" value={value} onChange={e=>{if(e.target.value!==''&&Number.isFinite(Number(e.target.value)))onChange(Number(e.target.value));}}/></label>;
  if(typeof value==='string')return <label>{label}<input aria-label={label} value={value} onChange={e=>onChange(e.target.value)}/></label>;
  if(Array.isArray(value))return <fieldset><legend>{label}</legend>{value.map((row,i)=><ExpressionControl key={i} value={row} onChange={next=>onChange(value.map((item,j)=>i===j?next:item))} label={`${label}[${i}]`}/>)}</fieldset>;
  if(mapping(value))return <fieldset><legend>{label}</legend>{Object.entries(value).map(([key,row])=>key==='$integer'?<label key={key}>{label} exact integer<input aria-label={`${label} exact integer`} value={String(row)} onChange={e=>{if(/^-?(0|[1-9]\d*)$/.test(e.target.value))onChange({...value,[key]:e.target.value});}}/></label>:<ExpressionControl key={key} value={row} onChange={next=>onChange({...value,[key]:next})} label={`${label}.${key}`}/>)}</fieldset>;
  return <p>Unsupported {label} remains in raw YAML.</p>;
}
