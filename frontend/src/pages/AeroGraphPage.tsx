import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Checkbox, Input, Select, Table, Tag, Tree } from 'antd';
import { Link, useLocation } from 'react-router-dom';
import { StudioApi } from '../studio/api';
import { Details } from '../console/Details';
import { PageHeader, PageState } from '../console/PageState';
import { useI18n } from '../i18n/I18nProvider';
import './aerograph.css';
import { readableLabel } from './inspection-format';

/** Native catalog definitions joined with the selected workspace. */
interface ExplorerPayload {
  workspace: { id: string; name: string } | null;
  types: Array<{
    id: string; name: unknown; description?: string; parents: string[];
    abstract?: boolean; directory?: string; count: number | null;
  }>;
  fields: Array<{
    id: string; name: unknown; description?: string; declaring_type: string;
    schema?: Record<string, unknown>; metadata?: Record<string, unknown>;
    writers: Array<{ engine_id: string; plugin: string; entities: string[] }>;
  }>;
  relations: Array<Record<string, unknown>>;
  predicates: Array<{ id: string; name?: unknown; description?: string; definition: Record<string, unknown> }>;
  events?: Array<{ id: string; name?: unknown; description?: string; definition: Record<string, unknown> }>;
  workspace_relations?: Array<Record<string,unknown>>;
  package_errors?: string[];
  chains: Array<{ id: string; name?: unknown; description?: string; definition: Record<string, unknown> }>;
  entities: Array<{ id: string; type: string; label: string }>;
  workspaces: Array<{ id: string; name: string }>;
}

type Row = Record<string, unknown>;

const DEFAULT_BASE = ['3000', '4179'].includes(window.location.port)
  ? 'http://127.0.0.1:8002' : window.location.origin;

function displayName(name: unknown, locale: string): string {
  if (typeof name === 'string') return name;
  if (name && typeof name === 'object') {
    const record = name as Row;
    const value = locale.startsWith('zh')
      ? record['zh-CN'] ?? record.zh ?? record['en-US']
      : record['en-US'] ?? record.en ?? record['zh-CN'];
    if (typeof value === 'string') return value;
    const fallback = locale.startsWith('zh') ? record['en-US'] : record['zh-CN'];
    if (typeof fallback === 'string') return fallback;
  }
  return '';
}

/** Column head token (source field-id suffix after the declaring class). */
function fieldToken(id: string): string {
  const tail = id.includes('.') ? id.slice(id.lastIndexOf('.') + 1) : id;
  return tail || id;
}

function schemaText(schema: Row | undefined): string {
  if (!schema || typeof schema !== 'object') return '—';
  const type = schema['type'];
  const unit = (schema['unit'] as Row | undefined)?.['symbol'];
  const nullable = schema['nullable'] === true ? ' · nullable' : '';
  const record = typeof type === 'string' && (schema['members'] ?? schema['properties'])
    ? ` record(${Object.keys((schema['members'] ?? schema['properties']) as Row).join(', ')})` : '';
  return `${typeof type === 'string' ? type : 'complex'}${record}${unit ? ` · ${String(unit)}` : ''}${nullable}`;
}

/** Ancestors of `id` within the type map, nearest first; unknown parents are skipped. */
function ancestry(id: string, byId: Map<string, ExplorerPayload['types'][number]>): string[] {
  const chain: string[] = [];
  const seen = new Set<string>([id]);
  let cursor = id;
  while (true) {
    const row = byId.get(cursor);
    const next = row?.parents.find(parent => !seen.has(parent));
    if (!next) break;
    chain.push(next);
    seen.add(next);
    cursor = next;
  }
  return chain;
}

/** Tokens referenced by a definition AST: rule/field/relation/parameter names. */
function referencedTokens(value: unknown, out: Set<string>): Set<string> {
  if (Array.isArray(value)) for (const item of value) referencedTokens(item, out);
  else if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value as Row)) {
      if (typeof item === 'string' && ['rule', 'field', 'relation', 'parameter', 'predicate', 'chain', 'sourceRole', 'targetRole'].includes(key)) out.add(item);
      referencedTokens(item, out);
    }
  } else if (typeof value === 'string') out.add(value);
  return out;
}

