import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Collapse, Drawer, Input, Popconfirm, Select, Space, Spin, Table, Tag } from 'antd';
import { useI18n } from '../../i18n/I18nProvider';
import { TypedPayloadForm } from '../../behaviours/TypedPayloadForm';
import { Details } from '../../console/Details';
import { applicableFields, isA } from '../../behaviours/model';
import { readableName, registryLabel } from '../guided-model';
import type { StudioApi, TypeDetail, TypeRow } from '../api';

type Row = Record<string, any>;
type Json = any;
const INT = '$integer';
const BG = 'background';
interface FieldDefLike extends Record<string, unknown> { id: string; type?: string; schema?: Json; metadata?: Json }
export interface EntitiesStepProps {
  initialEntityId?: string;
  scenario: Row;
  onChange: (scenario: Row) => void;
  api: StudioApi;
  types?: TypeRow[];
  fields?: FieldDefLike[];
  schemas?: Record<string, any>;
  onValidityChange?: (valid: boolean) => void;
}
const ancestors = (type: string, types: Array<{ id: string; parents: string[] }>): string[] => {
  const byId = new Map(types.map(row => [row.id, row])), out: string[] = [];
  const walk = (id: string): void => { if (!out.includes(id)) { out.push(id); (byId.get(id)?.parents ?? []).forEach(walk); } };
  walk(type); return out;
};
const typeLabel = (id: string, types: Array<{ id: string; name?: string }>, locale: string): string => {
  const names = types.find(row => row.id === id)?.name?.split('/').map(name => name.trim());
  const preferred = names?.find(name => locale === 'zh-CN' ? /[\u3400-\u9fff]/.test(name) : !/[\u3400-\u9fff]/.test(name));
  return registryLabel(preferred || (locale === 'zh-CN' ? names?.[0] : undefined) || id, locale);
};
const fieldLabel = (id: string, fields: FieldDefLike[], locale: string): string => {
  const field = fields.find(item => item.id === id);
  const display = field?.metadata?.raw?.displayName ?? field?.metadata?.displayName ?? field?.metadata?.sourceDisplayName;
  if (typeof display !== 'string') return registryLabel(id, locale);
  const names = /[\u3400-\u9fff]/.test(display) ? display.split('/').map(name => name.trim()) : [display];
  const preferred = names.find(name => locale === 'zh-CN' ? /[\u3400-\u9fff]/.test(name) : !/[\u3400-\u9fff]/.test(name));
  return registryLabel(preferred || (locale === 'zh-CN' ? names[0] : id), locale);
};

/** Known scalar schemas get typed editing; unsupported structures stay intact and visible. */
function FactEditor({ value, schema, schemas, label, onWrite, onValidityChange }: {
  value: Json; schema?: Json; schemas: Record<string, any>; label: string; onWrite: (value: Json) => void; onValidityChange: (valid: boolean) => void;
}) {
  const { t } = useI18n();
  const validity = useRef(onValidityChange); validity.current = onValidityChange;
  useEffect(() => () => validity.current(true), []);
  const resolved = typeof schema === 'string' ? schemas[schema] : schema?.schema_ref ? schemas[schema.schema_ref] : schema;
  const inferred = value && typeof value === 'object' && !Array.isArray(value) && value[INT] !== undefined ? 'integer'
    : value && typeof value === 'object' && !Array.isArray(value) && value.$number !== undefined ? 'number' : typeof value;
  const descriptor = resolved ?? (['string', 'boolean', 'number'].includes(inferred) ? { type: inferred } : undefined);
  if (['string', 'boolean', 'number', 'integer'].includes(descriptor?.type)) {
    return <TypedPayloadForm schema={descriptor} definitions={schemas} value={value} path={label} onChange={onWrite} onValidityChange={onValidityChange} />;
  }
  if (value === null || value === undefined) return <code>{value === null ? t('Explicit null') : t('Unset')}</code>;
  if (descriptor?.type === 'vector') {
    if (!Array.isArray(value)) return <span className="guided-fact-raw">{t('Not set — no synthetic origin/zeros')}</span>;
    return <span className="guided-fact-vector">{value.map((coordinate, index) => <Input key={index} aria-label={t('{field} coordinate {number}', { field: label, number: index + 1 })} type="number" step="any" className="guided-fact-number" value={coordinate ?? ''} onChange={event => {
      const next = Number(event.target.value); if (event.target.value !== '' && Number.isFinite(next)) { const coordinates = value.slice(); coordinates[index] = next; onWrite(coordinates); }
    }} />)}</span>;
  }
  if (descriptor?.type === 'ref' || value?.$ref) return <span>{t('Reference')} · {value?.$ref?.id ? readableName(value.$ref.id) : t('Not resolved')} <Details title={t('Entity reference')} buttonLabel={t('Reference details')}><pre>{JSON.stringify(value?.$ref ?? value, null, 2)}</pre></Details></span>;
  return <Details title={t('Field value')} buttonLabel={t('View raw value')}><pre>{JSON.stringify(value, null, 2)}</pre></Details>;
}

