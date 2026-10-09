import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Checkbox, Collapse, Input, InputNumber, Select, Space, Table, Tabs, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n/I18nProvider';
import { StudioApi, type Workspace, type TypeRow, type TypeDetail } from './api';
import { ScenePreview } from './ScenePreview';
import { RegionMap } from './RegionMap';
import { DemoSettings } from './DemoSettings';
import { TrafficEntityTable } from './TrafficEntityTable';
import { EngineConfigForm } from './EngineConfigForm';
import { BehavioursPanel } from './BehavioursPanel';
import { PluginOwnershipPanel, type EngineDescriptor } from './PluginOwnershipPanel';
import { parseAuthoringJson } from '../feeds/lossless-json';
import './studio.css';

interface Catalog { demo_profiles?:Record<string,Record<string,unknown>>; extracts: Array<{id: string; name: string; bounds: number[]}>; engines: EngineDescriptor[] }
const customTypes: TypeRow[] = [
  { id: 'aas:StudioFacility', name: 'Studio facility / 设施', parents: ['oo:ModelObject'], abstract: false },
  { id: 'aas:StudioAirspace', name: 'Studio airspace / 空域', parents: ['oo:ModelObject'], abstract: false },
];
export default function StudioPage() {
  const { locale, setLocale } = useI18n();
  const m = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const base = new URLSearchParams(location.search).get('api') ?? window.location.origin;
  const api = useMemo(() => new StudioApi(base), [base]);
  const [catalog, setCatalog] = useState<Catalog>(), [workspaces, setWorkspaces] = useState<Workspace[]>([]), [workspace, setWorkspace] = useState<Workspace>();
  const [name, setName] = useState('City experiment'), [document, setDocument] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [extract, setExtract] = useState(''), [bounds, setBounds] = useState<number[]>([]), [alt, setAlt] = useState(0), [levelHeight, setLevelHeight] = useState(3);
  const [kind, setKind] = useState('entity'), [type, setType] = useState('oo:UAV'), [entityId, setEntityId] = useState('uav-1'), [engine, setEngine] = useState('kinematic');
  const [position, setPosition] = useState([0, 0, 10]), [facts, setFacts] = useState('{}'), [polygon, setPolygon] = useState('[[0,0],[40,0],[40,40],[0,40]]'), [floor, setFloor] = useState(0), [ceiling, setCeiling] = useState(100);
  const [types, setTypes] = useState<TypeRow[]>([]), [fields, setFields] = useState<any[]>([]), [relations,setRelations]=useState<any[]>([]), [query, setQuery] = useState(''), [detail, setDetail] = useState<TypeDetail>();
  const [validation, setValidation] = useState<Workspace['validation']>(), [runId, setRunId] = useState(''), [yaml, setYaml] = useState('');
  const [layers, setLayers] = useState(['ground', 'buildings', 'roads', 'entities', 'airspace']), [plugin, setPlugin] = useState('workflow'), [partition, setPartition] = useState('custom');
  const [engineJson, setEngineJson] = useState(''), [bindingJson, setBindingJson] = useState('');
  const [engineDirty, setEngineDirty] = useState(false), [bindingDirty, setBindingDirty] = useState(false);
  const [newConfig, setNewConfig] = useState<Record<string, unknown>>({}), [newConfigValid, setNewConfigValid] = useState(true);
  const [editingPartition, setEditingPartition] = useState(''), [editingConfigValid, setEditingConfigValid] = useState(true);
  const [behaviourValid,setBehaviourValid]=useState(true);
  const behaviourValidity=useCallback((valid:boolean)=>{setBehaviourValid(valid);if(!valid)setValidation(undefined);},[]);
  const selectedPlugin = catalog?.engines.find(item => item.id === plugin);
  const engineDraft = useMemo(() => {
    if (!engineJson) return { engines: undefined, error: '' };
    try {
      const engines = parseAuthoringJson(engineJson) as Record<string, any> as Record<string, {plugin: string; config: Record<string, unknown>}>;
      if (!engines || typeof engines !== 'object' || Array.isArray(engines)) throw Error('Engines must be a JSON object');
      for (const [id, item] of Object.entries(engines)) {
        if (!item || typeof item !== 'object' || typeof item.plugin !== 'string' || !item.config || typeof item.config !== 'object' || Array.isArray(item.config)) throw Error(`Engines.${id}: plugin and object config are required`);
      }
      return { engines, error: '' };
    } catch (error) { return { engines: undefined, error: String(error) }; }
  }, [engineJson]);
  const editedEngine = engineDraft.engines?.[editingPartition];
  const editedPlugin = catalog?.engines.find(item => item.id === editedEngine?.plugin);
  const apply = (w: Workspace) => { setEngineDirty(false); setBindingDirty(false); setWorkspace(w); setDocument(JSON.stringify(w.scenario, null, 2)); setEngineJson(JSON.stringify(w.scenario.engines, null, 2)); setBindingJson(JSON.stringify(w.scenario.bindings, null, 2)); setName(w.name); setValidation(w.validation); setRunId(''); if (w.region) { setExtract(w.region.extract); setBounds(w.region.bounds); setAlt(w.region.alt); setLevelHeight(w.region.level_height_m); } };
  const action = async (fn: () => Promise<void>) => { setBusy(true); setError(''); try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  const path = (suffix = '') => `/v1/studio/workspaces/${workspace!.id}${suffix}`;
  const parsed = () => { const doc = parseAuthoringJson(document) as Record<string, any>; if(engineDirty) doc.engines=parseAuthoringJson(engineJson) as Record<string, any>; if(bindingDirty) doc.bindings=parseAuthoringJson(bindingJson); return doc; };
  const save = async () => { if(!behaviourValid)throw Error('Correct invalid behaviour JSON before saving'); if (engineDraft.error || editedEngine && !editingConfigValid) throw Error(engineDraft.error || 'Correct the selected engine configuration before saving'); const w = await api.request<Workspace>(path(), { name, scenario: parsed() }); apply(w); return w; };
  const search = async (q: string) => { const data = await api.request<{types: TypeRow[]; fields: any[];relations?:any[]}>(`/v1/studio/types?q=${encodeURIComponent(q)}`); setTypes(data.types); setFields(data.fields);if(data.relations)setRelations(data.relations); };
  useEffect(() => { let active = true; void Promise.all([api.request<Catalog>('/v1/studio/catalog'), api.request<Workspace[]>('/v1/studio/workspaces'), api.request<{types: TypeRow[]; fields: any[];relations?:any[]}>('/v1/studio/types?q=')]).then(([c, w, t]) => { if (!active) return; setCatalog(c); setWorkspaces(w); setTypes(t.types); setFields(t.fields);if(t.relations)setRelations(t.relations); if (c.extracts[0]) { setExtract(c.extracts[0].id); setBounds(c.extracts[0].bounds); } }).catch(e => { if (active) setError(String(e)); }); return () => { active = false; }; }, [api]);
  const changeKind = (value: string) => { setKind(value); setType(value === 'entity' ? 'oo:UAV' : value === 'facility' ? 'aas:StudioFacility' : 'aas:StudioAirspace'); setEngine(value === 'entity' ? 'kinematic' : 'workflow'); setEntityId(value === 'entity' ? 'uav-1' : value === 'facility' ? 'facility-1' : 'airspace-1'); setPosition([0, 0, value === 'entity' ? 10 : 0]); };
  const chooseType = async (id: string) => { setType(id); if (id.startsWith('aas:Studio')) { const doc = parsed(); setDetail({ id, parents: ['oo:ModelObject'], abstract: false, fields: doc.registry.fields.filter((f: any) => f.type === 'oo:ModelObject' || f.type === id).map((f: any) => ({...f, declaring_type:f.type})) }); } else setDetail(await api.request<TypeDetail>(`/v1/studio/types/${encodeURIComponent(id)}`)); };
  const place = async () => { await save(); const body: any = {id: entityId, type, kind, engine, position, facts: JSON.parse(facts)}; if (kind === 'airspace') Object.assign(body, {polygon: JSON.parse(polygon), floor_m: floor, ceiling_m: ceiling}); apply(await api.request<Workspace>(path('/place'), body)); setEntityId(entityId.replace(/\d+$/, n => String(Number(n) + 1))); };
  const validate = async () => { if(!behaviourValid)throw Error('Correct invalid behaviour JSON before validation'); const scenario = parsed(); const result = await api.request<NonNullable<Workspace['validation']>>(path('/validate'), {scenario}); apply({...workspace!, scenario, validation: result}); };
  const editDocument = (value: string) => { setDocument(value); setEngineDirty(false); setBindingDirty(false); try { const doc=parseAuthoringJson(value) as Record<string, any>; setEngineJson(JSON.stringify(doc.engines,null,2)); setBindingJson(JSON.stringify(doc.bindings,null,2)); } catch {} setValidation(undefined); setRunId(''); };
  const currentSource = catalog?.extracts.find(e => e.id === extract);
  const draft = (() => { try { return parsed(); } catch { return undefined; } })();
  const scenario = draft;
  const typeOptions = [...customTypes, ...types].map(t => ({value: t.id, label: `${t.id} · ${t.name ?? ''}`, disabled: t.abstract}));
  return <div className="city-studio">
    <header className="studio-header"><div><h1>{m('City Studio', '城市场景工作室')}</h1><span>{m('Local sources · explicit writers · versioned scenarios', '本地来源 · 显式写者 · 版本化场景')}</span></div>
      <Space><Link to={`/runs?api=${encodeURIComponent(base)}`}>{m('Runs', '运行记录')}</Link><Select aria-label="Language" value={locale} options={[{value:'en-US',label:'English'},{value:'zh-CN',label:'简体中文'}]} onChange={setLocale} /></Space></header>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError('')} />}
    <Space wrap className="studio-workspaces"><Input aria-label="Workspace name" value={name} onChange={e => setName(e.target.value)} style={{width:210}} />
      <Button loading={busy} onClick={() => void action(async () => { const w = await api.request<Workspace>('/v1/studio/workspaces', {name}); apply(w); setWorkspaces(await api.request('/v1/studio/workspaces')); })}>{m('Create workspace', '创建工作区')}</Button>
      <Select aria-label="Workspace" placeholder={m('Open workspace', '打开工作区')} style={{width:230}} value={workspace?.id} options={workspaces.map(w => ({value:w.id,label:w.name}))} onChange={id => void action(async () => { apply(await api.request(`/v1/studio/workspaces/${id}`)); })} />
      <Button disabled={busy} onClick={()=>void action(async()=>{const w=await api.request<Workspace>('/v1/studio/workspaces',{name:'Traffic accident (demo)'});apply(await api.request<Workspace>(`/v1/studio/workspaces/${w.id}/templates/traffic-accident`,{console:true,capture_mode:'city'}));setWorkspaces(await api.request('/v1/studio/workspaces'));})}>{m('Traffic accident (demo)','交通事故（演示）')}</Button>
      <Button disabled={!workspace || busy} onClick={() => void action(async () => { await save(); setWorkspaces(await api.request('/v1/studio/workspaces')); })}>{m('Save workspace', '保存工作区')}</Button></Space>
    {workspace && <div className="studio-layout"><section className="studio-canvas-column">
      <Card title={m('Region & scene', '区域与场景')}><Space wrap><Select aria-label="Local extract" style={{width:170}} value={extract} options={catalog?.extracts.map(e => ({value:e.id,label:e.name}))} onChange={id => { setExtract(id); setBounds(catalog!.extracts.find(e => e.id === id)!.bounds); }} />
        <Button disabled={!currentSource || busy} onClick={() => void action(async () => { await save(); apply(await api.request(path('/region'), {extract, bounds, alt, level_height_m: levelHeight})); })}>{m('Select region', '编译选区')}</Button></Space>
        {currentSource && bounds.length === 4 && <><RegionMap source={currentSource.bounds} bounds={bounds} onChange={setBounds} scene={workspace.scene} /><Space wrap>{['West','South','East bound','North bound'].map((label,i) => <div className="studio-field" key={label}>{m(label, ['西','南','东','北'][i])}<InputNumber aria-label={label} value={bounds[i]} step={0.0001} onChange={v => { if(v!==null) setBounds(bounds.map((n,j)=>i===j?v:n)); }} /></div>)}<div className="studio-field">{m('Anchor altitude (m)', '锚点海拔 (m)')}<InputNumber aria-label="Anchor altitude" value={alt} onChange={v=>{if(v!==null)setAlt(v);}} /></div><div className="studio-field">{m('Metres per level', '每层米数')}<InputNumber aria-label="Metres per level" value={levelHeight} min={0.1} onChange={v=>{if(v!==null)setLevelHeight(v);}} /></div></Space></>}
        <p className="studio-muted">{m('Drag the map rectangle or edit coordinates. Heights from levels are explicit estimates.', '拖动地图或编辑坐标。按楼层换算的高度为显式估算。')}</p>
        <ScenePreview workspace={workspace} api={base} layers={layers} />
        <Checkbox.Group value={layers} options={['ground','buildings','roads','entities','airspace'].map((value,i)=>({value,label:m(value,['地面','建筑','道路','实体','空域'][i])}))} onChange={values=>setLayers(values as string[])} />
        {workspace.scene && <><p className="studio-muted">{workspace.scene.attribution} · ENU {workspace.scene.origin.lat.toFixed(5)}, {workspace.scene.origin.lon.toFixed(5)}</p><Collapse items={[{key:'diagnostics',label:m(`Source diagnostics (${workspace.scene.diagnostics.length})`,`来源诊断 (${workspace.scene.diagnostics.length})`),children:<ul>{workspace.scene.diagnostics.map((d,i)=><li key={i}>{d}</li>)}</ul>}]} /></>}
        <Space style={{marginTop:12}}><Button disabled={!workspace.region || busy} onClick={() => void action(async()=>{await api.request(path('/network'),{}); setWorkspace(await api.request(path()));})}>{m('Generate SUMO network','生成 SUMO 路网')}</Button><a href={`${base}${path('/network.net.xml')}`} target="_blank" rel="noreferrer">{m('Network artifact','路网文件')}</a></Space>
      </Card>
      {draft?.id==='traffic-accident'&&<Card><DemoSettings api={api} workspace={workspace} save={save} onApply={apply} profiles={catalog?.demo_profiles??{}}/></Card>}
      <Card title={m('Validation & execution','验证与运行')}><Space wrap><Button disabled={busy} onClick={()=>void action(validate)}>{m('Validate','验证')}</Button><Button type="primary" disabled={busy || !behaviourValid || !validation?.valid || !!engineDraft.error || (!!editedEngine && !editingConfigValid)} onClick={()=>void action(async()=>{const run=await api.request<{id:string}>('/v1/runs',{scenario:parsed(),studio_workspace:workspace.id});setRunId(run.id);window.location.assign(`/runs/${encodeURIComponent(run.id)}?api=${encodeURIComponent(base)}&mode=live`);})}>{m('Run now','立即运行')}</Button>{runId && <Link to={`/runs/${encodeURIComponent(runId)}?api=${encodeURIComponent(base)}&mode=live`}>{m('Open run console','打开运行控制台')}</Link>}</Space>
        {validation && <Alert style={{marginTop:12}} type={validation.valid?'success':'error'} message={validation.valid?m('Scenario valid','场景有效'):m('Validation failed','验证失败')} description={validation.valid?<code>{validation.digest}</code>:validation.errors.join('\n')} />}</Card>
    </section><aside><Tabs items={[
      {key:'entities',label:'Entities by AeroGraph type',children:draft&&<TrafficEntityTable registryFields={workspace.registry_catalog?.fields} schemas={workspace.registry_catalog?.schemas} scenario={draft} onChange={next=>editDocument(JSON.stringify(next,null,2))}/>},
      {key:'behaviours',label:m('Behaviours','行为链'),children: draft ? <Card><BehavioursPanel api={api} workspace={workspace} scenario={draft} types={[...new Map([...types,...(workspace.registry_catalog?.types??[]),...(draft.registry?.types ?? [])].map(row=>[row.id,row])).values()]} fields={[...new Map([...fields,...(workspace.registry_catalog?.fields??[]),...(draft.registry?.fields ?? [])].map(row=>[row.id,row])).values()]} relations={[...new Map([...relations,...(draft.registry?.relations ?? [])].map(row=>[row.id,row])).values()]} onChange={next => editDocument(JSON.stringify(next,null,2))} onApply={apply} save={save} onValidityChange={behaviourValidity} issues={validation?.issues} /></Card> : <Alert type="error" message="Correct the scenario JSON to edit behaviours" />},
      {key:'place',label:m('Place','放置'),children:<Card><Space direction="vertical" style={{width:'100%'}}>
        <div className="studio-field">{m('Placement kind','放置类别')}<Select aria-label="Placement kind" value={kind} style={{width:'100%'}} options={['entity','facility','airspace'].map((value,i)=>({value,label:m(value,['实体','设施','空域'][i])}))} onChange={changeKind} /></div>
        <div className="studio-field">{m('Entity ID','实体 ID')}<Input aria-label="Entity ID" value={entityId} onChange={e=>setEntityId(e.target.value)} /></div>
        <div className="studio-field">{m('Type','类型')}<Select aria-label="Type" showSearch optionFilterProp="label" style={{width:'100%'}} value={type} options={typeOptions} onChange={id=>void action(()=>chooseType(id))} /></div>
        <Space wrap>{['East','North','Up'].map((label,i)=><div className="studio-field" key={label}>{m(label,['东','北','上'][i])} (m)<InputNumber aria-label={label} value={position[i]} onChange={v=>{if(v!==null)setPosition(position.map((n,j)=>i===j?v:n));}} /></div>)}</Space>
        <div className="studio-field">{m('Engine','引擎')}<Select aria-label="Engine" style={{width:'100%'}} value={engine} options={[{value:'kinematic',label:'kinematic'},{value:'workflow',label:'workflow'}]} onChange={setEngine} /></div>
        <p className="studio-muted">{m('Kinematic template: initial rest, 100 kJ, 10 m/s, 5 m/s². Edit assumptions in Engines. Workflow keeps authored fields and a placed state.','运动学模板：初始静止、100 kJ、10 m/s、5 m/s²。可在引擎编辑器修改假设。工作流保留配置字段和放置状态。')}</p>
        <div className="studio-field">{m('Authored facts (JSON)','配置字段 (JSON)')}<Input.TextArea aria-label="Authored facts" rows={2} value={facts} onChange={e=>setFacts(e.target.value)} /></div>
        {kind==='airspace' && <><Input.TextArea aria-label="Airspace polygon" value={polygon} onChange={e=>setPolygon(e.target.value)} /><Space><InputNumber aria-label="Airspace floor" value={floor} onChange={v=>{if(v!==null)setFloor(v);}} /><InputNumber aria-label="Airspace ceiling" value={ceiling} onChange={v=>{if(v!==null)setCeiling(v);}} /></Space><p>{m('Geometry only; authority and enforcement require explicit rules.','仅配置几何；权限和强制约束须显式声明规则。')}</p></>}
        <Button type="primary" disabled={busy} onClick={()=>void action(place)}>{kind==='entity'?m('Place entity','放置实体'):kind==='facility'?m('Place facility','放置设施'):m('Place airspace','放置空域')}</Button>
        <Table size="small" pagination={false} rowKey="id" dataSource={scenario?.entities} columns={[{title:m('Entity','实体'),dataIndex:'id'},{title:m('Type','类型'),dataIndex:'type'}]} />
      </Space></Card>},
      {key:'types',label:m('AeroGraph','AeroGraph'),children:<Card><Input.Search aria-label="Search types and fields" value={query} onChange={e=>setQuery(e.target.value)} onSearch={q=>void action(()=>search(q))} placeholder={m('Search all directories','搜索全部目录')} />
        <Table size="small" rowKey="id" pagination={{pageSize:6}} dataSource={types} onRow={t=>({onClick:()=>void action(()=>chooseType(t.id))})} columns={[{title:m('Type','类型'),dataIndex:'id'},{title:m('Directory','目录'),dataIndex:'directory'},{title:m('Abstract','抽象'),dataIndex:'abstract',render:v=>v?'✓':''}]} />
        {detail && <><h3>{detail.id}</h3><p>{m('Parents','父类')}: {detail.parents.join(' → ')} {detail.abstract && <Tag>abstract</Tag>}</p><Table size="small" rowKey="id" pagination={{pageSize:6}} dataSource={detail.fields} columns={[{title:m('Inherited field','继承字段'),dataIndex:'id'},{title:m('Declared by','声明类型'),dataIndex:'declaring_type'},{title:m('Unit / frame','单位 / 坐标系'),render:(_,f)=>[typeof f.metadata.unit==='object'?JSON.stringify(f.metadata.unit):f.metadata.unit,f.metadata.frame].filter(Boolean).join(' · ')},{title:m('Schema','结构'),render:(_,f)=><details><summary>{f.schema.type}</summary><pre>{JSON.stringify(f.schema,null,2)}</pre></details>}]} /></>}
        <Collapse items={[{key:'fields',label:m(`Field search (${fields.length})`,`字段搜索 (${fields.length})`),children:<Table rowKey={(f:any)=>`${f.id}/${f.source?.file}`} size="small" pagination={{pageSize:6}} dataSource={fields} columns={[{title:'ID',dataIndex:'id'},{title:m('Declaring class','声明类型'),dataIndex:'declaringClass'},{title:m('Units','单位'),render:(_,f)=>JSON.stringify(f.unit)}]} />}]} /></Card>},
      {key:'engines',label:m('Engines & writers','引擎与写者'),children:<Card><Space wrap><Select aria-label="Plugin" value={plugin} style={{width:160}} options={catalog?.engines.map(e=>({value:e.id,label:`${e.id}${e.available?'':' · unavailable'}`,disabled:!e.available}))} onChange={value=>{setPlugin(value);setNewConfig({});}} /><Input aria-label="Partition ID" style={{width:120}} value={partition} onChange={e=>setPartition(e.target.value)} /><Button disabled={!selectedPlugin?.available || !newConfigValid} onClick={()=>{try{const doc=parsed();if(!partition.trim()||doc.engines[partition])throw Error('Partition ID must be new and nonempty');doc.engines[partition]={plugin,config:newConfig};editDocument(JSON.stringify(doc,null,2));setEngineJson(JSON.stringify(doc.engines,null,2));setEditingPartition(partition);}catch(e){setError(String(e));}}}>{m('Add engine','添加引擎')}</Button></Space>
        {selectedPlugin?.error && <Alert type="error" message={selectedPlugin.error} />}
        {selectedPlugin?.available && <EngineConfigForm key={`new/${plugin}`} schema={selectedPlugin.config_schema} value={newConfig} onChange={setNewConfig} onValidityChange={setNewConfigValid} />}
        <div className="studio-field"><label>{m('Edit configured engine','编辑已配置引擎')}</label><Select aria-label="Configured engine" allowClear value={editingPartition || undefined} options={Object.entries(engineDraft.engines ?? {}).map(([id,item])=>({value:id,label:`${id} · ${item.plugin}`}))} onChange={value=>{setEditingPartition(value ?? '');setEditingConfigValid(true);}} /></div>
        {engineDraft.error && <Alert type="error" message={engineDraft.error} />}
        {editedEngine && <>
          {editedPlugin?.error && <Alert type="error" message={editedPlugin.error} />}
          <EngineConfigForm key={`edit/${editingPartition}/${editedEngine.plugin}`} schema={editedPlugin?.config_schema} value={editedEngine.config} onValidityChange={setEditingConfigValid} onChange={config=>{setEngineJson(JSON.stringify({...engineDraft.engines,[editingPartition]:{...editedEngine,config}},null,2));setEngineDirty(true);setValidation(undefined);setRunId('');}} />
        </>}
        {draft && <PluginOwnershipPanel scenario={draft} engines={catalog?.engines ?? []} onChange={next => editDocument(JSON.stringify(next,null,2))} />}
        <p>{m('Configure the real plugin before validation. Writer bindings are per field.','验证前配置真实插件。每个字段须绑定写者。')}</p><Table rowKey={(b:any)=>`${b.entity??b.type}/${b.field??b.fields?.join(',')}/${b.writer}`} size="small" pagination={{pageSize:6}} dataSource={[...(scenario?.bindings.exact??[]),...(scenario?.bindings.rules??[])]} columns={[{title:m('Entity / type','实体 / 类型'),render:(_,b)=>b.entity??b.type},{title:m('Fields','字段'),render:(_,b)=>b.field??b.fields.join(', ')},{title:m('Writer','写者'),dataIndex:'writer'}]} />
        <div className="studio-field">{m('Engines (JSON)','引擎 (JSON)')}<Input.TextArea aria-label="Engine editor" rows={10} value={engineJson} onChange={e=>{setEngineJson(e.target.value);setEngineDirty(true);setValidation(undefined);}} /></div><div className="studio-field">{m('Bindings (JSON)','绑定 (JSON)')}<Input.TextArea aria-label="Binding editor" rows={8} value={bindingJson} onChange={e=>{setBindingJson(e.target.value);setBindingDirty(true);setValidation(undefined);}} /></div>
        <Button disabled={busy || !!engineDraft.error || (!!editedEngine && !editingConfigValid)} onClick={()=>void action(async()=>{const doc=parsed();doc.engines=parseAuthoringJson(engineJson) as Record<string, any>;doc.bindings=parseAuthoringJson(bindingJson);apply(await api.request(path(),{scenario:doc}));})}>{m('Apply and save','应用并保存')}</Button></Card>},
      {key:'yaml',label:m('YAML & advanced','YAML 与高级编辑'),children:<Card><Space><Button disabled={busy} onClick={()=>void action(async()=>{await save();setYaml(await api.export(workspace.id));})}>{m('Export YAML','导出 YAML')}</Button><Button disabled={!yaml} onClick={()=>{const url=URL.createObjectURL(new Blob([yaml],{type:'application/yaml'}));const a=documentGlobal.createElement('a');a.href=url;a.download=`${workspace.id}.yaml`;a.click();URL.revokeObjectURL(url);}}>{m('Download','下载')}</Button><Button disabled={!yaml||busy} onClick={()=>void action(async()=>{apply(await api.request(path('/import'),{yaml}));})}>{m('Import YAML','导入 YAML')}</Button></Space>
        <Input.TextArea aria-label="Scenario YAML" rows={12} value={yaml} onChange={e=>setYaml(e.target.value)} /><div className="studio-field">{m('Full scenario JSON','完整场景 JSON')}<Input.TextArea aria-label="Scenario editor" rows={18} value={document} onChange={e=>editDocument(e.target.value)} /></div></Card>},
    ]} /></aside></div>}
  </div>;
}
const documentGlobal = globalThis.document;