function typeLabel(row: ExplorerPayload['types'][number] | undefined, id: string): string {
  const name = row?.name;
  if (name && typeof name === 'object') {
    const en = (name as Row).en ?? (name as Row)['en-US'];
    if (typeof en === 'string') return en;
  }
  if (typeof name === 'string' && !/[\u3400-\u9fff]/.test(name)) return name;
  return readableLabel(id);
}
function relationEnds(row: Row): [string | undefined,string | undefined] {
  const source=row.sourceClass ?? row.source_type ?? row.source;
  const target=row.targetClass ?? row.target_type ?? row.target;
  return [typeof source==='string'?source:undefined,typeof target==='string'?target:undefined];
}
function TypeGraph({selected,types,relations,onSelect}: {selected:string;types:Map<string,ExplorerPayload['types'][number]>;relations:Row[];locale:string;onSelect:(id:string)=>void}) {
  const [camera,setCamera]=useState({x:0,y:0,scale:1});
  const [includeInherited,setIncludeInherited]=useState(false);
  const visibleRelations=useMemo(()=>includeInherited?relations:relations.filter(row=>relationEnds(row).includes(selected)),[relations,selected,includeInherited]);
  const drag=useRef<{x:number;y:number;tx:number;ty:number}>();
  useEffect(()=>setCamera({x:0,y:0,scale:1}),[selected]);
  const nodes=useMemo(()=>{
    const ids=new Set([selected,...ancestry(selected,types)]);
    for(const row of types.values()) if(row.parents.includes(selected)) ids.add(row.id);
    for(const row of visibleRelations) for(const id of relationEnds(row)) if(id) ids.add(id);
    return [...ids].map((id,index)=>({id,label:typeLabel(types.get(id),id),x:index===0?320:110+(index-1)%3*210,y:index===0?44:126+Math.floor((index-1)/3)*72}));
  },[selected,types,visibleRelations]);
  const positions=new Map(nodes.map(row=>[row.id,row]));
  const edges:Array<{source:string;target:string;label:string;ancestry:boolean}>=[];
  for(const node of nodes) for(const parent of types.get(node.id)?.parents??[]) if(positions.has(parent)) edges.push({source:node.id,target:parent,label:'is a',ancestry:true});
  for(const row of visibleRelations) {const [source,target]=relationEnds(row);if(source&&target&&positions.has(source)&&positions.has(target))edges.push({source,target,label:displayName(row.displayName,'en')||readableLabel(String(row.id)),ancestry:false});}
  return <div className="aerograph-graph" data-testid="aerograph-graph"><div className="aerograph-graph-controls"><span>{nodes.length} types · {edges.length} connections</span><Checkbox checked={includeInherited} onChange={event=>setIncludeInherited(event.target.checked)}>Inherited relations</Checkbox><button className="console-btn" onClick={()=>setCamera({x:0,y:0,scale:1})}>Reset view</button><button className="console-btn" aria-label="Zoom type graph in" onClick={()=>setCamera(c=>({...c,scale:Math.min(3,c.scale*1.2)}))}>+</button><button className="console-btn" aria-label="Zoom type graph out" onClick={()=>setCamera(c=>({...c,scale:Math.max(.3,c.scale/1.2)}))}>−</button></div>
    <svg viewBox="0 0 640 360" role="img" aria-label="Selected type and neighbours"
      onPointerDown={event=>{if((event.target as Element).closest('.aerograph-type-node'))return;drag.current={x:event.clientX,y:event.clientY,tx:camera.x,ty:camera.y};event.currentTarget.setPointerCapture(event.pointerId);}}
      onPointerMove={event=>{if(drag.current){const box=event.currentTarget.getBoundingClientRect();setCamera(c=>({...c,x:drag.current!.tx+(event.clientX-drag.current!.x)*640/box.width,y:drag.current!.ty+(event.clientY-drag.current!.y)*640/box.width}));}}}
      onPointerUp={()=>{drag.current=undefined;}} onPointerCancel={()=>{drag.current=undefined;}}
      onWheel={event=>setCamera(c=>({...c,scale:Math.max(.3,Math.min(3,c.scale*(event.deltaY<0?1.1:1/1.1)))}))}>
      <defs><marker id="type-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#8fa4b3"/></marker></defs>
      <g transform={`translate(${camera.x} ${camera.y}) scale(${camera.scale})`}>
        {edges.map((edge,index)=>{const a=positions.get(edge.source)!,b=positions.get(edge.target)!;return <line key={index} x1={a.x} y1={a.y+18} x2={b.x} y2={b.y-18} stroke={edge.ancestry?'#8fa4b3':'#5aaca2'} strokeDasharray={edge.ancestry?'4 4':undefined} markerEnd="url(#type-arrow)"><title>{edge.label}</title></line>;})}
        {nodes.map(node=><g key={node.id} className="aerograph-type-node" role="button" tabIndex={0} aria-label={`Explore ${node.label}`} aria-pressed={node.id===selected} onClick={()=>{if(types.has(node.id))onSelect(node.id);}} onKeyDown={event=>{if((event.key==='Enter'||event.key===' ')&&types.has(node.id)){event.preventDefault();onSelect(node.id);}}} transform={`translate(${node.x} ${node.y})`}><rect x="-88" y="-20" width="176" height="40" rx="8" fill={node.id===selected?'#0d7f74':'white'} stroke={node.id===selected?'#0d7f74':'#cdd7e0'}/><text textAnchor="middle" dy="5" fontSize="13" fill={node.id===selected?'white':'#17222e'}>{node.label.length>24?node.label.slice(0,23)+'…':node.label}</text><title>{node.label}</title></g>)}
      </g>
    </svg><span className="aerograph-graph-hint">Dashed: is a · solid: relation · drag to pan</span>
  </div>;
}

