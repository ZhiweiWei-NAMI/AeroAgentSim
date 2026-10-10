import { useMemo, useState } from 'react';
import { Alert, Button, Card, Input, Popconfirm, Select, Space, Table, Tag } from 'antd';
import { useI18n } from '../../i18n/I18nProvider';
import { translate } from '../../i18n/translate';
import { Ast, Roles, AuthoringScope } from '../../behaviours/BehaviourEditor';
import { readableName, registryLabel } from '../guided-model';
import { patch, mapping } from '../../behaviours/model';
import { ExpressionControl } from '../../behaviours/ExpressionControl';
import { Details } from '../../console/Details';
import type { StudioApi, TypeRow, Workspace } from '../api';

export interface GuidedStepProps {
  workspace: Workspace;
  api: StudioApi;
  save: () => Promise<Workspace>;
  onApply: (workspace: Workspace) => void;
  onChange: (scenario: Record<string, any>) => void;
}
export interface CatalogProps { types: TypeRow[]; fields: Record<string, unknown>[]; relations: Record<string, unknown>[] }
type Package = Record<string, unknown>;
type Draft = Record<string, unknown>;
export function packagesOf(scenario: Record<string, any>): Package[] { return Array.isArray(scenario.behaviours) ? scenario.behaviours as Package[] : []; }
/** Pinned path references remain read-only until explicitly converted to an inline package. */
export function isInline(value: Package | undefined): boolean { return !!value && mapping(value) && !('path' in value); }
export function predicateEntries(value: Package | undefined): Array<[string, Draft]> {
  const predicates = mapping(value?.predicates) ? value!.predicates as Draft : {};
  return Object.entries(predicates).filter(([, row]) => mapping(row)) as Array<[string, Draft]>;
}
export function chainEntries(value: Package | undefined): Array<[string, Draft]> {
  const chains = mapping(value?.chains) ? value!.chains as Draft : {};
  return Object.entries(chains).filter(([, row]) => mapping(row)) as Array<[string, Draft]>;
}
export function predicateUses(value: Package | undefined, predicate: string, locale = 'en-US'): string[] {
  const uses: string[] = [];
  for (const [chain, row] of chainEntries(value)) {
    const trigger = mapping(row.trigger) ? row.trigger : {};
    const edge = trigger.edge === 'entered' ? translate(locale, 'becomes true') : trigger.edge === 'exited' ? translate(locale, 'becomes false') : trigger.edge === 'while' ? translate(locale, 'while true') : String(trigger.edge ?? '');
    if (trigger.predicate === predicate) uses.push(translate(locale, 'Chain {chain} · trigger {edge}', { chain, edge }));
    for (const transition of Array.isArray(row.transitions) ? row.transitions as Draft[] : [])
      if (mapping(transition) && mapping(transition.on) && transition.on.predicate === predicate) uses.push(translate(locale, 'Chain {chain} · transition {transition}', { chain, transition: String(transition.id) }));
  }
  for (const conflict of Array.isArray(value?.conflicts) ? value!.conflicts as Draft[] : [])
    if (mapping(conflict) && conflict.predicate === predicate) uses.push(translate(locale, 'Conflict {id}', { id: String(conflict.id) }));
  return uses;
}
interface Props extends GuidedStepProps, CatalogProps { onValidityChange?: (valid: boolean) => void }
type Row = { expression: string; key: string; pkg: number; id: string; profile: string; roles: string[]; parameters: string[]; uses: string[] };
export function PredicatesStep({ onChange, types, fields, relations, scenario, onValidityChange }: Props & { scenario: Record<string, any> }) {
  const { t, locale } = useI18n();
  const packages = packagesOf(scenario), inline = packages.map((value, pkg) => ({ value, pkg })).filter(row => isInline(row.value));
  const [selected, setSelected] = useState(''), [newId, setNewId] = useState(''), [newProfile, setNewProfile] = useState('committed_reactive/v1'), [newPackage, setNewPackage] = useState<number>();
  const profileOptions = [{ value: 'committed_reactive/v1', label: t('When committed state changes') }, { value: 'aerograph_sampled/v1', label: t('At simulation sample times') }];
  const profiles: Record<string, string> = Object.fromEntries(profileOptions.map(option => [option.value, option.label]));
  const rows: Row[] = useMemo(() => inline.flatMap(({ value, pkg }) => predicateEntries(value).map(([id, row]) => ({
    key: `${pkg}/${id}`, pkg, id, expression: expressionLabel(row.expression, locale), profile: String(row.profile ?? ''),
    roles: Object.entries(mapping(row.roles) ? row.roles : {}).map(([role, type]) => `${readableName(role)}: ${registryLabel(String(type), locale)}`),
    parameters: Object.keys(mapping(row.parameters) ? row.parameters : {}).map(readableName), uses: predicateUses(value, id, locale),
  }))), [scenario.behaviours, locale]);
  const active = rows.find(row => row.key === selected) ?? rows[0];
  const current = active ? packages[active.pkg] : undefined;
  const draft = active && current && mapping(current.predicates) ? current.predicates[active.id] as Draft : undefined;
  const target = inline.find(row => row.pkg === newPackage) ?? inline.find(row => row.pkg === active?.pkg) ?? inline[0];
  const change = (pkg: number, next: Package) => onChange({ ...scenario, behaviours: packages.map((row, index) => index === pkg ? next : row) });
  const update = (path: string[], next: unknown) => { if (active && current) change(active.pkg, patch(current, path, next) as Package); };
  const addPackage = () => {
    let id = 'conditions', number = 1; while (packages.some(pkg => pkg?.id === id)) id = `conditions-${++number}`;
    const next = { format: 'aeroagentsim.behaviour-package/v1', id, revision: 1, registry: { digest_ref: 'scenario.registry_digest' }, evaluator: { version: 'aerograph-predicate/1' }, budgets: { max_transitions_per_instance_per_ns: 64, max_instances: 10000 }, predicates: {}, chains: {}, bindings: [], conflicts: [], injection_points: [] };
    onChange({ ...scenario, behaviours: [...packages, next] }); setNewPackage(packages.length);
  };
  if (scenario.behaviours !== undefined && !Array.isArray(scenario.behaviours)) return <Alert type="warning" showIcon message={t('Unsupported behaviour collection retained in scenario JSON; correct its list shape in the Scenario editor.')} />;
  return <AuthoringScope onValidityChange={onValidityChange}><div className="guided-root"><Card size="small" title={t('Scenario predicates')} className="guided-card">
    <p className="guided-muted">{t('Conditions are evaluated against AeroGraph state. Predicate transitions can trigger event chains and actions.')}</p>
    <Table size="small" rowKey="key" pagination={{ pageSize: 6 }} className="guided-table" rowSelection={{ type: 'radio', selectedRowKeys: active ? [active.key] : [], onChange: keys => setSelected(String(keys[0] ?? '')) }} dataSource={rows} columns={[
      { title: t('Predicate'), dataIndex: 'id', render: (id: string) => readableName(id) },
      { title: t('Expression'), dataIndex: 'expression', render: (text: string) => <span className="guided-expression-summary" title={text}>{text}</span> },
      { title: t('Profile'), dataIndex: 'profile', render: (profile: string) => <Tag>{profiles[profile] ?? (profile || t('Unset'))}</Tag> },
      { title: t('Applies to'), dataIndex: 'roles', render: (roles: string[]) => roles.join(', ') || t('No roles') },
      { title: t('Parameters'), dataIndex: 'parameters', render: (parameters: string[]) => parameters.join(', ') || '—' },
      { title: t('Chain / conflict uses'), dataIndex: 'uses', render: (uses: string[]) => uses.length ? <ul className="guided-uses">{uses.map((use, index) => <li key={index}>{use}</li>)}</ul> : <Tag>{t('unused')}</Tag> },
    ]} locale={{ emptyText: t('No inline packages carry predicates yet.') }} />
    {packages.length !== inline.length && <Alert type="info" showIcon message={t('{count} file-reference packages are shown in Rules. Use Edit referenced package there before editing their predicates.', { count: packages.length - inline.length })} />}
    {target ? <Space wrap className="guided-actions">
      <Select aria-label={t('New predicate package')} value={target.pkg} options={inline.map(({ value, pkg }) => ({ value: pkg, label: String(value.id ?? t('Package {number}', { number: pkg + 1 })) }))} onChange={setNewPackage} />
      <Input aria-label={t('New predicate ID')} placeholder={t('e.g. new-condition')} value={newId} onChange={event => setNewId(event.target.value)} className="guided-predicate-id" />
      <Select aria-label={t('New predicate profile')} value={newProfile} className="guided-profile-select" onChange={setNewProfile} options={profileOptions} />
      <Button disabled={!newId.trim() || newId.trim() in (mapping(target.value.predicates) ? target.value.predicates : {})} onClick={() => {
        const id = newId.trim(), predicates = mapping(target.value.predicates) ? target.value.predicates : {};
        change(target.pkg, { ...target.value, predicates: { ...predicates, [id]: { profile: newProfile, roles: {}, expression: { literal: null }, parameters: {} } } }); setSelected(`${target.pkg}/${id}`); setNewId('');
      }}>{t('Add predicate to {package}', { package: String(target.value.id ?? t('Package {number}', { number: target.pkg + 1 })) })}</Button>
    </Space> : <><p>{t('Start by adding an inline behaviour package, then create your first predicate.')}</p><Button onClick={addPackage}>{t('Add inline behaviour package')}</Button></>}
    {active && draft && current && <fieldset key={active.key} className="guided-fieldset"><legend>{t('Edit {name}', { name: readableName(active.id) })}</legend><Space wrap>
      <label className="guided-label">{t('Evaluation profile')}<Select aria-label={t('Predicate evaluation profile')} value={String(draft.profile ?? '')} className="guided-profile-select" onChange={value => update(['predicates', active.id, 'profile'], value)} options={[{ value: '', label: t('Unset') }, ...profileOptions]} /></label>
      <span className="guided-muted">{t('Registry catalog: {types} types · {fields} fields · {relations} relations constrain role and field picks in the condition editor.', { types: types.length, fields: fields.length, relations: relations.length })}</span>
    </Space><p className="guided-muted">{t('Choose the actors this condition applies to, then compare their fields or combine simpler conditions. Advanced JSON is available in Details.')}</p>
      <Roles label={t('Predicate roles')} value={draft.roles} types={types} onChange={next => update(['predicates', active.id, 'roles'], next)} />
      <Ast node={draft.expression} path={t('Predicate expression')} roles={mapping(draft.roles) ? draft.roles : {}} types={types} fields={fields} relations={relations} onChange={next => update(['predicates', active.id, 'expression'], next)} />
      <ExpressionControl label={t('Parameters')} value={draft.parameters ?? {}} onChange={next => update(['predicates', active.id, 'parameters'], next)} />
      <Popconfirm title={t('Remove this predicate?')} description={t('Existing event chains may refer to it. Validate the draft after removing it.')} okText={t('Remove')} cancelText={t('Cancel')} onConfirm={() => { const predicates = { ...(mapping(current.predicates) ? current.predicates : {}) }; delete predicates[active.id]; change(active.pkg, { ...current, predicates }); setSelected(''); }}><Button danger>{t('Remove predicate')}</Button></Popconfirm>
      <Details title={t('{name} predicate JSON', { name: active.id })} buttonLabel={t('Predicate JSON')}><pre>{JSON.stringify(draft, null, 2)}</pre></Details><Details title={t('Package {name} JSON', { name: String(current.id ?? active.pkg) })} buttonLabel={t('Package JSON')}><pre>{JSON.stringify(current, null, 2)}</pre></Details>
    </fieldset>}
  </Card></div></AuthoringScope>;
}