/** Registry-backed editing preserves unknown scenario sections and authored facts. */
export function EntitiesStep({ scenario, onChange, api, initialEntityId, types: catalogTypes, fields: catalogFields, schemas: catalogSchemas, onValidityChange }: EntitiesStepProps) {
  const { t, locale } = useI18n();
  const [query, setQuery] = useState(''), [hideBackground, setHideBackground] = useState(false);
  const [selected, setSelected] = useState<{ id: string; type: string } | null>(() => initialEntityId ? (scenario.entities as Array<{ id: string; type: string }> | undefined)?.find(entity => entity.id === initialEntityId) ?? null : null);
  const [detail, setDetail] = useState<TypeDetail>(), [detailBusy, setDetailBusy] = useState(false), [detailError, setDetailError] = useState('');
  const [newType, setNewType] = useState(''), [newId, setNewId] = useState(''), [addError, setAddError] = useState('');
  const [newField, setNewField] = useState<string>(), [fieldValue, setFieldValue] = useState<unknown>(), [fieldValid, setFieldValid] = useState(true);
  const [factValidity, setFactValidity] = useState<Record<string, boolean>>({});
  const scenarioTypes: TypeRow[] = scenario?.registry?.types ?? [];
  const types = useMemo(() => [...new Map([...(catalogTypes ?? []), ...scenarioTypes].map(row => [row.id, row])).values()], [catalogTypes, scenarioTypes]);
  const fields = useMemo(() => [...new Map([...(catalogFields ?? []), ...(scenario?.registry?.fields ?? [])].map((row: any) => [row.id, row])).values()] as FieldDefLike[], [catalogFields, scenario?.registry?.fields]);
  const schemas: Record<string, any> = { ...catalogSchemas, ...scenario?.registry?.schemas };
  const relations: any[] = scenario?.registry?.relations ?? [];
  const fieldById = useMemo(() => new Map(fields.map(field => [field.id, field])), [fields]);
  const typeById = useMemo(() => new Map(types.map(type => [type.id, type])), [types]);
  const roleFieldIds = useMemo(() => fields.filter(field => field.id === 'traffic.actor.role' || Array.isArray(field?.schema?.enum) && field.schema.enum.includes(BG)).map(field => field.id), [fields]);
  const isBackground = (entity: Row) => roleFieldIds.some(id => entity?.facts?.[id] === BG);
  const entities: Row[] = Array.isArray(scenario?.entities) ? scenario.entities : [];
  const knownTypeIds = useMemo(() => new Set(types.map(type => type.id)), [types]);
  const existingFactKeys = new Set(entities.flatMap(entity => Object.keys(entity.facts ?? {}).map(field => `${entity.id}/${field}`)));
  const valid = Object.entries(factValidity).every(([key, valid]) => !existingFactKeys.has(key) || valid);
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
  const groups = useMemo(() => {
    const result = new Map<string, Row[]>(), search = query.toLowerCase();
    entities.forEach(entity => {
      if (hideBackground && isBackground(entity)) return;
      if (search && !`${entity.id} ${readableName(String(entity.id))} ${entity.type ?? ''} ${typeLabel(String(entity.type ?? ''), types, locale)}`.toLowerCase().includes(search)) return;
      const key = String(entity.type ?? ''), rows = result.get(key) ?? []; rows.push(entity); result.set(key, rows);
    });
    return [...result.entries()].sort((left, right) => left[0].localeCompare(right[0]));
  }, [entities, query, hideBackground, roleFieldIds, types, locale]);
  useEffect(() => {
    if (!selected) { setDetail(undefined); setDetailError(''); return; }
    let active = true; setDetailBusy(true); setDetailError(''); setDetail(undefined);
    const local = types.find(row => row.id === selected.type);
    if (local) {
      const inherited = applicableFields(selected.type, types, fields as Record<string, unknown>[]);
      setDetail({ id: local.id, parents: ancestors(local.id, types).slice(1), abstract: local.abstract, fields: inherited.map(row => ({ id: String(row.id), declaring_type: String(row.declaring_type ?? row.type), schema: row.schema, metadata: row.metadata })) });
      setDetailBusy(false); return;
    }
    api.request<TypeDetail>(`/v1/studio/types/${encodeURIComponent(selected.type)}`).then(value => { if (active) setDetail(value); }).catch(problem => { if (active) setDetailError(String(problem)); }).finally(() => { if (active) setDetailBusy(false); });
    return () => { active = false; };
  }, [selected, api, types, fields]);
  const setFact = (id: string, key: string, value: Json) => onChange({ ...scenario, entities: entities.map(entity => {
    if (entity.id !== id) return entity;
    const facts = { ...entity.facts, [key]: value }; if (value === undefined) delete facts[key];
    return { ...entity, facts };
  }) });
  const removeFact = (id: string, key: string) => onChange({ ...scenario, entities: entities.map(entity => { if (entity.id !== id) return entity; const facts = { ...entity.facts }; delete facts[key]; return { ...entity, facts }; }) });
  const removeEntity = (id: string) => { onChange({ ...scenario, entities: entities.filter(entity => entity.id !== id) }); if (selected?.id === id) setSelected(null); };
  const addEntity = () => {
    setAddError(''); const id = newId.trim();
    if (!newType || !id) { setAddError(t('Choose a real registry type and provide an entity ID')); return; }
    if (entities.some(entity => entity.id === id)) { setAddError(t('Entity ID "{id}" already exists in this scenario', { id })); return; }
    if (!knownTypeIds.has(newType)) { setAddError(t('Type must come from the scenario registry')); return; }
    if (typeById.get(newType)?.abstract) { setAddError(t('Abstract types cannot be instantiated')); return; }
    onChange({ ...scenario, entities: [...entities, { id, type: newType, facts: {} }] }); setNewId(''); setNewType('');
  };
  const relationsFor = (type: string) => relations.filter(relation => typeof relation.source_type === 'string' && isA(type, relation.source_type, types) || typeof relation.target_type === 'string' && isA(type, relation.target_type, types));
  const detailFields = useMemo(() => !selected || !detail ? [] : detail.fields.map(field => ({ ...field, isInitial: Object.prototype.hasOwnProperty.call(entities.find(entity => entity.id === selected.id)?.facts ?? {}, field.id) })), [detail, selected, entities]);
  const editor = (field: { id: string; schema?: unknown }) => {
    const id = selected!.id, key = `${id}/${field.id}`;
    return <FactEditor value={entities.find(entity => entity.id === id)?.facts?.[field.id]} schema={field.schema} schemas={schemas} label={fieldLabel(field.id, fields, locale)} onWrite={value => setFact(id, field.id, value)} onValidityChange={valid => setFactValidity(current => current[key] === valid ? current : { ...current, [key]: valid })} />;
  };
  const removeFieldButton = (field: string) => <Button size="small" danger aria-label={t('Remove initial field {field}', { field: fieldLabel(field, fields, locale) })} onClick={() => removeFact(selected!.id, field)}>{t('Remove')}</Button>;
  return <div className="guided-entities-step">
    <Space wrap className="guided-entities-controls"><Input.Search aria-label={t('Filter entities by id or type')} allowClear placeholder={t('Search entities or types…')} value={query} onChange={event => setQuery(event.target.value)} className="guided-entities-search" /><Checkbox aria-label={t('Hide background actors')} checked={hideBackground} onChange={event => setHideBackground(event.target.checked)}>{t('Hide background actors')}</Checkbox><span className="guided-entities-total">{t('{entities} entities · {groups} visible type groups', { entities: entities.length, groups: groups.length })}</span></Space>
    <p className="guided-muted">{t('Background actors are explicitly marked in the draft. Drones or traffic generated by an engine appear after a run starts.')}</p>
    {groups.length === 0 && <p className="guided-entities-empty">{entities.length ? t('No entities match the current filter.') : t('No entities yet. Add one from a registry type below, or place it in Scene.')}</p>}
    <Collapse className="guided-entities-groups" items={groups.map(([typeId, rows]) => ({
      key: typeId, label: <span className="guided-entities-group-label">{typeId ? typeLabel(typeId, types, locale) : t('Unknown type')} <Tag>{rows.length}</Tag></span>,
      children: <Table size="small" rowKey="id" pagination={rows.length > 10 ? { pageSize: 10 } : false} dataSource={rows} columns={[
        { title: t('Entity'), dataIndex: 'id', render: readableName },
        { title: t('Initial fields'), key: 'fields', render: (_, entity) => Object.keys(entity?.facts ?? {}).length },
        { title: t('Actions'), key: 'actions', render: (_, entity) => <Space size="small"><Button size="small" onClick={() => { setSelected({ id: entity.id, type: entity.type }); setNewField(undefined); setFieldValue(undefined); }}>{t('Inspect')}</Button><Popconfirm title={t('Remove this entity?')} description={t('Rules and relationships may still refer to it. Validate the draft after removing it.')} okText={t('Remove')} cancelText={t('Cancel')} onConfirm={() => removeEntity(entity.id)}><Button size="small" danger>{t('Remove')}</Button></Popconfirm></Space> },
      ]} />,
    }))} />
    <section className="guided-entities-add" aria-label={t('Add entity')}><h4>{t('Add entity from registry type')}</h4><p className="guided-muted">{t('New entities start without initial values. Inspect them to add fields; use Scene to place an actor with movement settings filled in.')}</p><Space wrap><Select aria-label={t('Registry type picker')} showSearch optionFilterProp="label" placeholder={t('Choose an AeroGraph type')} value={newType || undefined} options={types.map(row => ({ value: row.id, label: typeLabel(row.id, types, locale), disabled: row.abstract }))} onChange={setNewType} className="guided-entities-type-picker" /><Input aria-label={t('New entity ID')} placeholder={t('Entity ID')} value={newId} onChange={event => setNewId(event.target.value)} className="guided-entities-id-input" /><Button disabled={!newType || !newId.trim() || entities.some(entity => entity.id === newId.trim())} onClick={addEntity}>{t('Add entity')}</Button></Space>{entities.some(entity => entity.id === newId.trim()) && <Alert type="warning" message={t('Entity ID "{id}" already exists in this scenario', { id: newId.trim() })} />}{addError && <Alert type="error" showIcon message={addError} />}</section>
    {selected && <Drawer closable={{ 'aria-label': t('Close') }} width={650} open title={readableName(selected.id)} onClose={() => setSelected(null)} rootClassName="guided-entity-drawer"><section className="guided-entities-detail" aria-label={t('Selected entity detail')}><Tag>{typeLabel(selected.type, types, locale)}</Tag>
      {detailBusy && <Spin size="small" tip={t('Loading entity fields')} />}{detailError && <Alert type="warning" showIcon message={t('Registry detail unavailable: {error}', { error: detailError })} />}
      {detail && <><p className="guided-entities-ancestry">{t('Type ancestry:')} {detail.parents.length ? detail.parents.map(id => <Tag key={id}>{typeLabel(id, types, locale)}</Tag>) : <em>{t('no parents declared')}</em>}{detail.abstract ? <Tag>{t('abstract')}</Tag> : null}</p>
        <h5>{t('Initial fields (declared by registry)')}</h5><Table size="small" rowKey="id" pagination={{ pageSize: 8 }} dataSource={detailFields.filter(row => row.isInitial)} locale={{ emptyText: t('No initial fields set. Add an inherited field below.') }} columns={[
          { title: t('Field'), dataIndex: 'id', render: (id: string) => fieldLabel(id, fields, locale) },
          { title: t('Type'), dataIndex: 'declaring_type', render: (id: string) => typeLabel(id, types, locale) },
          { title: t('In this entity'), dataIndex: 'isInitial', render: (present: boolean) => present ? <Tag color="blue">{t('present')}</Tag> : <Tag>{t('not set')}</Tag> },
          { title: t('Value'), key: 'value', render: (_, field) => editor(field) },
          { title: t('Actions'), key: 'actions', render: (_, field) => removeFieldButton(field.id) },
        ]} />
        <section className="guided-field-add"><h5>{t('Add an initial field')}</h5><Select aria-label={t('New initial field')} showSearch optionFilterProp="label" placeholder={t('Choose an inherited field')} value={newField} options={detail.fields.filter(row => !Object.prototype.hasOwnProperty.call(entities.find(entity => entity.id === selected.id)?.facts ?? {}, row.id)).map(row => ({ value: row.id, label: fieldLabel(row.id, fields, locale) }))} onChange={id => { setNewField(id); setFieldValue(undefined); setFieldValid(true); }} />{newField && <TypedPayloadForm key={newField} schema={detail.fields.find(row => row.id === newField)?.schema} definitions={schemas} value={fieldValue} path={t('Initial field value')} onChange={setFieldValue} onValidityChange={setFieldValid} />}<Button disabled={!newField || fieldValue === undefined || !fieldValid} onClick={() => { setFact(selected.id, newField!, fieldValue); setNewField(undefined); setFieldValue(undefined); }}>{t('Add initial field')}</Button></section>
        <h5>{t('Relations touching this type')}</h5>{relationsFor(selected.type).length ? <Table size="small" rowKey={(row, index) => `${row.relationId ?? row.id ?? index}`} pagination={false} dataSource={relationsFor(selected.type)} columns={[
          { title: t('Relation'), key: 'relation', render: (_, row) => registryLabel(String(row.name ?? row.relationId ?? row.id), locale) },
          { title: t('Source type'), key: 'source', render: (_, row) => typeLabel(row.source_type, types, locale) },
          { title: t('Target type'), key: 'target', render: (_, row) => typeLabel(row.target_type, types, locale) },
        ]} /> : <p className="guided-entities-empty">{t('No relations declared for this type in the scenario registry.')}</p>}
        <Details title={t('Additional authored fields')} buttonLabel={t('Additional fields')}><Table size="small" rowKey="key" pagination={{ pageSize: 8 }} dataSource={Object.entries(entities.find(entity => entity.id === selected.id)?.facts ?? {}).filter(([key]) => !detail.fields.some(row => row.id === key)).map(([key, value]) => ({ key, value }))} columns={[
          { title: t('Field'), dataIndex: 'key', render: (id: string) => fieldLabel(id, fields, locale) },
          { title: t('Value'), dataIndex: 'value', render: (_, row) => editor({ id: row.key, schema: fieldById.get(row.key)?.schema }) },
          { title: t('Actions'), key: 'actions', render: (_, row) => removeFieldButton(row.key) },
        ]} /></Details>
        <Details title={t('Entity raw JSON')} buttonLabel={t('Entity JSON')}><pre>{JSON.stringify(entities.find(entity => entity.id === selected.id), null, 2)}</pre></Details><div className="guided-entities-detail-actions"><Button onClick={() => setSelected(null)}>{t('Close entity detail')}</Button></div>
      </>}
    </section></Drawer>}
  </div>;
}
export default EntitiesStep;
