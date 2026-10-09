import { useMemo, useState } from 'react';
import { Alert, Button, Card, Input, Select, Space, Table, Tag } from 'antd';
import { Ast, Roles, AuthoringScope } from '../../behaviours/BehaviourEditor';
import { readableName } from '../guided-model';
import { patch } from '../../behaviours/model';
import { ExpressionControl } from '../../behaviours/ExpressionControl';
import { mapping } from '../../behaviours/model';
import { Details } from '../../console/Details';
import type { StudioApi, TypeRow, Workspace } from '../api';


/** Shared guided-step contract: steps edit the same real scenario draft the root Studio owns. */
export interface GuidedStepProps {
  workspace: Workspace;
  api: StudioApi;
  save: () => Promise<Workspace>;
  onApply: (workspace: Workspace) => void;
  onChange: (scenario: Record<string, any>) => void;
}
export interface CatalogProps {
  types: TypeRow[];
  fields: Record<string, unknown>[];
  relations: Record<string, unknown>[];
}

type Package = Record<string, unknown>;

export function packagesOf(scenario: Record<string, any>): Package[] {
  return Array.isArray(scenario.behaviours) ? scenario.behaviours as Package[] : [];
}

/** Inline behaviour packages only; pinned path references stay read-only until exported. */
export function isInline(value: Package | undefined): boolean {
  return !!value && mapping(value) && !('path' in value);
}

export function predicateEntries(value: Package | undefined): Array<[string, Draft]> {
  const predicates = mapping(value?.predicates) ? value!.predicates as Draft : {};
  return Object.entries(predicates).filter(([, row]) => mapping(row)) as Array<[string, Draft]>;
}
export function chainEntries(value: Package | undefined): Array<[string, Draft]> {
  const chains = mapping(value?.chains) ? value!.chains as Draft : {};
  return Object.entries(chains).filter(([, row]) => mapping(row)) as Array<[string, Draft]>;
}
type Draft = Record<string, unknown>;

/** Every real reference to a predicate inside the same package: chain triggers, conflicts, bindings chain links. */
export function predicateUses(value: Package | undefined, predicate: string): string[] {
  const uses: string[] = [];
  for (const [chainId, chain] of chainEntries(value)) {
    const trigger = mapping(chain.trigger) ? chain.trigger : {};
    if (trigger.predicate === predicate) uses.push(`chain ${chainId} · trigger ${String(trigger.edge ?? 'edge')}`);
    for (const transition of Array.isArray(chain.transitions) ? chain.transitions as Draft[] : [])
      if (mapping(transition) && mapping(transition.on) && (transition.on as Draft).predicate === predicate)
        uses.push(`chain ${chainId} · transition ${String(transition.id)}`);
  }
  for (const conflict of Array.isArray(value?.conflicts) ? value!.conflicts as Draft[] : [])
    if (mapping(conflict) && conflict.predicate === predicate) uses.push(`conflict ${String(conflict.id)}`);
  return uses;
}

interface Props extends GuidedStepProps, CatalogProps {onValidityChange?:(valid:boolean)=>void}
type Row = { expression:string; key: string; pkg: number; id: string; profile: string; roles: string[]; parameters: string[]; uses: string[] };