export function expressionLabel(value: unknown, locale = 'en-US'): string {
  if (!mapping(value)) return translate(locale, 'Unsupported expression');
  if ('literal' in value) {
    const literal = value.literal;
    if (typeof literal === 'boolean') return literal ? translate(locale, 'true') : translate(locale, 'false');
    return mapping(literal) && ('$number' in literal || '$integer' in literal) ? String(literal.$number ?? literal.$integer) : typeof literal === 'object' ? JSON.stringify(literal) : String(literal);
  }
  if (typeof value.field === 'string') return `${String(value.role ?? '')} · ${registryLabel(value.field, locale)}`;
  if (typeof value.parameter === 'string') return translate(locale, 'Parameter {name}', { name: readableName(value.parameter) });
  if (typeof value.op === 'string') {
    const names: Record<string, string> = { eq: translate(locale, 'Equals'), ne: translate(locale, 'Does not equal'), and: translate(locale, 'All conditions'), or: translate(locale, 'Any condition'), not: translate(locale, 'Not'), gt: translate(locale, 'Greater than'), ge: translate(locale, 'At least'), lt: translate(locale, 'Less than'), le: translate(locale, 'At most') };
    return `${names[value.op] ?? readableName(value.op)}(${(Array.isArray(value.args) ? value.args : []).map(argument => expressionLabel(argument, locale)).join(', ')})`;
  }
  if (typeof value.relation === 'string') return translate(locale, 'Related by {relation}', { relation: registryLabel(value.relation, locale) });
  return translate(locale, 'Advanced expression');
}
