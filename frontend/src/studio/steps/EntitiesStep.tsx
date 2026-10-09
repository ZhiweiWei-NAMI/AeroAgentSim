import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Checkbox, Collapse, Drawer, Input, Select, Space, Spin, Table, Tag } from 'antd';
import { TypedPayloadForm } from '../../behaviours/TypedPayloadForm';
import { Details } from '../../console/Details';
import { applicableFields, isA } from '../../behaviours/model';
import { readableName } from '../guided-model';
import type { StudioApi, TypeDetail, TypeRow } from '../api';

/**
 * EntitiesStep — Studio step that presents scenario entities grouped by readable AeroGraph type.
 *
 * Honesty rules:
 * - Only real scenario registry types/fields/relations are offered (scenario.registry plus the
 *   workspace registry catalog when provided through `types`/`fields` props by the host page).
 * - Type detail (initial fields, relations, ancestry) comes from the live Studio API.
 * - Unknown scenario content (unknown types, unknown fact keys, unknown scenario sections) is shown
 *   as-is and survives edits; nothing is fabricated, defaulted to zero, or silently dropped.
 */

type Row = Record<string, any>;
type Json = any;

const INT = '$integer'; // lossless tag convention from frontend/src/feeds/lossless-json.ts
const BG = 'background';

interface FieldDefLike extends Record<string,unknown> { id: string; type?: string; schema?: Json; metadata?: Json }
export interface EntitiesStepProps {
  scenario: Row;
  onChange: (scenario: Row) => void;
  api: StudioApi;
  /** Optional registry catalog rows the host page already fetched (still real server data). */
  types?: TypeRow[];
  fields?: FieldDefLike[];
  schemas?: Record<string,any>;
}

const ancestors = (type: string, types: Array<{ id: string; parents: string[] }>): string[] => {
  const byId = new Map(types.map(t => [t.id, t]));
  const out: string[] = [];
  const walk = (id: string): void => {
    if (out.indexOf(id) < 0) { out.push(id); (byId.get(id)?.parents ?? []).forEach(walk); }
  };
  walk(type);
  return out;
};

/** Readable name: type display name when the registry supplies one, else the raw type ID. */
const typeLabel = (id: string, types: Array<{ id: string; name?: string }>): string => {
  const row = types.find(t => t.id === id);
  const name = row?.name?.split('/')[0]?.trim();
  return name || readableName(id);
};

const fieldLabel = (id: string, fields: FieldDefLike[]): string => {
  const f = fields.find(item => item.id === id);
  const display = f?.metadata?.raw?.displayName ?? f?.metadata?.displayName ?? f?.metadata?.sourceDisplayName;
  return typeof display === 'string' && /[a-z]/i.test(display) && !/[\u3400-\u9fff]/.test(display) ? display : readableName(id);
};

const writeInt = (raw: string): Json | undefined => {
  if (!/^-?\d+$/.test(raw)) return undefined;
  const b = BigInt(raw);
  return b >= BigInt(Number.MIN_SAFE_INTEGER) && b <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(raw) : { [INT]: raw };
};

/** Field editor for a single initial field value. Unknown schema → read-only raw value, never guessed. */
function FactEditor({ value, schema, schemas, onWrite }: { value: Json; schema?: Json; schemas: Record<string, any>; onWrite: (v: Json) => void }) {
  const resolved = typeof schema === 'string' ? schemas[schema] : schema?.schema_ref ? schemas[schema.schema_ref] : schema;
  if (value === null || value === undefined) return <code>{value === null ? 'Explicit null' : 'Unset'}</code>;
  const num = (step: string, write: (s: string) => void) =>
    <Input type="number" step={step} className="guided-fact-number" value={typeof value === 'number' ? value : ''} onChange={e => write(e.target.value)} />;
  if (value && typeof value === 'object' && !Array.isArray(value) && value[INT] !== undefined)
    return <Input value={String(value[INT])} onChange={e => { const v = writeInt(e.target.value); if (v !== undefined) onWrite(typeof v === 'number' ? { [INT]: e.target.value } : v); }} />;
  const st = resolved?.type;
  if (st === 'string' || (st === undefined && typeof value === 'string')) {
    const en: string[] | undefined = resolved?.enum;
    if (en) return <Input value={String(value ?? '')} readOnly />;
    return <Input value={String(value ?? '')} onChange={e => onWrite(e.target.value)} />;
  }
  if(st==='boolean' && typeof value!=='boolean') return <Details title="Invalid Boolean value"><pre>{JSON.stringify(value)}</pre></Details>;
  if (st === 'boolean' || (st === undefined && typeof value === 'boolean'))
    return <Checkbox checked={value} onChange={e => onWrite(e.target.checked)}>{String(value)}</Checkbox>;
  if (st === 'integer' || (st === undefined && typeof value === 'number' && Number.isInteger(value)))
    return num('1', s => { const v = writeInt(s); if (v !== undefined) onWrite(v); });
  if (st === 'number' || (st === undefined && typeof value === 'number'))
    return num('any', s => { const v = Number(s); if (s !== '' && Number.isFinite(v)) onWrite(v); });
  if (st === 'vector') {
    if (!Array.isArray(value)) return <span className="guided-fact-raw">Not set — no synthetic origin/zeros</span>;
    const len = resolved?.length ?? value.length;
    return <span className="guided-fact-vector">{Array.from({ length: len }, (_, i) =>
      <Input key={i} type="number" step="any" className="guided-fact-number" value={value[i] ?? ''} onChange={e => {
        const v = Number(e.target.value);
        if (e.target.value !== '' && Number.isFinite(v)) { const next = value.slice(); next[i] = v; onWrite(next); }
      }} />)}</span>;
  }
  if (st === 'ref' || (value && typeof value === 'object' && !Array.isArray(value) && value.$ref))
    return <span>Reference · {value?.$ref?.id ? readableName(value.$ref.id) : 'Not resolved'} <Details title="Entity reference"><pre>{JSON.stringify(value?.$ref ?? value, null, 2)}</pre></Details></span>;
  // Unknown schema / arrays / nested objects: complete value shown read-only in Details.
  return <Details title="Field value" buttonLabel="View raw value"><pre>{JSON.stringify(value, null, 2)}</pre></Details>;
}

