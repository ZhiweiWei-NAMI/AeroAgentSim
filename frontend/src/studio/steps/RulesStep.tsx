import { useMemo, useState } from 'react';
import { Alert, Button, Card, Collapse, Input, InputNumber, Select, Space, Table, Tag } from 'antd';
import { BehavioursPanel } from '../BehavioursPanel';
import { Details } from '../../console/Details';
import { ExpressionControl } from '../../behaviours/ExpressionControl';
import { TypedPayloadForm } from '../../behaviours/TypedPayloadForm';
import { applicableFields, applicableRelations, isA, mapping, type Draft } from '../../behaviours/model';
import { readableName } from '../guided-model';
import { packagesOf, isInline, type GuidedStepProps, type CatalogProps } from './PredicatesStep';
import './RulesStep.css';

interface Props extends GuidedStepProps, CatalogProps {scenario:Record<string,any>;onValidityChange?:(valid:boolean)=>void}
/** Authored entity label from an actual fact (name/label/display_name), else the humanized id. Never fabricated. */
export function entityLabel(entity:Draft):string {
  const facts=mapping(entity.facts)?entity.facts:{};
  for (const key of ['name','label','display_name']) if (typeof facts[key]==='string'&&(facts[key] as string).trim()) return (facts[key] as string).trim();
  return readableName(String(entity.id));
}
/** Optional plan override; the default stays the original predicate-rule shape. */
export interface RulePlan {actions:Draft[];transitions?:Draft[]}
export function appendPredicateRule(scenario:Record<string,any>,index:number,id:string,predicate:string,match:Draft,action:Draft,plan?:RulePlan):Record<string,any> {
 const packages=packagesOf(scenario),pkg=packages[index];
 if(!isInline(pkg)||!mapping(pkg.predicates)||!mapping(pkg.predicates[predicate])) throw Error('Choose an inline package predicate');
 const row=pkg.predicates[predicate] as Draft;
 if(!id.trim()||mapping(pkg.chains)&&id in pkg.chains) throw Error('Choose a unique rule name');
 const actions=plan?.actions??[{...action,id:'action'},{id:'finish',kind:'complete',status:'completed'}];
 const transitions=plan?.transitions??[{id:'act',from:'waiting',on:{instance:'activated'},to:'done',actions}];
 const chain={roles:row.roles,trigger:{predicate,edge:'entered'},initial:'waiting',terminal:['done'],states:['waiting','done'],transitions};
 const next={...pkg,chains:{...(mapping(pkg.chains)?pkg.chains:{}),[id]:chain},bindings:[...(Array.isArray(pkg.bindings)?pkg.bindings:[]),{id:`${id}-binding`,chain:id,match,multiplicity:'once_per_entity',on_unbind:'retain_until_terminal'}]};
 return {...scenario,behaviours:packages.map((value,i)=>i===index?next:value)};
}
export function commandRulePlan(action:Draft):RulePlan {
 return {actions:[action],transitions:[
  {id:'issue',from:'waiting',on:{instance:'activated'},to:'waiting',actions:[action]},
  {id:'succeeded',from:'waiting',on:{receipt:action.id,status:'succeeded'},to:'done',actions:[{id:'finish',kind:'complete',status:'completed'}]},
  {id:'failed',from:'waiting',on:{receipt:action.id,status:['failed','rejected','canceled']},to:'done',actions:[{id:'finish',kind:'complete',status:'failed'}]},
 ]};
}
type RuleKind='set' |'emit'|'command'|'delay'|'assert_relation';
export function RulesStep({workspace,api,save,onApply,onChange,scenario,types,fields,relations,onValidityChange}:Props) {
 const packages=packagesOf(scenario),inline=packages.map((pkg,index)=>({pkg,index})).filter(row=>isInline(row.pkg));
 const [predicateKey,setPredicateKey]=useState<string>(),[name,setName]=useState(''),[roleBindings,setRoleBindings]=useState<Record<string,string>>({}),[targetRole,setTargetRole]=useState<string>(),[field,setField]=useState<string>(),[value,setValue]=useState<unknown>(),[error,setError]=useState('');
 const [kind,setKind]=useState<RuleKind>('set'),[emitSchema,setEmitSchema]=useState<string>(),[topic,setTopic]=useState(''),[payload,setPayload]=useState<unknown>(),[payloadValid,setPayloadValid]=useState(true),[commandCap,setCommandCap]=useState<string>(),[commandTarget,setCommandTarget]=useState(''),[duration,setDuration]= useState<number|undefined>(),[relation,setRelation]=useState<string>(),[edgeId,setEdgeId]=useState(''),[sourceRole,setSourceRole]=useState<string>(),[relTargetRole,setRelTargetRole]=useState<string>();
 const predicateOptions=inline.flatMap(({pkg,index})=>Object.keys(mapping(pkg.predicates)?pkg.predicates:{}).map(id=>({value:`${index}/${id}`,label:readableName(id)})));
 const pkgIndex=predicateKey?Number(predicateKey.slice(0,predicateKey.indexOf('/'))):undefined,predicateId=predicateKey?.slice(predicateKey.indexOf('/')+1);
 const pkg=pkgIndex===undefined?undefined:packages[pkgIndex],predicate=pkg&&predicateId&&mapping(pkg.predicates)?pkg.predicates[predicateId]:undefined;
 const roles=mapping(predicate)&&mapping(predicate.roles)?predicate.roles:{};
 const targetType=targetRole&&typeof roles[targetRole]==='string'?roles[targetRole] as string:undefined;
 const applicable=targetType?applicableFields(targetType,types,fields):[];
 const messages:Array<Draft>=scenario.registry?.messages??[],schemas:Record<string,Draft>=scenario.registry?.schemas??{};
 const behaviourEngines=Object.values(scenario.engines??{}).filter((row):row is Draft=>mapping(row)&&row.plugin==='behaviour');
 const capabilityValues=behaviourEngines.length===1&&mapping(behaviourEngines[0].config)&&mapping(behaviourEngines[0].config.capabilities)?behaviourEngines[0].config.capabilities:{};
 const capabilities:Record<string,Draft>=Object.fromEntries(Object.entries(capabilityValues).flatMap(([id,row])=>mapping(row)?[[id,row]]:[]));
 const messageSchema=(id:string|undefined)=>{const row=messages.find(item=>item.id===id)?.schema;return typeof row==='string'?schemas[row]:row;};
 const eventOptions=messages.filter(row=>row.kind==='event').map(row=>({value:String(row.id),label:readableName(String(row.id))}));
 const commandOptions=messages.filter(row=>row.kind==='command').map(row=>({value:String(row.id),label:readableName(String(row.id))}));
 const emitSchemaResolved=messageSchema(emitSchema),commandCapSchema=messageSchema(typeof capabilities[commandCap??'']?.schema==='string'?String(capabilities[commandCap??'']!.schema):undefined);
 const relSource=sourceRole&&typeof roles[sourceRole]==='string'?roles[sourceRole] as string:undefined;
 const relTarget=relTargetRole&&typeof roles[relTargetRole]==='string'?roles[relTargetRole] as string:undefined;
 const relationOptions=useMemo(()=>applicableRelations(relSource,relTarget,types,relations).map(row=>({value:String(row.id),label:readableName(String(row.id))})),[relSource,relTarget,relations]);
 const entityOptions=(type:string)=>[{value:'@type',label:`Every ${readableName(type)}`},...(Array.isArray(scenario.entities)?scenario.entities:[]).filter((entity:any)=>isA(entity.type,type,types)).map((entity:any)=>({value:String(entity.id),label:entityLabel(entity)}))];
 const rows=useMemo(()=>inline.flatMap(({pkg,index})=>Object.entries(mapping(pkg.chains)?pkg.chains:{}).filter(([,chain])=>mapping(chain)).map(([id,chain])=>{const row=chain as Draft,bindings=(Array.isArray(pkg.bindings)?pkg.bindings:[]).filter((binding:any)=>binding.chain===id);return {key:`${index}/${id}`,id,chain:row,bindings};})),[scenario.behaviours]);
 const common={api,workspace,scenario,types,fields,relations,onChange,onApply,save,onValidityChange:onValidityChange ?? (()=>{})};
 const kindOptions=[{value:'set',label:'Set state'},{value:'emit',label:'Emit event'},{value:'command',label:'Issue command'},{value:'delay',label:'Delay, then complete'},{value:'assert_relation',label:'Assert relation'}];
 const completeAction={id:'finish',kind:'complete',status:'completed'};
 const delayReady=kind!=='delay'||typeof duration==='number'&&Number.isInteger(duration)&&duration>=1;
 const ready=name&&predicateId&&pkgIndex!==undefined&&Object.keys(roles).every(role=>roleBindings[role])&&delayReady&&(
  kind==='set'?targetRole&&field&&value!==undefined:
  kind==='emit'?emitSchema&&topic.trim()&&payload!==undefined&&payloadValid:
  kind==='command'?payload!==undefined&&payloadValid&&(commandCap?true:Boolean(commandTarget.trim()&&emitSchema&&messages.find(row=>row.id===emitSchema)?.kind==='command')):
  kind==='assert_relation'?relation&&edgeId.trim()&&sourceRole&&relTargetRole:kind==='delay');
 const buildPlan=():RulePlan=>{
  if(kind==='set') return {actions:[{...{kind:'set',entity:{$role:targetRole},field,value},id:'action'},completeAction]};
  if(kind==='emit') return {actions:[{id:'action',kind:'emit',schema:emitSchema,topic,payload},completeAction]};
  if(kind==='command') return commandRulePlan(commandCap?{id:'action',kind:'command',capability:commandCap,payload}:{id:'action',kind:'command',schema:emitSchema,target:commandTarget,payload});
  if(kind==='assert_relation') return {actions:[{id:'action',kind:'assert_relation',edge_id:edgeId,relation,source:{$role:sourceRole},target:{$role:relTargetRole}},completeAction]};
  // Delay: the completing transition must wait for the scheduled timer; completing in the same
  // transition would tear the instance down and cancel the pending timer.
  return {actions:[{id:'wait',kind:'delay',duration_ns:duration}],transitions:[
   {id:'wait-timer',from:'waiting',on:{instance:'activated'},to:'waiting',actions:[{id:'wait',kind:'delay',duration_ns:duration}]},
   {id:'finish',from:'waiting',on:{timer:'wait'},to:'done',actions:[completeAction]}]};
 };
 return <div className="guided-root">
  <Card title="Create a predicate rule"><p className="guided-muted">When a predicate becomes true, start a chain for the chosen entity or every matching type, then perform an action.</p><div className="guided-rule-builder"><label>Rule name<Input aria-label="Rule name" value={name} onChange={event=>setName(event.target.value)} placeholder="e.g. incident-alert"/></label><label>When<Select aria-label="Rule predicate" placeholder="Choose a predicate" value={predicateKey} options={predicateOptions} onChange={key=>{setPredicateKey(key);setRoleBindings({});setTargetRole(undefined);setField(undefined);setValue(undefined);}}/></label><span className="guided-rule-connector">becomes true</span></div>
  {Object.entries(roles).map(([role,type])=><label className="guided-rule-role" key={role}>For {readableName(role)}<Select aria-label={`Rule entity ${role}`} placeholder={`Choose a ${readableName(String(type))}`} value={roleBindings[role]} options={entityOptions(String(type))} onChange={next=>setRoleBindings({...roleBindings,[role]:next})}/></label>)}
  <div className="guided-rule-builder guided-rule-actions"><span className="guided-rule-connector">→</span><label>Rule action<Select aria-label="Rule action kind" value={kind} options={kindOptions} onChange={next=>setKind(next as RuleKind)}/></label>
  {kind==='set'&&<><label>Target role<Select aria-label="Rule action role" value={targetRole} options={Object.keys(roles).map(role=>({value:role,label:readableName(role)}))} onChange={next=>{setTargetRole(next);setField(undefined);}}/></label><label>Field<Select aria-label="Rule action field" showSearch optionFilterProp="label" value={field} options={applicable.map(row=>({value:String(row.id),label:readableName(String(row.id))}))} onChange={setField}/></label></>}
  {kind==='emit'&&<><label>Event<Select aria-label="Rule emit event" placeholder="Registry event" showSearch optionFilterProp="label" value={emitSchema} options={eventOptions} onChange={id=>{setEmitSchema(id);setPayload(undefined);}}/></label><label>Topic<Input aria-label="Rule emit topic" value={topic} onChange={event=>setTopic(event.target.value)} placeholder="e.g. ops.events"/></label></>}
  {kind==='command'&&<><label>By<Select aria-label="Rule command mode" value={commandCap?'capability':'explicit'} options={[{value:'capability',label:'Configured capability'},{value:'explicit',label:'Schema + target'}]} onChange={next=>{setCommandCap(next==='capability'?commandCap??Object.keys(capabilities)[0]:undefined);setPayload(undefined);}}/></label>{commandCap?<label>Capability<Select aria-label="Rule command capability" value={commandCap} options={Object.keys(capabilities).map(id=>({value:id,label:readableName(id)}))} onChange={id=>{setCommandCap(id);setPayload(undefined);}}/></label>:<><label>Command schema<Select aria-label="Rule command schema" showSearch optionFilterProp="label" value={emitSchema&&commandOptions.some(row=>row.value===emitSchema)?emitSchema:undefined} options={commandOptions} onChange={id=>{setEmitSchema(id);setCommandCap(undefined);setPayload(undefined);}}/></label><label>Target<Input aria-label="Rule command target" value={commandTarget} onChange={event=>setCommandTarget(event.target.value)}/></label></>}</>}
  {kind==='delay'&&<label>Duration (ns)<InputNumber aria-label="Rule delay duration" min={1} precision={0} value={duration} onChange={next=>setDuration(typeof next==='number'?next:undefined)}/></label>}
  {kind==='assert_relation'&&<><label>Relation<Select aria-label="Rule relation" showSearch optionFilterProp="label" value={relation} options={relationOptions} onChange={setRelation}/></label><label>Edge ID<Input aria-label="Rule relation edge" value={edgeId} onChange={event=>setEdgeId(event.target.value)}/></label><label>Source role<Select aria-label="Rule relation source" value={sourceRole} options={Object.keys(roles).map(role=>({value:role,label:readableName(role)}))} onChange={next=>{setSourceRole(next);setRelation(undefined);}}/></label><label>Target role<Select aria-label="Rule relation target" value={relTargetRole} options={Object.keys(roles).map(role=>({value:role,label:readableName(role)}))} onChange={next=>{setRelTargetRole(next);setRelation(undefined);}}/></label></>}</div>
  {kind==='set'&&field&&<ExpressionControl label="Rule state value" value={value} onChange={setValue}/>}
  {kind==='emit'&&emitSchema&&<div className="guided-rule-payload"><TypedPayloadForm schema={emitSchemaResolved} definitions={schemas} value={payload} path="Event payload" onChange={setPayload} onValidityChange={setPayloadValid}/></div>}
  {kind==='command'&&(commandCap?Boolean(commandCapSchema):Boolean(messageSchema(emitSchema)))&&<div className="guided-rule-payload"><TypedPayloadForm schema={commandCap?commandCapSchema:messageSchema(emitSchema)} definitions={schemas} value={payload} path="Command payload" onChange={setPayload} onValidityChange={setPayloadValid}/></div>}
  <Space direction="vertical"><Button disabled={!ready} onClick={()=>{try{const match=Object.fromEntries(Object.entries(roles).map(([role,type])=>[role,roleBindings[role]==='@type'?{is_a:type}:{entity:roleBindings[role]}]));onChange(appendPredicateRule(scenario,pkgIndex!,name,predicateId!,match,kind==='set'?{kind:'set',entity:{$role:targetRole},field,value}:{},buildPlan()));setName('');setError('');}catch(problem){setError(String(problem));}}}>Add rule to draft</Button>{error&&<Alert type="error" message={error}/>}<p className="guided-muted">Commands wait for their execution receipt; delays wait for their timer. Edit the state-machine graph below to add subsequent steps.</p></Space></Card>
  <Card title="Event chains"><Table size="small" rowKey="key" pagination={{pageSize:6}} dataSource={rows} columns={[{title:'Chain',dataIndex:'id',render:readableName},{title:'States',key:'states',render:(_,row)=>Array.isArray(row.chain.states)?row.chain.states.map((state:string)=><Tag key={state}>{readableName(state)}</Tag>):'No states'},{title:'Binding',key:'bindings',render:(_,row)=>row.bindings.map((binding:any)=><p key={binding.id} className="guided-binding">Auto-instantiated {binding.multiplicity==='once_per_task_episode'?'for each task episode':'for each matching entity'} · {Object.entries(mapping(binding.match)?binding.match:{}).map(([role,selector])=>{const value=mapping(selector)?selector.entity??selector.is_a:undefined;const entity=typeof value==='string'?(scenario.entities??[]).find((row:any)=>row.id===value):undefined;return `${readableName(role)}: ${entity?entityLabel(entity):mapping(selector)?readableName(String(selector.entity ?? selector.is_a ?? 'relation match')):'unsupported selector'}`;}).join(' · ')}</p>)}]} expandable={{expandedRowRender:row=><Details title={`${readableName(row.id)} chain`}><pre>{JSON.stringify(row.chain,null,2)}</pre></Details>}}/></Card>
  <Collapse className="guided-rules-editor" defaultActiveKey={['editor']} items={[{key:'editor',label:'State-machine graphs, transitions and actions',children:<BehavioursPanel {...common} initialTab="chains"/>}]}/>
  <Card title="Conflict rules & injection points">{inline.map(({pkg,index})=><div key={index}><h3>{readableName(String(pkg.id))}</h3><Table size="small" rowKey="id" pagination={false} dataSource={Array.isArray(pkg.conflicts)?pkg.conflicts:[]} columns={[{title:'Conflict rule',dataIndex:'id',render:readableName},{title:'Condition',dataIndex:'predicate',render:readableName},{title:'Event',dataIndex:'emits',render:readableName}]}/><h4>External events an operator can fire</h4><Table size="small" rowKey="id" pagination={false} dataSource={Array.isArray(pkg.injection_points)?pkg.injection_points:[]} columns={[{title:'Injection point',dataIndex:'id',render:readableName},{title:'Event',dataIndex:'emits',render:(id)=>id?readableName(id):'Typed command'},{title:'Payload fields',key:'payload',render:(_,point)=>{const schema=scenario.registry?.messages?.find((row:any)=>row.id===point.emits)?.schema;const resolved=typeof schema==='string'?scenario.registry?.schemas?.[schema]:schema;return mapping(resolved?.members)?Object.keys(resolved.members).map(readableName).join(', '):<Details title="Payload schema"><pre>{JSON.stringify(point.schema ?? schema ?? point,null,2)}</pre></Details>;}}]}/></div>)}</Card>
 </div>;
}