export default function AeroGraphPage() {
  const { locale } = useI18n();
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const base = query.get('api') ?? DEFAULT_BASE;
  const api = useMemo(() => new StudioApi(base), [base]);

  const requestedType=query.get('type');
  const requestedWorkspace=query.get('workspace');
  const [payload, setPayload] = useState<ExplorerPayload>();
  const [workspaceId, setWorkspaceId] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const load = useCallback((target?: string) => {
    setLoading(true);
    setError(undefined);
    api.request<ExplorerPayload>(`/v1/studio/explorer${target ? `?workspace_id=${encodeURIComponent(target)}` : ''}`)
      .then(data => {
        data.relations = [...data.relations, ...(data.workspace_relations ?? []).map(row=>({...row,sourceClass:row.sourceClass??row.source_type??row.source,targetClass:row.targetClass??row.target_type??row.target}))];
        setPayload(data);
        setWorkspaceId(data.workspace?.id);
        setSelected(previous => previous && data.types.some(row => row.id === previous) ? previous : requestedType && data.types.some(row=>row.id===requestedType) ? requestedType : data.types.find(row=>row.count!==null&&row.count>0&&!row.abstract)?.id ?? data.types[0]?.id);
      })
      .catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setLoading(false));
  }, [api, requestedType]);

  useEffect(() => { load(requestedWorkspace ?? undefined); }, [load,requestedWorkspace]);

  const types = payload?.types ?? [];
  const byId = useMemo(() => new Map(types.map(row => [row.id, row])), [types]);
  const ancestors = useMemo(() => (selected ? ancestry(selected, byId) : []), [selected, byId]);

  // Declared native fields on the selected type or any ancestor (draft native types may fail /types/{id} policy).
  const typeFields = useMemo(() => (payload?.fields ?? []).filter(field =>
    (field.declaring_type === selected || ancestors.includes(field.declaring_type)) && !/(digest|sha256|provenance|hash)/i.test(field.id)), [payload, selected, ancestors]);

  const typeRelations = useMemo(() => (payload?.relations ?? []).filter(row => {
    const source = row['sourceClass'] ?? row['source_type'] ?? row['source'];
    const target = row['targetClass'] ?? row['target_type'] ?? row['target'];
    return source === selected || target === selected || (typeof source==='string'&&ancestors.includes(source)) || (typeof target==='string'&&ancestors.includes(target));
  }), [payload, selected,ancestors]);

  const references = useMemo(() => {
    const result: Array<{ kind: 'Predicate' | 'Event' | 'Chain'; id: string; name: string; description: string; definition: Row }> = [];
    const active = new Set<string>([selected ?? "", ...ancestors,...typeFields.map(row=>row.id),...typeRelations.map(row=>String(row.id))]);
    for (const [list, kind] of [[payload?.predicates ?? [], 'Predicate'], [payload?.chains ?? [], 'Chain'], [payload?.events ?? [], 'Event']] as const) {
      for (const item of list) {
        const tokens = referencedTokens(item.definition, new Set());
        const entityTypes = (item.definition['entityTypeIds'] as unknown) ?? item.definition['entityTypeIds'];
        const typeMatch = Array.isArray(entityTypes) && entityTypes.some(entry => active.has(String(entry)));
        if (typeMatch || [...tokens].some(token => [...active].some(id => token === id))) {
          result.push({ kind, id: item.id, name: displayName(item.name, locale), description: item.description ?? '', definition: item.definition });
        }
      }
    }
    return result;
  }, [payload, selected, ancestors, locale,typeFields,typeRelations]);

  const entities = useMemo(() => (payload?.entities ?? []).filter(entity => entity.type === selected || ancestry(entity.type,byId).includes(selected ?? '')), [payload, selected]);
  const workspaceTypes = payload?.workspaces ?? [];

  const filteredTree = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return types;
    return types.filter(row =>
      displayName(row.name, locale).toLowerCase().includes(needle) || row.id.toLowerCase().includes(needle));
  }, [types, search, locale]);

  const treeNodes=useMemo(()=>{
    const visible=new Set(filteredTree.map(row=>row.id));
    if(search) for(const id of [...visible]) for(const parent of ancestry(id,byId)) visible.add(parent);
    type Node={key:string;title:React.ReactNode;children?:Node[]};
    const children=new Map<string,ExplorerPayload['types']>();
    for(const row of types) if(visible.has(row.id)){const parent=row.parents.find(id=>byId.has(id)&&visible.has(id))??'';children.set(parent,[...(children.get(parent)??[]),row]);}
    const build=(parent:string,seen:Set<string>):Node[]=>(children.get(parent)??[]).filter(row=>!seen.has(row.id)).sort((a,b)=>typeLabel(a,a.id).localeCompare(typeLabel(b,b.id))).map(row=>({key:row.id,title:<span className="aerograph-tree-label"><span>{typeLabel(row,row.id)}</span><span className="aerograph-count">{row.count===null?'—':row.count}</span></span>,children:build(row.id,new Set([...seen,row.id]))}));
    return build('',new Set());
  },[types,filteredTree,byId,search]);

  const selectedRow = selected ? byId.get(selected) : undefined;
  const selectedName = selectedRow ? typeLabel(selectedRow,selectedRow.id) : '';
  const selectedNameOther = selectedRow
    ? displayName(selectedRow.name, 'zh-CN') : '';

  const columns = [
    {
      title: 'Field', dataIndex: 'name', key: 'name',
      render: (_: unknown, row: ExplorerPayload['fields'][number]) => displayName(row.name, 'en-US') || readableLabel(fieldToken(row.id)),
    },
    { title: 'Value type', key: 'schema', render: (_: unknown, row: ExplorerPayload['fields'][number]) => schemaText(row.schema) },
    {
      title: 'Domain', key: 'domain',
      render: (_: unknown, row: ExplorerPayload['fields'][number]) => {
        const value = (row.metadata as Row | undefined)?.['domain'] ?? row.metadata?.['domain'];
        return typeof value === 'string' && value ? readableLabel(value) : '—';
      },
    },
    { title:'Unit',key:'unit',render:(_:unknown,row:ExplorerPayload['fields'][number])=>{const unit=row.metadata?.unit??row.schema?.unit;return typeof unit==='string'?unit:unit&&typeof unit==='object'&&typeof (unit as Row).symbol==='string'?String((unit as Row).symbol):'—';} },
    {
      title: 'Plugin writer', key: 'writers',
      render: (_: unknown, row: ExplorerPayload['fields'][number]) => row.writers.length
        ? row.writers.map((writer, index) => (
          <Tag key={index}>{writer.plugin ? readableLabel(writer.plugin) : 'Missing plugin binding'}{writer.entities.length ? ` · ${writer.entities.length}` : ''}</Tag>
        ))
        : <span style={{ color: 'var(--csl-text-muted)' }}>No declared writer</span>,
    },
  ] as const;

  return <div className="console-page aerograph" data-testid="aerograph-page">
    <PageHeader
      eyebrow="Explore"
      title="AeroGraph"
      description="Browse the shared entity types, fields, relations, predicates and behaviour chains behind your workspaces."
    />
    {error && <PageState kind="error" title="The explorer catalog is unavailable" description={error} action={<button className="console-btn" onClick={() => load(workspaceId)}>Retry</button>} />}
    {!error && loading && <PageState kind="loading" title="Loading the AeroGraph catalog" />}
    {!error && !loading && types.length === 0 && (
      <PageState kind="empty" title="No ontology types found" description="The configured AeroGraph source checkout exposes no type rows." />
    )}
    {payload?.package_errors?.map((message,index)=><PageState key={index} kind="error" title="A behaviour package could not be read" description={message}/>)}
    {!error && !loading && types.length > 0 && <div className="aerograph">
      <div className="aerograph-toolbar">
        <Select
          className="aerograph-workspace"
          aria-label="Workspace"
          placeholder="All workspaces (native catalog)"
          value={workspaceId}
          options={workspaceTypes.map(workspace => ({ value: workspace.id, label: workspace.name }))}
          onChange={(value?: string) => load(value)}
          loading={loading}
        />
        <span className="aerograph-meta">
          {payload?.workspace ? payload.workspace.name : 'Native catalog only'} · {types.length} types · {typeFields.length} declared fields on selection
        </span>
      </div>
      <div className="aerograph-layout">
        <div className="aerograph-tree">
          <Input.Search
            className="aerograph-tree-search"
            aria-label="Search types"
            placeholder="Search types"
            allowClear
            value={search}
            onChange={event => setSearch(event.target.value)}
          />
          <div className="aerograph-tree-body">
            <div data-testid="aerograph-tree"><Tree showLine blockNode selectedKeys={selected?[selected]:[]} defaultExpandedKeys={ancestors} autoExpandParent treeData={treeNodes} onSelect={keys=>{if(keys[0])setSelected(String(keys[0]));}} /></div>
          </div>
        </div>
        <div className="aerograph-main">
          {selectedRow && <div className="aerograph-panel">
            {ancestors.length > 0 && <nav className="aerograph-crumbs" aria-label="Type ancestry">
              {[...ancestors].reverse().map(id => (
                <span key={id}>
                  <button onClick={() => setSelected(id)}>{typeLabel(byId.get(id),id)}</button>
                  <span className="aerograph-crumb-sep"> › </span>
                </span>
              ))}
            </nav>}
            <div className="aerograph-title">
              <h2>{selectedName || readableLabel(selected ?? '')}</h2>
              {selectedNameOther && selectedNameOther!==selectedName && <span className="aerograph-cn">{selectedNameOther}</span>}
              <span className="aerograph-badges">
                {selectedRow.abstract && <Tag>Abstract</Tag>}
                <Tag>{selectedRow.count ?? '—'} in workspace</Tag>
              </span>
            </div>
            {selectedRow.description && <p className="aerograph-desc">{selectedRow.description}</p>}
          </div>}
          <TypeGraph
            selected={selected!}
            types={byId}
            relations={typeRelations}
            locale={locale}
            onSelect={setSelected}
          />
          <div className="aerograph-panel">
            <h3>Fields</h3>
            <p className="aerograph-hint">Includes fields inherited through the is_a ancestry. Writers come from the current workspace bindings.</p>
            {typeFields.length === 0
              ? <p className="aerograph-hint">No declared field on this type or its ancestors.</p>
              : <Table
                rowKey="id"
                size="small"
                columns={[...columns]}
                dataSource={typeFields}
                pagination={{ pageSize: 8, hideOnSinglePage: true }}
                locale={{ emptyText: 'No declared fields' }}
              />}
          </div>
          <div className="aerograph-panel">
            <h3>Relations</h3>
            {typeRelations.length === 0
              ? <p className="aerograph-hint">No declared relation touches this type.</p>
              : <ul className="aerograph-references">
                {typeRelations.map((row, index) => {
                  const source = row['sourceClass'] ?? row['source_type'] ?? row['source'];
                  const target = row['targetClass'] ?? row['target_type'] ?? row['target'];
                  const name = typeof row['displayName'] === 'string' ? row['displayName'] : typeof row['id'] === 'string' ? row['id'] : `relation ${index + 1}`;
                  const kind = typeof row['kind'] === 'string' ? row['kind'] : '';
                  return (
                    <li key={index}>
                      <span className="aerograph-ref-kind">{kind && <Tag>{readableLabel(kind)}</Tag>}</span>
                      <span className="aerograph-ref-name">{readableLabel(String(name))}</span>
                      <span className="aerograph-ref-desc">
                        {typeof source==='string'?typeLabel(byId.get(source),source):'Undeclared subject'} → {typeof target==='string'?typeLabel(byId.get(target),target):'Undeclared object'}
                      </span>
                    </li>
                  );
                })}
              </ul>}
          </div>
          <div className="aerograph-panel">
            <h3>Predicates, events and chains</h3>
            {references.length === 0
              ? <p className="aerograph-hint">No predicate, event or chain definition references this type.</p>
              : <ul className="aerograph-references">
                {references.map(reference => (
                  <li key={`${reference.kind}/${reference.id}`}>
                    <span className="aerograph-ref-kind"><Tag>{reference.kind}</Tag></span>
                    <span className="aerograph-ref-name">{reference.name || readableLabel(reference.id)}</span>
                    <span className="aerograph-ref-desc">{reference.description}</span>
                    <Details title={reference.name || readableLabel(reference.id)} buttonLabel="Definition">
                      <pre className="aerograph-details-pre">{JSON.stringify(reference.definition, null, 2)}</pre>
                    </Details>
                  </li>
                ))}
              </ul>}
          </div>
          <div className="aerograph-panel">
            <h3>Workspace entities of this type</h3>
            {entities.length === 0
              ? <p className="aerograph-hint">The selected workspace declares no entity of this type.</p>
              : <ul className="aerograph-entities">
                {entities.map(entity => (
                  <li key={entity.id}>
                    <Link className="console-btn" to={`/studio?api=${encodeURIComponent(base)}&workspace=${encodeURIComponent(workspaceId ?? '')}&entity=${encodeURIComponent(entity.id)}`}>{entity.label===entity.id?readableLabel(entity.id):entity.label}</Link>

                  </li>
                ))}
              </ul>}
          </div>
        </div>
      </div>
    </div>}
  </div>;
}