/** Readable root label per AeroGraph type family; unknown types keep their own ID as the group name. */
const groupTitle = (typeId: string, types: Array<{ id: string; name?: string }>): string => typeLabel(typeId, types);

export function EntitiesStep({ scenario, onChange, api, types: catalogTypes, fields: catalogFields, schemas: catalogSchemas }: EntitiesStepProps) {
  const [query, setQuery] = useState('');
  const [hideBackground, setHideBackground] = useState(false);
  const [selected, setSelected] = useState<{ id: string; type: string } | null>(null);
  const [detail, setDetail] = useState<TypeDetail>();
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [newType, setNewType] = useState('');
  const [newId, setNewId] = useState('');
  const [addError, setAddError] = useState('');
  const [newField,setNewField]=useState<string>(),[fieldValue,setFieldValue]=useState<unknown>(),[fieldValid,setFieldValid]=useState(true);

  const scenarioTypes: TypeRow[] = scenario?.registry?.types ?? [];
  const types = useMemo(() => {
    const merged = [...(catalogTypes ?? []), ...scenarioTypes];
    return [...new Map(merged.map(t => [t.id, t])).values()];
  }, [catalogTypes, scenarioTypes]);
  const fields = useMemo(() => {
    const merged = [...(catalogFields ?? []), ...(scenario?.registry?.fields ?? [])];
    return [...new Map(merged.map((f: any) => [f.id, f])).values()] as FieldDefLike[];
  }, [catalogFields, scenario?.registry?.fields]);
  const schemas: Record<string, any> = {...catalogSchemas,...scenario?.registry?.schemas};
  const relations: any[] = scenario?.registry?.relations ?? [];

  const fieldById = useMemo(() => new Map(fields.map(f => [f.id, f])), [fields]);
  const typeById = useMemo(() => new Map(types.map(t => [t.id, t])), [types]);

  // Background = ONLY an explicitly present role fact holding the background value; missing role → not background.
  const roleFieldIds = useMemo(() => fields
    .filter(f => f.id === 'traffic.actor.role' || (Array.isArray(f?.schema?.enum) && f.schema.enum.includes(BG)))
    .map(f => f.id), [fields]);
  const isBackground = (e: Row) => roleFieldIds.some(id => e?.facts?.[id] === BG);

  const entities: Row[] = scenario?.entities ?? [];
  const knownTypeIds = useMemo(() => new Set(types.map(t => t.id)), [types]);

  const groups = useMemo(() => {
    const m = new Map<string, Row[]>();
    entities.forEach(e => {
      if (hideBackground && isBackground(e)) return;
      if (query && !String(e.id).toLowerCase().includes(query.toLowerCase()) && !String(e.type ?? '').toLowerCase().includes(query.toLowerCase())) return;
      const key = String(e.type ?? 'unknown type');
      const arr = m.get(key) ?? []; arr.push(e); m.set(key, arr);
    });
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [entities, query, hideBackground, roleFieldIds]);

  useEffect(() => {
    if (!selected) { setDetail(undefined); setDetailError(''); return; }
    let active = true;
    setDetailBusy(true); setDetailError('');
    const local = types.find(row=>row.id===selected.type);
    if(local) {
      const inherited=applicableFields(selected.type,types,fields as Record<string,unknown>[]);
      setDetail({id:local.id,parents:ancestors(local.id,types).slice(1),abstract:local.abstract,fields:inherited.map(row=>({id:String(row.id),declaring_type:String(row.declaring_type ?? row.type),schema:row.schema,metadata:row.metadata}))});
      setDetailBusy(false);return;
    }
    api.request<TypeDetail>(`/v1/studio/types/${encodeURIComponent(selected.type)}`)
      .then(d => { if (active) setDetail(d); })
      .catch(e => { if (active) setDetailError(String(e)); })
      .finally(() => { if (active) setDetailBusy(false); });
    return () => { active = false; };
  }, [selected, api, types, fields]);

  const setFact = (eid: string, key: string, v: Json) =>
    onChange({ ...scenario, entities: entities.map(e => e.id === eid ? { ...e, facts: { ...e.facts, [key]: v } } : e) });

  const removeEntity = (eid: string) => onChange({ ...scenario, entities: entities.filter(e => e.id !== eid) });

  const addEntity = () => {
    setAddError('');
    const id = newId.trim();
    if (!newType || !id) { setAddError('Choose a real registry type and provide an entity ID'); return; }
    if (entities.some(e => e.id === id)) { setAddError(`Entity ID "${id}" already exists in this scenario`); return; }
    if (!knownTypeIds.has(newType)) { setAddError('Type must come from the scenario registry'); return; }
    const abstract = typeById.get(newType)?.abstract;
    if (abstract) { setAddError('Abstract types cannot be instantiated'); return; }
    // No fabricated initial fields: a new entity starts with empty facts.
    onChange({ ...scenario, entities: [...entities, { id, type: newType, facts: {} }] });
    setNewId(''); setNewType('');
  };

  const relationsFor = (typeId: string) => relations.filter(r => (typeof r.source_type==='string' && isA(typeId,r.source_type,types)) || (typeof r.target_type==='string' && isA(typeId,r.target_type,types)));

  const detailFields = useMemo(() => {
    if (!selected || !detail) return [];
    return detail.fields.map(f => ({ ...f, isInitial: Object.prototype.hasOwnProperty.call(entities.find(e => e.id === selected.id)?.facts ?? {}, f.id) }));
  }, [detail, selected, entities]);

  return <div className="guided-entities-step">
    <Space wrap className="guided-entities-controls">
      <Input.Search aria-label="Filter entities by id or type" allowClear placeholder="Search entities or types…" value={query} onChange={e => setQuery(e.target.value)} className="guided-entities-search" />
      <Checkbox aria-label="Hide background actors" checked={hideBackground} onChange={e => setHideBackground(e.target.checked)}>
        Hide background actors
      </Checkbox>
      <span className="guided-entities-total">{entities.length} entities · {groups.length} visible type groups</span>
    </Space>

    {groups.length === 0 && <p className="guided-entities-empty">No entities match the current filter.</p>}

    <Collapse className="guided-entities-groups" items={groups.map(([typeId, ents]) => ({
      key: typeId,
      label: <span className="guided-entities-group-label">{groupTitle(typeId, types)} <Tag>{ents.length}</Tag></span>,
      children: <Table
        size="small"
        rowKey="id"
        pagination={ents.length > 10 ? { pageSize: 10 } : false}
        dataSource={ents}
        columns={[
          { title: 'Entity', dataIndex: 'id', render:readableName },
          
          
          { title: 'Initial fields', key: 'fields', render: (_, e) => Object.keys(e?.facts ?? {}).length },
          {
            title: 'Actions', key: 'actions', render: (_, e) => <Space size="small">
              <Button size="small" onClick={() => {setSelected({ id: e.id, type: e.type });setNewField(undefined);setFieldValue(undefined);}}>Inspect</Button>
              <Button size="small" danger onClick={() => removeEntity(e.id)}>Remove</Button>
            </Space>,
          },
        ]}
      />,
    }))} />

    <section className="guided-entities-add" aria-label="Add entity">
      <h4>Add entity from registry type</h4>
      <Space wrap>
        <Select aria-label="Registry type picker" showSearch optionFilterProp="label" placeholder="Choose an AeroGraph type" value={newType || undefined} options={types.map(row=>({value:row.id,label:typeLabel(row.id,types),disabled:row.abstract}))} onChange={setNewType} className="guided-entities-type-picker"/>
        <Input aria-label="New entity ID" placeholder="Entity ID" value={newId} onChange={e => setNewId(e.target.value)} className="guided-entities-id-input" />
        <Button onClick={addEntity}>Add</Button>
      </Space>
      {addError && <Alert type="error" showIcon message={addError} />}
    </section>

    {selected && <Drawer width={650} open title={readableName(selected.id)} onClose={()=>setSelected(null)} rootClassName="guided-entity-drawer"><section className="guided-entities-detail" aria-label="Selected entity detail"><Tag>{typeLabel(selected.type,types)}</Tag>
      {detailBusy && <Spin size="small" />}
      {detailError && <Alert type="warning" showIcon message={`Registry detail unavailable: ${detailError}`} />}
      {detail && <>
        <p className="guided-entities-ancestry">
          Type ancestry: {detail.parents.length ? detail.parents.map(id => <Tag key={id}>{typeLabel(id, types)}</Tag>) : <em>no parents declared</em>}
          {detail.abstract ? <Tag>abstract</Tag> : null}
        </p>
        <h5>Initial fields (declared by registry)</h5>
        <Table
          size="small"
          rowKey="id"
          pagination={{pageSize:8}}
          dataSource={detailFields.filter(row=>row.isInitial)}
          locale={{ emptyText: 'This type declares no fields in the registry; the entity carries only its raw facts below.' }}
          columns={[
            { title: 'Field', dataIndex: 'id', render: (id: string) => fieldLabel(id, fields) },
            { title: 'Type', dataIndex: 'declaring_type',render:(id:string)=>typeLabel(id,types) },
            { title: 'In this entity', dataIndex: 'isInitial', render: (v: boolean) => (v ? <Tag color="blue">present</Tag> : <Tag>not set</Tag>) },
            {
              title: 'Value', key: 'value', render: (_, f) => {
                const eid = selected!.id;
                const entity = entities.find(e => e.id === eid);
                const current = entity?.facts?.[f.id];
                return <FactEditor value={current} schema={f.schema ?? f} schemas={schemas} onWrite={v => setFact(eid, f.id, v)} />;
              },
            },
          ]}
        />
        <section className="guided-field-add"><h5>Add an initial field</h5><Select aria-label="New initial field" showSearch optionFilterProp="label" placeholder="Choose an inherited field" value={newField} options={detail.fields.filter(row=>!Object.prototype.hasOwnProperty.call(entities.find(entity=>entity.id===selected.id)?.facts ?? {},row.id)).map(row=>({value:row.id,label:fieldLabel(row.id,fields)}))} onChange={id=>{setNewField(id);setFieldValue(undefined);}}/>{newField&&<TypedPayloadForm schema={detail.fields.find(row=>row.id===newField)?.schema} definitions={schemas} value={fieldValue} path="Initial field value" onChange={setFieldValue} onValidityChange={setFieldValid}/>}<Button disabled={!newField||fieldValue===undefined||!fieldValid} onClick={()=>{setFact(selected.id,newField!,fieldValue);setNewField(undefined);setFieldValue(undefined);}}>Add initial field</Button></section>
        <h5>Relations touching this type</h5>
        {relationsFor(selected.type).length
          ? <Table size="small" rowKey={(r, i) => `${r.relationId ?? r.id ?? i}`} pagination={false}
              dataSource={relationsFor(selected.type)}
              columns={[
                { title: 'Relation', key: 'relation', render: (_, r) => readableName(String(r.name ?? r.relationId ?? r.id)) },
                { title: 'Source type', key: 'source', render: (_, r) => typeLabel(r.source_type,types) },
                { title: 'Target type', key: 'target', render: (_, r) => typeLabel(r.target_type,types) },
              ]} />
          : <p className="guided-entities-empty">No relations declared for this type in the scenario registry.</p>}
        <Details title="Additional authored fields"><Table size="small" rowKey="key" pagination={{pageSize:8}} dataSource={Object.entries(entities.find(e=>e.id===selected.id)?.facts ?? {}).filter(([key])=>!detail.fields.some(row=>row.id===key)).map(([key,value])=>({key,value}))} columns={[{title:'Field',dataIndex:'key',render:(id:string)=>fieldLabel(id,fields)},{title:'Value',dataIndex:'value',render:(value:Json,row:any)=><FactEditor value={value} schema={fieldById.get(row.key)?.schema} schemas={schemas} onWrite={next=>setFact(selected.id,row.key,next)}/>}]} /></Details>
        <Details title="Entity raw JSON"><pre>{JSON.stringify(entities.find(e => e.id === selected.id), null, 2)}</pre></Details>
        <div className="guided-entities-detail-actions"><Button onClick={() => setSelected(null)}>Close entity detail</Button></div>
      </>}
    </section></Drawer>}
  </div>;
}

export default EntitiesStep;