/** Guided predicate authoring over the actual inline behaviour packages: readable table plus real AST controls. */
export function PredicatesStep({ workspace, api, save, onApply, onChange, types, fields, relations, scenario, onValidityChange }: Props & { scenario: Record<string, any> }) {
  const packages = packagesOf(scenario);
  const inline = packages.map((value, pkg) => ({ value, pkg })).filter(row => isInline(row.value));
  const [selected, setSelected] = useState(''), [newId, setNewId] = useState(''), [newProfile, setNewProfile] = useState('committed_reactive/v1'), [error, setError] = useState('');
  const rows: Row[] = useMemo(() => inline.flatMap(({ value, pkg }) => predicateEntries(value).map(([id, row]) => ({
    key: `${pkg}/${id}`, pkg, id, expression: expressionLabel(row.expression),
    profile: String(row.profile ?? ''),
    roles: Object.entries(mapping(row.roles) ? row.roles : {}).map(([role, type]) => `${role}: ${String(type)}`),
    parameters: Object.keys(mapping(row.parameters) ? row.parameters : {}).map(readableName),
    uses: predicateUses(value, id).map(readableName),
  }))), [inline]);
  const active = rows.find(row => row.key === selected) ?? rows[0];
  const change = (pkg: number, next: Package) => onChange({ ...scenario, behaviours: packages.map((row, index) => index === pkg ? next : row) });
  const current = active ? packages[active.pkg] : undefined;
  const draft = active && current && mapping(current.predicates) ? (current.predicates as Draft)[active.id] as Draft : undefined;
  const update = (path: string[], next: unknown) => {
    if (!active || !current) return;
    const cloned = patch(current,path,next) as Package;
    change(active.pkg, cloned);
  };
  if (scenario.behaviours !== undefined && !Array.isArray(scenario.behaviours))
    return <Alert type="warning" showIcon message="Unsupported behaviour collection retained in scenario JSON; correct its list shape in the Scenario editor." />;
  return <AuthoringScope onValidityChange={onValidityChange}><div className="guided-root">
    <Card size="small" title="Scenario predicates" className="guided-card">
      <p className="guided-muted">Conditions are evaluated against AeroGraph state. Predicate transitions can trigger event chains and actions.</p>
      <Table size="small" rowKey="key" pagination={{pageSize:6}} className="guided-table"
        rowSelection={{ type: 'radio', selectedRowKeys: active ? [active.key] : [], onChange: keys => setSelected(String(keys[0] ?? '')) }}
        dataSource={rows}
        columns={[
          { title: 'Predicate', dataIndex: 'id', render: (id:string)=>readableName(id) },
          {title:'Expression',dataIndex:'expression',render:(text:string)=><span className="guided-expression-summary" title={text}>{text}</span>},
          
          { title: 'Profile', dataIndex: 'profile', render: (value: string) => <Tag>{value || 'unset'}</Tag> },
          { title: 'Applies to', dataIndex: 'roles', render: (value: string[]) => value.map(readableName).join(', ') || 'No roles' },
          { title: 'Parameters', dataIndex: 'parameters', render: (value: string[]) => value.join(', ') || '—' },
          { title: 'Chain / conflict uses', dataIndex: 'uses', render: (value: string[]) => value.length ? <ul className="guided-uses">{value.map((use, index) => <li key={index}>{use}</li>)}</ul> : <Tag>unused</Tag> },
        ]}
        expandable={{ expandedRowRender: () => null, rowExpandable: () => false }} locale={{ emptyText: 'No inline packages carry predicates yet.' }} />
      {packages.length !== inline.length && <Alert type="info" showIcon message={`${packages.length - inline.length} pinned path reference package(s) are shown in Rules; export them to inline drafts there before editing predicates.`} />}
      {active && draft && current && <>
        <Space wrap className="guided-actions">
          <Input aria-label="New predicate ID" placeholder="traffic.p.new" value={newId} onChange={event => setNewId(event.target.value)} className="guided-predicate-id" />
          <Select aria-label="New predicate profile" value={newProfile} className="guided-profile-select"
            onChange={value => setNewProfile(value)} options={['committed_reactive/v1', 'aerograph_sampled/v1'].map(value => ({ value }))} />
          <Button disabled={!newId || newId in (mapping(current.predicates) ? current.predicates as Draft : {})}
            onClick={() => { const predicates = mapping(current.predicates) ? current.predicates as Draft : {}; change(active.pkg, { ...current, predicates: { ...predicates, [newId]: { profile: newProfile, roles: {}, expression: { literal: null }, parameters: {} } } }); setSelected(`${active.pkg}/${newId}`); setNewId(''); }}>
            Add predicate to {String(current.id ?? `package ${active.pkg}`)}</Button>
        </Space>
        <fieldset className="guided-fieldset"><legend>Edit {readableName(active.id)}</legend>
          <Space wrap>
            <label className="guided-label">Evaluation profile
              <Select aria-label="Predicate evaluation profile" value={String(draft.profile ?? '')} className="guided-profile-select"
                onChange={value => update(['predicates', active.id, 'profile'], value)}
                options={[{ value: '', label: 'Unset' }, { value: 'committed_reactive/v1' }, { value: 'aerograph_sampled/v1' }]} /></label>
            <span className="guided-muted">Registry catalog: {types.length} types · {fields.length} fields · {relations.length} relations constrain role and field picks in the AST controls.</span>
          </Space>
          <Roles label="Predicate roles" value={draft.roles} types={types} onChange={next => update(['predicates',active.id,'roles'],next)}/>
          <Ast node={draft.expression} path="Predicate expression" roles={mapping(draft.roles)?draft.roles:{}} types={types} fields={fields} relations={relations} onChange={next=>update(['predicates',active.id,'expression'],next)}/>
          <ExpressionControl label="parameters" value={draft.parameters} onChange={next => update(['predicates', active.id, 'parameters'], next)} />
          <Details title={`${active.id} predicate JSON`} buttonLabel="Predicate JSON"><pre>{JSON.stringify(draft, null, 2)}</pre></Details>
          <Details title={`Package ${String(current.id ?? active.pkg)} JSON`} buttonLabel="Package JSON"><pre>{JSON.stringify(current, null, 2)}</pre></Details>
        </fieldset>
      </>}
      {error && <Alert type="error" showIcon message={error} />}
    </Card>
  </div></AuthoringScope>;
}

export function expressionLabel(value:unknown):string {
 if(!mapping(value)) return 'Unsupported expression';
 if('literal' in value) { const literal=value.literal;return mapping(literal)&&('$number' in literal || '$integer' in literal)?String(literal.$number ?? literal.$integer):typeof literal==='object'?JSON.stringify(literal):String(literal); }
 if(typeof value.field==='string') return `${String(value.role ?? '')} · ${readableName(value.field)}`;
 if(typeof value.parameter==='string') return `Parameter ${readableName(value.parameter)}`;
 if(typeof value.op==='string') return `${readableName(value.op)}(${(Array.isArray(value.args)?value.args:[]).map(expressionLabel).join(', ')})`;
 if(typeof value.relation==='string') return `Related by ${readableName(value.relation)}`;
 return 'Advanced expression';
}
