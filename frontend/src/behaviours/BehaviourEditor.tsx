import { Details } from '../console/Details';
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { applicableFields, applicableRelations, arity, astKind, mapping, operators, patch, temporal, type AuthorType, type Draft, type Path } from './model';
import { parseLosslessJson } from '../feeds/lossless-json';
import './editor.css';
import { ExpressionControl } from './ExpressionControl';
import { TriggerForm as Trigger } from './TriggerForm';
interface Props {
  value: Draft; onChange: (value: Draft) => void; types?: AuthorType[]; fields?: Draft[]; relations?:Draft[];
  errors?: Array<{path:string;message:string}>; onValidate?:()=>void; onValidityChange?:(valid:boolean)=>void;
  capabilities?:string[]; events?:string[]; onImportYaml?:(yaml:string)=>Promise<void>; onExportYaml?:()=>Promise<string>;
}
const RelationCatalog=createContext<Draft[]>([]);
const EditorValidity = createContext<(label:string,error:string)=>void>(()=>{});
function Json({label,value,onChange}:{label:string;value:unknown;onChange:(value:unknown)=>void}) {
  const [text,setText]=useState(()=>JSON.stringify(value,null,2) ?? ''),[error,setError]=useState('');const sent=useRef(value); const report=useContext(EditorValidity);
  useEffect(()=>{report(label,error);return()=>report(label,'');},[report,label,error]);
  useEffect(()=>{if(value!==sent.current){setText(JSON.stringify(value,null,2) ?? '');setError('');sent.current=value;}},[value]);
  return <Details title={label}><label className="behaviour-json">{label}<textarea aria-label={label} rows={4} value={text} onChange={event=>{setText(event.target.value);try{const next=parseLosslessJson(event.target.value,true);sent.current=next;onChange(next);setError('');}catch(problem){setError(String(problem));}}}/>{error&&<span role="alert">{error}</span>}</label></Details>;
}
function Ast({node,onChange,path,roles,types,fields,depth=0}:{node:unknown;onChange:(node:unknown)=>void;path:string;roles:Draft;types:AuthorType[];fields:Draft[];depth?:number}) {
  const relations=useContext(RelationCatalog);
  const kind=astKind(node), row=mapping(node)?node:{};
  const set=(key:string,next:unknown)=>onChange({...row,[key]:next});
  if(kind==='unsupported'||depth>32) return <div><p>Unsupported AST content retained at {path}; edit raw JSON. Server compiler determines support.</p><Json label={`${path} AST JSON`} value={node} onChange={onChange}/></div>;
  const relationOptions=applicableRelations(typeof row.sourceRole==='string'&&typeof roles[row.sourceRole]==='string'?roles[row.sourceRole] as string:undefined,typeof row.targetRole==='string'&&typeof roles[row.targetRole]==='string'?roles[row.targetRole] as string:undefined,types,relations);
  const options = typeof row.role === 'string' && typeof roles[row.role] === 'string' ? applicableFields(roles[row.role] as string,types,fields) : [];
  return <fieldset className="ast-node"><legend>{path} · {kind}</legend>
    <label>Node kind <select aria-label={`${path} node kind`} value={kind} onChange={event=>{
      const selected=event.target.value;
      onChange(selected==='operator'?{op:'eq',args:[{literal:null},{literal:null}]}:selected==='field'?{field:'',role:'',path:[]}:selected==='relation'?{relation:'',sourceRole:'',targetRole:''}:selected==='time'?{time:true}:selected==='parameter'?{parameter:''}:selected==='var'?{var:'',path:[]}:{literal:null});
    }}>{['literal','field','relation','operator','parameter','var','time'].map(name=><option key={name}>{name}</option>)}</select></label>
    {kind==='field'&&<><label>Role <select aria-label={`${path} role`} value={typeof row.role==='string'?row.role:''} onChange={event=>set('role',event.target.value)}><option value="">Select bound role</option>{Object.keys(roles).map(role=><option key={role}>{role}</option>)}</select></label>
      <label>Field <input aria-label={`${path} field`} list={`${path}-fields`} value={typeof row.field==='string'?row.field:''} onChange={event=>set('field',event.target.value)}/><datalist id={`${path}-fields`}>{options.map(field=><option key={String(field.id)} value={String(field.id)}>{String(field.declaring_type??field.type??field.declaringClass)} · {JSON.stringify(field.schema)}</option>)}</datalist></label>
      <small>Picker includes fields declared on this role's actual ancestors. Server resolves pinned registry applicability.</small><Json label={`${path} field path`} value={row.path ?? []} onChange={next=>set('path',next)}/></>}
    {kind==='literal'&&<Json label={`${path} literal (${row.literal===null?'null':typeof row.literal})`} value={row.literal} onChange={next=>set('literal',next)}/>}
    {kind==='relation'&&['relation','sourceRole','targetRole'].map(key=><label key={key}>{key}<input aria-label={`${path} ${key}`} value={typeof row[key]==='string'?row[key] as string:''} onChange={event=>set(key,event.target.value)} list={key==='relation'?`${path}-relations`:`${path}-roles`}/></label>)}
    {kind==='relation'&&<datalist id={`${path}-relations`}>{relationOptions.map(relation=><option key={String(relation.id)} value={String(relation.id)}>{JSON.stringify(relation)}</option>)}</datalist>}
    {kind==='relation'&&<datalist id={`${path}-roles`}>{Object.keys(roles).map(role=><option key={role}>{role}</option>)}</datalist>}
    {['parameter','var'].includes(kind)&&<label>{kind}<input aria-label={`${path} ${kind}`} value={typeof row[kind]==='string'?row[kind] as string:''} onChange={event=>set(kind,event.target.value)}/></label>}
    {kind==='operator'&&<><label>Operator <select aria-label={`${path} operator`} value={String(row.op)} onChange={event=>{
      const op=event.target.value;if(op==='all'||op==='any'){onChange({...row,op,array:{literal:[]},var:'item',predicate:{literal:true}});return;}
      const old=Array.isArray(row.args)?row.args:[];set('op',op);onChange({...row,op,args:Array.from({length:arity[op]??(temporal.includes(op)?2:Math.max(1,old.length))},(_,i)=>old[i]??{literal:null})});
    }}>{operators.map(op=><option key={op}>{op}</option>)}</select></label>
    {(row.op==='all'||row.op==='any')?<><label>Bound variable <input value={String(row.var??'')} onChange={event=>set('var',event.target.value)}/></label>{['array','predicate','applicabilityExpression'].filter(key=>key in row).map(key=><Ast key={key} node={row[key]} onChange={next=>set(key,next)} path={`${path}.${key}`} roles={roles} types={types} fields={fields} depth={depth+1}/>)}</>:Array.isArray(row.args)&&<>{row.args.map((child,index)=><Ast key={index} node={child} onChange={next=>set('args',(row.args as unknown[]).map((item,i)=>index===i?next:item))} path={`${path}.args[${index}]`} roles={roles} types={types} fields={fields} depth={depth+1}/>)}
      {!arity[String(row.op)]&&<button onClick={()=>set('args',[...(row.args as unknown[]),{literal:null}])}>Add operand</button>}</>}
      {'asScope' in row&&<Ast node={row.asScope} onChange={next=>set('asScope',next)} path={`${path}.asScope`} roles={roles} types={types} fields={fields} depth={depth+1}/>}</>}
    <Json label={`${path} complete node`} value={row} onChange={onChange}/>
  </fieldset>;
}
function Roles({value,onChange,types,label}:{value:unknown;onChange:(next:unknown)=>void;types:AuthorType[];label:string}) {
  const [role,setRole]=useState('');if(!mapping(value))return <Json label={label} value={value} onChange={onChange}/>;
  return <fieldset><legend>{label}</legend>{Object.entries(value).map(([key,type])=><label key={key}>{key}<input aria-label={`${label}.${key}`} list={`${label}-types`} value={typeof type==='string'?type:''} onChange={event=>onChange({...value,[key]:event.target.value})}/></label>)}
    <datalist id={`${label}-types`}>{types.map(type=><option key={type.id} value={type.id}>{type.abstract?'abstract · ':''}{type.parents.join(', ')}</option>)}</datalist>
    <input aria-label={`${label} new role`} value={role} onChange={event=>setRole(event.target.value)}/><button disabled={!role||role in value} onClick={()=>{onChange({...value,[role]:''});setRole('');}}>Add role</button>
  </fieldset>;
}
function Actions({value,onChange,label,roles,types,fields,capabilities}:{value:unknown;onChange:(value:unknown)=>void;label:string;roles:Draft;types:AuthorType[];fields:Draft[];capabilities:string[]}) {
  const kinds=['set','emit','command','cancel_command','delay','cancel_timer','assert_relation','close_relation','create_entity','remove_entity','complete'];
  const [kind,setKind]=useState('set');
  if(!Array.isArray(value))return <Json label={label} value={value} onChange={onChange}/>;
  const set=(index:number,next:unknown)=>onChange(value.map((row,i)=>index===i?next:row));
  return <fieldset><legend>{label}</legend>{value.map((action,index)=>mapping(action)?<fieldset key={index}><legend>{String(action.id??'Action')} · {String(action.kind)}</legend>
    <label>Action ID <input aria-label={`${label} action ${index} id`} value={typeof action.id==='string'?action.id:''} onChange={event=>set(index,{...action,id:event.target.value})}/></label>
    <label>Kind <select aria-label={`${label} action ${index} kind`} value={String(action.kind??'')} onChange={event=>set(index,{...action,kind:event.target.value})}><option value="">Unset</option>{kinds.map(name=><option key={name}>{name}</option>)}</select></label>
    {action.kind==='set'&&<><label>Target role <select aria-label={`${label} action ${index} role`} value={mapping(action.entity)&&typeof action.entity.$role==='string'?action.entity.$role:''} onChange={event=>set(index,{...action,entity:{$role:event.target.value}})}><option value="">Select</option>{Object.keys(roles).map(role=><option key={role}>{role}</option>)}</select></label>
      <label>State field <input aria-label={`${label} action ${index} field`} value={typeof action.field==='string'?action.field:''} list={`${label}-${index}-fields`} onChange={event=>set(index,{...action,field:event.target.value})}/><datalist id={`${label}-${index}-fields`}>{applicableFields(mapping(action.entity)&&typeof action.entity.$role==='string'&&typeof roles[action.entity.$role]==='string'?roles[action.entity.$role] as string:'',types,fields).map(field=><option key={String(field.id)} value={String(field.id)}/>)}</datalist></label><ExpressionControl label={`${label} action ${index} typed value`} value={action.value} onChange={next=>set(index,{...action,value:next})}/></>}
    {action.kind==='command'&&<label>Command capability<input aria-label={`${label} action ${index} capability`} list="command-capabilities" value={typeof action.capability==='string'?action.capability:''} onChange={event=>set(index,{...action,capability:event.target.value})}/><datalist id="command-capabilities">{capabilities.map(id=><option key={id}>{id}</option>)}</datalist></label>}
    {['command','emit'].includes(String(action.kind))&&<>{['schema',action.kind==='command'?'target':'topic'].map(key=><label key={key}>{key}<input value={typeof action[key]==='string'?action[key] as string:''} onChange={event=>set(index,{...action,[key]:event.target.value})}/></label>)}<ExpressionControl label={`${label} action ${index} payload`} value={action.payload} onChange={next=>set(index,{...action,payload:next})}/></>}
    {['delay','cancel_command','cancel_timer'].includes(String(action.kind))&&<Json label={`${label} action ${index} timing / child identity`} value={action} onChange={next=>set(index,next)}/>}
    <Json label={`${label} action ${index} JSON`} value={action} onChange={next=>set(index,next)}/>
  </fieldset>:<Json key={index} label={`${label} unsupported action ${index}`} value={action} onChange={next=>set(index,next)}/>)}
    <select aria-label={`${label} new action kind`} value={kind} onChange={event=>setKind(event.target.value)}>{kinds.map(name=><option key={name}>{name}</option>)}</select><button onClick={()=>onChange([...value,{id:`action-${value.length+1}`,kind}])}>Add ordered action</button></fieldset>;
}
function Declarations({section,value,onChange,types}:{section:string;value:unknown;onChange:(value:unknown)=>void;types:AuthorType[]}) {
  if(!Array.isArray(value))return <Json label={`${section} typed declarations JSON`} value={value} onChange={onChange}/>;
  const keys=section==='bindings'?['id','chain','multiplicity','on_unbind','episode_field']:section==='conflicts'?['id','predicate','edge','emits','topic']:['id','stream_id','command','target','emits','topic'];
  const options:Record<string,string[]>={multiplicity:['once_per_entity','once_per_relation','once_per_task_episode'],on_unbind:['close_after_cleanup','retain_until_terminal'],edge:['entered','exited','while']};
  return <div>{value.map((row,index)=>mapping(row)?<fieldset key={index}><legend>{section}[{index}]</legend>{keys.map(key=><label key={key}>{key}{options[key]?<select aria-label={`${section} ${index} ${key}`} value={String(row[key]??'')} onChange={event=>onChange(value.map((item,i)=>i===index?{...row,[key]:event.target.value}:item))}><option value="">Unset</option>{options[key].map(item=><option key={item}>{item}</option>)}</select>:<input aria-label={`${section} ${index} ${key}`} value={typeof row[key]==='string'?row[key] as string:''} onChange={event=>onChange(value.map((item,i)=>i===index?{...row,[key]:event.target.value}:item))}/>}</label>)}
    {section==='conflicts'&&<Roles value={row.roles} onChange={next=>onChange(value.map((item,i)=>i===index?{...row,roles:next}:item))} types={types} label={`conflicts ${index} roles`}/>}
    <ExpressionControl label={`${section} ${index} complete declaration`} value={row} onChange={next=>onChange(value.map((item,i)=>i===index?next:item))}/></fieldset>:<Json key={index} label={`${section} ${index} unsupported declaration`} value={row} onChange={next=>onChange(value.map((item,i)=>i===index?next:item))}/>)}<button onClick={()=>onChange([...value,{id:''}])}>Add {section} declaration</button></div>;
}
export function BehaviourEditor({value,onChange,types=[],fields=[],relations=[],errors=[],capabilities=[],events=[],onValidate,onImportYaml,onExportYaml,onValidityChange}:Props) {
  const [tab,setTab]=useState('predicates'),[selected,setSelected]=useState(''),[newId,setNewId]=useState(''),[yaml,setYaml]=useState(''),[error,setError]=useState(''),[connect,setConnect]=useState(false),[from,setFrom]=useState<string>();
  const [invalid,setInvalid]=useState<Record<string,string>>({});
  const report=useCallback((label:string,error:string)=>setInvalid(previous=>{if(previous[label]===error||(!error&&!(label in previous)))return previous;const next={...previous};if(error)next[label]=error;else delete next[label];return next;}),[]);
  const valid=Object.keys(invalid).length===0;useEffect(()=>{onValidityChange?.(valid);},[valid,onValidityChange]);
  const update=(path:Path,next:unknown)=>onChange(patch(value,path,next) as Draft);
  const predicates=mapping(value.predicates)?value.predicates:{},chains=mapping(value.chains)?value.chains:{};
  const ids=Object.keys(tab==='chains'?chains:predicates),id=ids.includes(selected)?selected:ids[0];
  const row=mapping((tab==='chains'?chains:predicates)[id])?(tab==='chains'?chains:predicates)[id] as Draft:undefined;
  const perform=async(fn:()=>Promise<void>)=>{setError('');try{await fn();}catch(problem){setError(String(problem));}};
  const selectState=(state:string)=>{if(!connect||!row)return; if(!from){setFrom(state);return;} const transitions=Array.isArray(row.transitions)?row.transitions:[];update(['chains',id,'transitions'],[...transitions,{id:`transition-${transitions.length+1}`,from,on:{},to:state,priority:0,actions:[]}]);setFrom(undefined);};
  return <RelationCatalog.Provider value={relations}><EditorValidity.Provider value={report}><div className="behaviour-editor">
    <label>Package ID <input aria-label="Package ID" value={typeof value.id==='string'?value.id:''} onChange={event=>update(['id'],event.target.value)}/></label>
    <button disabled={!valid} onClick={onValidate}>Validate package on server</button>
    {errors.map((item,index)=><button className="authored-error" key={index} onClick={()=>{if(item.path.includes('.chains.')){setTab('chains');setSelected(Object.keys(chains).find(name=>item.path.includes(`.chains.${name}`))??'');}else if(item.path.includes('.predicates.')){setTab('predicates');setSelected(Object.keys(predicates).find(name=>item.path.includes(`.predicates.${name}`))??'');}else setTab('raw');document.getElementById(`authored-${item.path}`)?.scrollIntoView?.();}}>{item.path}: {item.message}</button>)}
    <nav>{['predicates','chains','bindings','conflicts','injection_points','raw'].map(name=><button key={name} aria-pressed={tab===name} onClick={()=>{setTab(name);setSelected('');}}>{name}</button>)}</nav>
    {['predicates','chains'].includes(tab)&&<><label>Authored element <select aria-label="Authored element" value={id??''} onChange={event=>setSelected(event.target.value)}>{ids.map(name=><option key={name}>{name}</option>)}</select></label>
      <input aria-label="New element ID" value={newId} onChange={event=>setNewId(event.target.value)}/><button disabled={!newId||ids.includes(newId)} onClick={()=>{const section=tab==='chains'?chains:predicates;update([tab],{...section,[newId]:tab==='chains'?{roles:{},trigger:{},preconditions:[],initial:'waiting',terminal:['done'],states:['waiting','done'],transitions:[]}:{profile:'committed_reactive/v1',roles:{},expression:{literal:null}}});setSelected(newId);setNewId('');}}>Add {tab==='chains'?'chain':'predicate'}</button>
      {!row&&<p>Choose an element or edit its unsupported section in raw JSON.</p>}
      {row&&<div id={`authored-$.${tab}.${id}`}><Roles value={row.roles} onChange={next=>update([tab,id,'roles'],next)} types={types} label={`${tab}.${id}.roles`}/>
      {tab==='predicates'?<><label>Evaluation profile <select aria-label="Predicate profile" value={String(row.profile??'')} onChange={event=>update(['predicates',id,'profile'],event.target.value)}><option value="">Unset</option><option>committed_reactive/v1</option><option>aerograph_sampled/v1</option></select></label>
        <Ast node={row.expression} onChange={next=>update(['predicates',id,'expression'],next)} path={`predicates.${id}.expression`} roles={mapping(row.roles)?row.roles:{}} types={types} fields={fields}/></>:<>
        <Trigger events={events} initial value={row.trigger} onChange={next=>update(['chains',id,'trigger'],next)} label={`chains.${id}.trigger`} predicates={Object.keys(predicates)}/>
        <ExpressionControl label="Chain preconditions" value={row.preconditions??[]} onChange={next=>update(['chains',id,'preconditions'],next)}/>
        <ExpressionControl label="Chain states" value={row.states} onChange={next=>update(['chains',id,'states'],next)}/>
        <label>Initial state <input aria-label="Initial state" value={String(row.initial??'')} onChange={event=>update(['chains',id,'initial'],event.target.value)}/></label>
        <ExpressionControl label="Terminal states" value={row.terminal} onChange={next=>update(['chains',id,'terminal'],next)}/>
        <label><input type="checkbox" checked={connect} onChange={event=>{setConnect(event.target.checked);setFrom(undefined);}}/>Connect states · {from?'choose target':'choose source'}</label>
        {Array.isArray(row.states)&&<svg viewBox={`0 0 520 ${Math.max(150,row.states.length*80)}`} role="img" aria-label="Draft chain state-machine graph"><defs><marker id="draft-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#64748b"/></marker></defs>
          {Array.isArray(row.transitions)&&row.transitions.filter(mapping).map((transition,index)=>{const start=(row.states as unknown[]).indexOf(transition.from),end=(row.states as unknown[]).indexOf(transition.to);if(start<0||end<0)return null;return <g key={index} role="button" tabIndex={0} aria-label={`Graph transition ${String(transition.id)}`} onClick={()=>document.getElementById(`transition-${id}-${index}`)?.scrollIntoView?.()}><path d={`M270 ${40+start*80} C460 ${40+start*80},460 ${40+end*80},270 ${40+end*80}`} fill="none" stroke="#64748b" markerEnd="url(#draft-arrow)"/><text x="350" y={40+(start+end)*40}>{String(transition.id)}</text></g>;})}
          {row.states.map((state,index)=><g role="button" tabIndex={0} aria-label={`Graph state ${String(state)}`} key={String(state)} onClick={()=>selectState(String(state))}><rect x="80" y={index*80+15} width="190" height="50" rx="7" fill={state===from?'#e0f2fe':'#f1f5f9'} stroke="#64748b"/><text x="100" y={index*80+45}>{String(state)}{state===row.initial?' (initial)':''}</text></g>)}</svg>}
        {Array.isArray(row.transitions)&&row.transitions.map((transition,index)=>mapping(transition)?<fieldset key={index} id={`transition-${id}-${index}`}><legend>Transition {String(transition.id)}</legend>{transition.guard!==undefined&&<ExpressionControl label={`Transition ${index} guard`} value={transition.guard} onChange={next=>update(['chains',id,'transitions',index,'guard'],next)}/> }{['id','from','to'].map(key=><label key={key}>{key}<input aria-label={`Transition ${index} ${key}`} value={typeof transition[key]==='string'?transition[key] as string:''} onChange={event=>update(['chains',id,'transitions',index,key],event.target.value)}/></label>)}
          <label>Priority <input aria-label={`Transition ${index} priority`} type="number" value={typeof transition.priority==='number'?transition.priority:''} onChange={event=>update(['chains',id,'transitions',index,'priority'],event.target.value===''?undefined:event.target.valueAsNumber)}/></label>
          <Trigger events={events} actions={Array.isArray(row.transitions)?row.transitions.filter(mapping).flatMap(item=>Array.isArray(item.actions)?item.actions.filter(mapping).flatMap(action=>typeof action.id==='string'?[action.id]:[]):[]):[]} value={transition.on} onChange={next=>update(['chains',id,'transitions',index,'on'],next)} label={`Transition ${index} on`} predicates={Object.keys(predicates)}/>
          <Actions capabilities={capabilities} label={`Transition ${index} ordered actions`} value={transition.actions} onChange={next=>update(['chains',id,'transitions',index,'actions'],next)} roles={mapping(row.roles)?row.roles:{}} types={types} fields={fields}/>
          <Json label={`Transition ${index} JSON`} value={transition} onChange={next=>update(['chains',id,'transitions',index],next)}/>
        </fieldset>:<Json key={index} label={`Unsupported transition ${index}`} value={transition} onChange={next=>update(['chains',id,'transitions',index],next)}/>)}
      </>}
      <Json label={`${tab}.${id} complete JSON`} value={row} onChange={next=>update([tab,id],next)}/></div>}</>}
    {['bindings','conflicts','injection_points'].includes(tab)&&<Declarations section={tab} value={value[tab]} onChange={next=>update([tab],next)} types={types}/>}
    {tab==='raw'&&<><Json label="Behaviour package JSON" value={value} onChange={next=>{if(!mapping(next))throw Error('Behaviour package must be a mapping');onChange(next);}}/>
      <label>YAML round trip <textarea aria-label="Behaviour package YAML" rows={10} value={yaml} onChange={event=>setYaml(event.target.value)}/></label>
      <button disabled={!onExportYaml} onClick={()=>void perform(async()=>setYaml(await onExportYaml!()))}>Export package YAML</button>
      <button disabled={!onImportYaml||!yaml} onClick={()=>void perform(async()=>onImportYaml!(yaml))}>Import package YAML</button></>}
    {error&&<p role="alert">{error}</p>}
  </div></EditorValidity.Provider></RelationCatalog.Provider>;
}
