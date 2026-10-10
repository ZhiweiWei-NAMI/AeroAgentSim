import { Details } from '../console/Details';
import { Popconfirm } from 'antd';
import type { ReactNode } from 'react';
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { applicableFields, applicableRelations, arity, astKind, mapping, operators, patch, temporal, type AuthorType, type Draft, type Path } from './model';
import { parseLosslessJson } from '../feeds/lossless-json';
import './editor.css';
import { AuthoringValidity, ExpressionControl } from './ExpressionControl';
import { TriggerForm as Trigger } from './TriggerForm';
interface Props {
  value: Draft; onChange: (value: Draft) => void; types?: AuthorType[]; fields?: Draft[]; relations?: Draft[];
  errors?: Array<{ path: string; message: string }>; onValidate?: () => void; onValidityChange?: (valid: boolean) => void; initialTab?: string;
  capabilities?: string[]; events?: string[]; onImportYaml?: (yaml: string) => Promise<void>; onExportYaml?: () => Promise<string>;
}
const RelationCatalog = createContext<Draft[]>([]);
/** Removing a chain also removes its start bindings, not unrelated authored data. */
export function removeAuthoredElement(value: Draft, section: 'chains' | 'predicates', id: string): Draft {
  const rows = mapping(value[section]) ? { ...value[section] } : {};
  delete rows[id];
  return { ...value, [section]: rows, ...(section === 'chains' && Array.isArray(value.bindings)
    ? { bindings: value.bindings.filter(binding => !mapping(binding) || binding.chain !== id) } : {}) };
}
/** Standalone typed editors share the same invalid-buffer handling as package authoring. */
export function AuthoringScope({ children, onValidityChange }: { children: ReactNode; onValidityChange?: (valid: boolean) => void }) {
  const [invalid, setInvalid] = useState<Record<string, string>>({});
  const report = useCallback((id: string, error: string) => setInvalid(previous => {
    if (previous[id] === error || (!error && !(id in previous))) return previous;
    const next = { ...previous }; if (error) next[id] = error; else delete next[id]; return next;
  }), []);
  const valid = Object.keys(invalid).length === 0;
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
  return <AuthoringValidity.Provider value={report}>{children}</AuthoringValidity.Provider>;
}

function Json({ label, value, onChange }: { label: string; value: unknown; onChange: (value: unknown) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState(() => JSON.stringify(value, null, 2) ?? ''), [error, setError] = useState('');
  const sent = useRef(value), id = useId();
  const report = useContext(AuthoringValidity);
  useEffect(() => { report(id, error); return () => report(id, ''); }, [report, id, error]);
  useEffect(() => { if (value !== sent.current) { setText(JSON.stringify(value, null, 2) ?? ''); setError(''); sent.current = value; } }, [value]);
  return <Details title={label} buttonLabel={t('Details')}><label className="behaviour-json">{label}<textarea aria-label={label} aria-invalid={!!error || undefined} rows={4} value={text} onChange={event => {
    setText(event.target.value);
    try { const next = parseLosslessJson(event.target.value, true); onChange(next); sent.current = next; setError(''); } catch (problem) { setError(String(problem)); }
  }}/>{error && <span role="alert">{t('Invalid JSON: {error}', { error })}</span>}</label></Details>;
}

export function Ast({ node, onChange, path, roles, types, fields, depth = 0, relations: providedRelations }: { node: unknown; onChange: (node: unknown) => void; path: string; roles: Draft; types: AuthorType[]; fields: Draft[]; depth?: number; relations?: Draft[] }) {
  const { t } = useI18n();
  const contextRelations = useContext(RelationCatalog);
  const relations = providedRelations ?? contextRelations;
  const kind = astKind(node), row = mapping(node) ? node : {};
  const kindLabels: Record<string, string> = { literal: t('Literal'), field: t('State field'), relation: t('Relation'), operator: t('Operator'), parameter: t('Parameter'), var: t('Variable'), time: t('Time') };
  const set = (key: string, next: unknown) => onChange({ ...row, [key]: next });
  if (kind === 'unsupported' || depth > 32) return <div><p>{t('Unsupported AST content retained at {path}; edit raw JSON. Server compiler determines support.', { path })}</p><Json label={t('{path} AST JSON', { path })} value={node} onChange={onChange}/></div>;
  const relationOptions = applicableRelations(typeof row.sourceRole === 'string' && typeof roles[row.sourceRole] === 'string' ? roles[row.sourceRole] as string : undefined, typeof row.targetRole === 'string' && typeof roles[row.targetRole] === 'string' ? roles[row.targetRole] as string : undefined, types, relations);
  const options = typeof row.role === 'string' && typeof roles[row.role] === 'string' ? applicableFields(roles[row.role] as string, types, fields) : [];
  return <fieldset className="ast-node"><legend>{path} · {kindLabels[kind]}</legend>
    <label>{t('Node kind')} <select aria-label={t('{path} node kind', { path })} value={kind} onChange={event => {
      const selected = event.target.value;
      onChange(selected === 'operator' ? { op: 'eq', args: [{ literal: null }, { literal: null }] } : selected === 'field' ? { field: '', role: '', path: [] } : selected === 'relation' ? { relation: '', sourceRole: '', targetRole: '' } : selected === 'time' ? { time: true } : selected === 'parameter' ? { parameter: '' } : selected === 'var' ? { var: '', path: [] } : { literal: null });
    }}>{['literal', 'field', 'relation', 'operator', 'parameter', 'var', 'time'].map(name => <option key={name} value={name}>{kindLabels[name]}</option>)}</select></label>
    {kind === 'field' && <><label>{t('Role')} <select aria-label={t('{path} role', { path })} value={typeof row.role === 'string' ? row.role : ''} onChange={event => set('role', event.target.value)}><option value="">{t('Select bound role')}</option>{Object.keys(roles).map(role => <option key={role}>{role}</option>)}</select></label>
      <label>{t('Field')} <input aria-label={t('{path} field', { path })} list={`${path}-fields`} value={typeof row.field === 'string' ? row.field : ''} onChange={event => set('field', event.target.value)}/><datalist id={`${path}-fields`}>{options.map(field => <option key={String(field.id)} value={String(field.id)}>{String(field.declaring_type ?? field.type ?? field.declaringClass)} · {JSON.stringify(field.schema)}</option>)}</datalist></label>
      <small>{t("Picker includes fields inherited by this role's entity type. The server checks them against the pinned registry.")}</small><Json label={t('{path} field path', { path })} value={row.path ?? []} onChange={next => set('path', next)}/></>}
    {kind === 'literal' && <ExpressionControl label={t('{path} literal', { path })} value={row.literal} onChange={next => set('literal', next)}/>}
    {kind === 'relation' && ['relation', 'sourceRole', 'targetRole'].map(key => <label key={key}>{key === 'relation' ? t('Relation') : key === 'sourceRole' ? t('Source role') : t('Target role')}<input aria-label={key === 'relation' ? t('{path} relation', { path }) : key === 'sourceRole' ? t('{path} source role', { path }) : t('{path} target role', { path })} value={typeof row[key] === 'string' ? row[key] as string : ''} onChange={event => set(key, event.target.value)} list={key === 'relation' ? `${path}-relations` : `${path}-roles`}/></label>)}
    {kind === 'relation' && <datalist id={`${path}-relations`}>{relationOptions.map(relation => <option key={String(relation.id)} value={String(relation.id)}>{JSON.stringify(relation)}</option>)}</datalist>}
    {kind === 'relation' && <datalist id={`${path}-roles`}>{Object.keys(roles).map(role => <option key={role}>{role}</option>)}</datalist>}
    {['parameter', 'var'].includes(kind) && <label>{kindLabels[kind]}<input aria-label={kind === 'parameter' ? t('{path} parameter', { path }) : t('{path} variable', { path })} value={typeof row[kind] === 'string' ? row[kind] as string : ''} onChange={event => set(kind, event.target.value)}/></label>}
    {kind === 'operator' && <><label>{t('Operator')} <select aria-label={t('{path} operator', { path })} value={String(row.op)} onChange={event => {
      const op = event.target.value;
      if (op === 'all' || op === 'any') { onChange({ ...row, op, array: { literal: [] }, var: 'item', predicate: { literal: true } }); return; }
      const old = Array.isArray(row.args) ? row.args : [];
      onChange({ ...row, op, args: Array.from({ length: arity[op] ?? (temporal.includes(op) ? 2 : Math.max(1, old.length)) }, (_, i) => old[i] ?? { literal: null }) });
    }}>{operators.map(op => <option key={op}>{op}</option>)}</select></label>
      <small>{t('Operators combine values or conditions. Operand order is preserved.')}</small>
      {(row.op === 'all' || row.op === 'any') ? <><label>{t('Bound variable')} <input aria-label={t('{path} bound variable', { path })} value={String(row.var ?? '')} onChange={event => set('var', event.target.value)}/></label>{['array', 'predicate', 'applicabilityExpression'].filter(key => key in row).map(key => <Ast key={key} node={row[key]} onChange={next => set(key, next)} path={`${path}.${key}`} roles={roles} types={types} fields={fields} depth={depth + 1} relations={relations}/>)}</> : Array.isArray(row.args) && <>{row.args.map((child, index) => <Ast key={index} node={child} onChange={next => set('args', (row.args as unknown[]).map((item, i) => index === i ? next : item))} path={`${path}.args[${index}]`} roles={roles} types={types} fields={fields} depth={depth + 1} relations={relations}/>)}
        {!arity[String(row.op)] && <button type="button" onClick={() => set('args', [...(row.args as unknown[]), { literal: null }])}>{t('Add operand')}</button>}</>}
      {'asScope' in row && <Ast node={row.asScope} onChange={next => set('asScope', next)} path={`${path}.asScope`} roles={roles} types={types} fields={fields} depth={depth + 1} relations={relations}/>}</>}
    <Json label={t('{path} complete node', { path })} value={row} onChange={onChange}/>
  </fieldset>;
}

export function Roles({ value, onChange, types, label }: { value: unknown; onChange: (next: unknown) => void; types: AuthorType[]; label: string }) {
  const { t } = useI18n();
  const [role, setRole] = useState('');
  if (!mapping(value)) return <Json label={label} value={value} onChange={onChange}/>;
  return <fieldset><legend>{label}</legend><small>{t('Roles name the entities a condition or chain can refer to. Assign each role an entity type.')}</small>{Object.entries(value).map(([key, type]) => <label key={key}>{key}<input aria-label={`${label}.${key}`} list={`${label}-types`} value={typeof type === 'string' ? type : ''} onChange={event => onChange({ ...value, [key]: event.target.value })}/></label>)}
    <datalist id={`${label}-types`}>{types.map(type => <option key={type.id} value={type.id}>{type.abstract ? t('abstract · {parents}', { parents: type.parents.join(', ') }) : type.parents.join(', ')}</option>)}</datalist>
    <input aria-label={t('{label} new role', { label })} value={role} onChange={event => setRole(event.target.value)}/><button type="button" disabled={!role || role in value} onClick={() => { onChange({ ...value, [role]: '' }); setRole(''); }}>{t('Add role')}</button>
  </fieldset>;
}

function Actions({ value, onChange, label, roles, types, fields, capabilities }: { value: unknown; onChange: (value: unknown) => void; label: string; roles: Draft; types: AuthorType[]; fields: Draft[]; capabilities: string[] }) {
  const { t } = useI18n();
  const kindLabels: Record<string, string> = { set: t('Set state field'), emit: t('Emit event'), command: t('Submit command'), cancel_command: t('Cancel command'), delay: t('Schedule delay'), cancel_timer: t('Cancel timer'), assert_relation: t('Assert relation'), close_relation: t('Close relation'), create_entity: t('Create entity'), remove_entity: t('Remove entity'), complete: t('Complete chain') };
  const kinds = Object.keys(kindLabels);
  const [kind, setKind] = useState('set');
  if (!Array.isArray(value)) return <Json label={label} value={value} onChange={onChange}/>;
  const set = (index: number, next: unknown) => onChange(value.map((row, i) => index === i ? next : row));
  return <fieldset><legend>{label}</legend>{!value.length && <p>{t('No actions yet. Actions run in the order shown.')}</p>}{value.map((action, index) => mapping(action) ? <fieldset key={index}><legend>{String(action.id ?? t('Action'))} · {kindLabels[String(action.kind)] ?? String(action.kind)}</legend>
    <label>{t('Action ID')} <input aria-label={t('{label} action {index} id', { label, index })} value={typeof action.id === 'string' ? action.id : ''} onChange={event => set(index, { ...action, id: event.target.value })}/></label>
    <label>{t('Kind')} <select aria-label={t('{label} action {index} kind', { label, index })} value={String(action.kind ?? '')} onChange={event => set(index, { ...action, kind: event.target.value })}><option value="">{t('Unset')}</option>{kinds.map(name => <option key={name} value={name}>{kindLabels[name]}</option>)}</select></label>
    {action.kind === 'set' && <><label>{t('Target role')} <select aria-label={t('{label} action {index} role', { label, index })} value={mapping(action.entity) && typeof action.entity.$role === 'string' ? action.entity.$role : ''} onChange={event => set(index, { ...action, entity: { $role: event.target.value } })}><option value="">{t('Select')}</option>{Object.keys(roles).map(role => <option key={role}>{role}</option>)}</select></label>
      <label>{t('State field')} <input aria-label={t('{label} action {index} field', { label, index })} value={typeof action.field === 'string' ? action.field : ''} list={`${label}-${index}-fields`} onChange={event => set(index, { ...action, field: event.target.value })}/><datalist id={`${label}-${index}-fields`}>{applicableFields(mapping(action.entity) && typeof action.entity.$role === 'string' && typeof roles[action.entity.$role] === 'string' ? roles[action.entity.$role] as string : '', types, fields).map(field => <option key={String(field.id)} value={String(field.id)}/>)}</datalist></label><ExpressionControl label={t('{label} action {index} typed value', { label, index })} value={action.value} onChange={next => set(index, { ...action, value: next })}/></>}
    {action.kind === 'command' && <label>{t('Command capability')}<input aria-label={t('{label} action {index} capability', { label, index })} list={`${label}-${index}-capabilities`} value={typeof action.capability === 'string' ? action.capability : ''} onChange={event => set(index, { ...action, capability: event.target.value })}/><datalist id={`${label}-${index}-capabilities`}>{capabilities.map(id => <option key={id}>{id}</option>)}</datalist></label>}
    {['command', 'emit'].includes(String(action.kind)) && <>{['schema', action.kind === 'command' ? 'target' : 'topic'].map(key => <label key={key}>{key === 'schema' ? t('Schema') : key === 'target' ? t('Target') : t('Topic')}<input value={typeof action[key] === 'string' ? action[key] as string : ''} onChange={event => set(index, { ...action, [key]: event.target.value })}/></label>)}<ExpressionControl label={t('{label} action {index} payload', { label, index })} value={action.payload} onChange={next => set(index, { ...action, payload: next })}/></>}
    {['delay', 'cancel_command', 'cancel_timer'].includes(String(action.kind)) && <Json label={t('{label} action {index} timing / child identity', { label, index })} value={action} onChange={next => set(index, next)}/>}
    <Json label={t('{label} action {index} JSON', { label, index })} value={action} onChange={next => set(index, next)}/>
  </fieldset> : <Json key={index} label={t('{label} unsupported action {index}', { label, index })} value={action} onChange={next => set(index, next)}/>)}
    <select aria-label={t('{label} new action kind', { label })} value={kind} onChange={event => setKind(event.target.value)}>{kinds.map(name => <option key={name} value={name}>{kindLabels[name]}</option>)}</select><button type="button" onClick={() => onChange([...value, { id: `action-${value.length + 1}`, kind }])}>{t('Add ordered action')}</button>
  </fieldset>;
}

function Declarations({ section, value, onChange, types }: { section: string; value: unknown; onChange: (value: unknown) => void; types: AuthorType[] }) {
  const { t } = useI18n();
  const sectionLabel = section === 'bindings' ? t('bindings') : section === 'conflicts' ? t('conflicts') : t('Injection points');
  if (!Array.isArray(value)) return <Json label={t('{section} typed declarations JSON', { section: sectionLabel })} value={value} onChange={onChange}/>;
  const keys = section === 'bindings' ? ['id', 'chain', 'multiplicity', 'on_unbind', 'episode_field'] : section === 'conflicts' ? ['id', 'predicate', 'edge', 'emits', 'topic'] : ['id', 'stream_id', 'command', 'target', 'emits', 'topic'];
  const keyLabels: Record<string, string> = { id: t('ID'), chain: t('Chain'), multiplicity: t('Multiplicity'), on_unbind: t('Unbind policy'), episode_field: t('Episode field'), predicate: t('Predicate'), edge: t('Edge'), emits: t('Emitted event'), topic: t('Topic'), stream_id: t('Stream ID'), command: t('Command'), target: t('Target') };
  const options: Record<string, string[]> = { multiplicity: ['once_per_entity', 'once_per_relation', 'once_per_task_episode'], on_unbind: ['close_after_cleanup', 'retain_until_terminal'], edge: ['entered', 'exited', 'while'] };
  const optionLabels: Record<string, string> = { once_per_entity: t('Once per entity'), once_per_relation: t('Once per relation'), once_per_task_episode: t('Once per task episode'), close_after_cleanup: t('Close after cleanup'), retain_until_terminal: t('Retain until terminal state'), entered: t('entered'), exited: t('exited'), while: t('while') };
  return <div>{value.map((row, index) => mapping(row) ? <fieldset key={index}><legend>{sectionLabel}[{index}]</legend>{keys.map(key => <label key={key}>{keyLabels[key]}{options[key] ? <select aria-label={t('{section} {index} {field}', { section: sectionLabel, index, field: keyLabels[key] })} value={String(row[key] ?? '')} onChange={event => onChange(value.map((item, i) => i === index ? { ...row, [key]: event.target.value } : item))}><option value="">{t('Unset')}</option>{options[key].map(item => <option key={item} value={item}>{optionLabels[item]}</option>)}</select> : <input aria-label={t('{section} {index} {field}', { section: sectionLabel, index, field: keyLabels[key] })} value={typeof row[key] === 'string' ? row[key] as string : ''} onChange={event => onChange(value.map((item, i) => i === index ? { ...row, [key]: event.target.value } : item))}/>}</label>)}
    {section === 'conflicts' && <Roles value={row.roles} onChange={next => onChange(value.map((item, i) => i === index ? { ...row, roles: next } : item))} types={types} label={t('conflicts {index} roles', { index })}/>}
    <ExpressionControl label={t('{section} {index} complete declaration', { section: sectionLabel, index })} value={row} onChange={next => onChange(value.map((item, i) => i === index ? next : item))}/>
  </fieldset> : <Json key={index} label={t('{section} {index} unsupported declaration', { section: sectionLabel, index })} value={row} onChange={next => onChange(value.map((item, i) => i === index ? next : item))}/>)}<button type="button" onClick={() => onChange([...value, { id: '' }])}>{section === 'bindings' ? t('Add binding declaration') : section === 'conflicts' ? t('Add conflict declaration') : t('Add injection point declaration')}</button></div>;
}

export function BehaviourEditor({ value, onChange, types = [], fields = [], relations = [], errors = [], capabilities = [], events = [], onValidate, onImportYaml, onExportYaml, onValidityChange, initialTab }: Props) {
  const { t } = useI18n();
  const [tab, setTab] = useState(initialTab ?? 'predicates'), [selected, setSelected] = useState(''), [newId, setNewId] = useState(''), [yaml, setYaml] = useState(''), [error, setError] = useState(''), [connect, setConnect] = useState(false), [from, setFrom] = useState<string>();
  const [invalid, setInvalid] = useState<Record<string, string>>({});
  const report = useCallback((id: string, problem: string) => setInvalid(previous => {
    if (previous[id] === problem || (!problem && !(id in previous))) return previous;
    const next = { ...previous }; if (problem) next[id] = problem; else delete next[id]; return next;
  }), []);
  const valid = Object.keys(invalid).length === 0;
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
  const update = (path: Path, next: unknown) => onChange(patch(value, path, next) as Draft);
  const predicates = mapping(value.predicates) ? value.predicates : {}, chains = mapping(value.chains) ? value.chains : {};
  const ids = Object.keys(tab === 'chains' ? chains : predicates), id = ids.includes(selected) ? selected : ids[0];
  const row = mapping((tab === 'chains' ? chains : predicates)[id]) ? (tab === 'chains' ? chains : predicates)[id] as Draft : undefined;
  useEffect(() => { setFrom(undefined); }, [id, tab]);
  const tabLabels: Record<string, string> = { predicates: t('Conditions (predicates)'), chains: t('Event chains'), bindings: t('Start rules'), conflicts: t('Conflict rules'), injection_points: t('External events'), raw: t('Source JSON & YAML') };
  const tabDescriptions: Record<string, string> = { predicates: t('Conditions evaluated against bound entities'), chains: t('State machines and ordered actions'), bindings: t('How entities start chain instances'), conflicts: t('Conditions that report conflicts'), injection_points: t('Declared entry points for external events'), raw: t('Complete package JSON and YAML') };
  const perform = async (fn: () => Promise<void>) => { setError(''); try { await fn(); } catch (problem) { setError(String(problem)); } };
  const selectState = (state: string) => {
    if (!connect || !row) return;
    if (from === undefined) { setFrom(state); return; }
    const transitions = Array.isArray(row.transitions) ? row.transitions : [];
    update(['chains', id, 'transitions'], [...transitions, { id: `transition-${transitions.length + 1}`, from, on: {}, to: state, priority: 0, actions: [] }]); setFrom(undefined);
  };
  return <RelationCatalog.Provider value={relations}><AuthoringValidity.Provider value={report}><div className="behaviour-editor">
    <label>{t('Package ID')} <input aria-label={t('Package ID')} value={typeof value.id === 'string' ? value.id : ''} onChange={event => update(['id'], event.target.value)}/></label>
    {onValidate && <button type="button" disabled={!valid} onClick={onValidate}>{t('Validate package on server')}</button>}
    {errors.map((item, index) => <button type="button" className="authored-error" key={index} onClick={() => { if (item.path.includes('.chains.')) { setTab('chains'); setSelected(Object.keys(chains).find(name => item.path.includes(`.chains.${name}`)) ?? ''); } else if (item.path.includes('.predicates.')) { setTab('predicates'); setSelected(Object.keys(predicates).find(name => item.path.includes(`.predicates.${name}`)) ?? ''); } else setTab('raw'); document.getElementById(`authored-${item.path}`)?.scrollIntoView?.(); }}>{t('Validation error at {path}: {message}', { path: item.path, message: item.message })}</button>)}
    <nav>{['predicates', 'chains', 'bindings', 'conflicts', 'injection_points', 'raw'].map(name => <button type="button" key={name} title={tabDescriptions[name]} aria-pressed={tab === name} onClick={() => { setTab(name); setSelected(''); }}>{tabLabels[name]}</button>)}</nav>
    {['predicates', 'chains'].includes(tab) && <><label>{t('Authored element')} <select aria-label={t('Authored element')} value={id ?? ''} onChange={event => setSelected(event.target.value)}>{ids.map(name => <option key={name}>{name}</option>)}</select></label>
      <input aria-label={t('New element ID')} value={newId} onChange={event => setNewId(event.target.value)}/><button type="button" disabled={!newId || ids.includes(newId)} onClick={() => { const section = tab === 'chains' ? chains : predicates; update([tab], { ...section, [newId]: tab === 'chains' ? { roles: {}, trigger: {}, preconditions: [], initial: 'waiting', terminal: ['done'], states: ['waiting', 'done'], transitions: [] } : { profile: 'committed_reactive/v1', roles: {}, expression: { literal: null } } }); setSelected(newId); setNewId(''); }}>{tab === 'chains' ? t('Add chain') : t('Add predicate')}</button>
      {row && <Popconfirm title={tab === 'chains' ? t('Remove this chain and its start rules?') : t('Remove this predicate?')} description={t('Other references are kept. Validate the draft after removal.')} okText={t('Remove')} cancelText={t('Cancel')} onConfirm={() => { onChange(removeAuthoredElement(value, tab as 'chains' | 'predicates', id)); setSelected(''); }}><button type="button">{tab === 'chains' ? t('Remove chain') : t('Remove predicate')}</button></Popconfirm>}
      {!row && <p>{t('Choose an element or edit its unsupported section in raw JSON.')}</p>}
      {row && <div key={`${tab}/${id}`} id={`authored-$.${tab}.${id}`}><Roles value={row.roles} onChange={next => update([tab, id, 'roles'], next)} types={types} label={`${tab}.${id}.roles`}/>
        {tab === 'predicates' ? <><label>{t('Evaluation profile')} <select aria-label={t('Predicate profile')} value={String(row.profile ?? '')} onChange={event => update(['predicates', id, 'profile'], event.target.value)}><option value="">{t('Unset')}</option><option value="committed_reactive/v1">{t('When committed state changes')}</option><option value="aerograph_sampled/v1">{t('At simulation sample times')}</option></select></label>
          <Ast node={row.expression} onChange={next => update(['predicates', id, 'expression'], next)} path={`predicates.${id}.expression`} roles={mapping(row.roles) ? row.roles : {}} types={types} fields={fields}/></> : <>
          <Trigger events={events} initial value={row.trigger} onChange={next => update(['chains', id, 'trigger'], next)} label={`chains.${id}.trigger`} predicates={Object.keys(predicates)}/>
          <ExpressionControl label={t('Chain preconditions')} value={row.preconditions ?? []} onChange={next => update(['chains', id, 'preconditions'], next)}/>
          <ExpressionControl label={t('Chain states')} value={row.states} onChange={next => update(['chains', id, 'states'], next)}/>
          <label>{t('Initial state')} <input aria-label={t('Initial state')} value={String(row.initial ?? '')} onChange={event => update(['chains', id, 'initial'], event.target.value)}/></label>
          <ExpressionControl label={t('Terminal states')} value={row.terminal} onChange={next => update(['chains', id, 'terminal'], next)}/>
          <label><input type="checkbox" checked={connect} onChange={event => { setConnect(event.target.checked); setFrom(undefined); }}/>{from === undefined ? t('Connect states · choose source') : t('Connect states · choose target')}</label>
          {Array.isArray(row.states) && <svg viewBox={`0 0 520 ${Math.max(150, row.states.length * 80)}`} role="img" aria-label={t('Draft chain state-machine graph')}><defs><marker id="draft-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#64748b"/></marker></defs>
            {Array.isArray(row.transitions) && row.transitions.map((transition, index) => { if (!mapping(transition)) return null; const start = (row.states as unknown[]).indexOf(transition.from), end = (row.states as unknown[]).indexOf(transition.to); if (start < 0 || end < 0) return null; const focus = () => document.getElementById(`transition-${id}-${index}`)?.scrollIntoView?.(); return <g key={index} role="button" tabIndex={0} aria-label={t('Graph transition {id}', { id: String(transition.id) })} onClick={focus} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focus(); } }}><path d={`M270 ${40 + start * 80} C460 ${40 + start * 80},460 ${40 + end * 80},270 ${40 + end * 80}`} fill="none" stroke="#64748b" markerEnd="url(#draft-arrow)"/><text x="350" y={40 + (start + end) * 40}>{String(transition.id)}</text></g>; })}
            {row.states.map((state, index) => <g role="button" tabIndex={0} aria-label={t('Graph state {state}', { state: String(state) })} key={String(state)} onClick={() => selectState(String(state))} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectState(String(state)); } }}><rect x="80" y={index * 80 + 15} width="190" height="50" rx="7" fill={state === from ? '#e0f2fe' : '#f1f5f9'} stroke="#64748b"/><text x="100" y={index * 80 + 45}>{state === row.initial ? t('{state} (initial)', { state: String(state) }) : String(state)}</text></g>)}
          </svg>}
          {Array.isArray(row.transitions) && row.transitions.map((transition, index) => mapping(transition) ? <fieldset key={index} id={`transition-${id}-${index}`}><legend>{t('Transition {id}', { id: String(transition.id) })}</legend>{transition.guard !== undefined && <ExpressionControl label={t('Transition {index} guard', { index })} value={transition.guard} onChange={next => update(['chains', id, 'transitions', index, 'guard'], next)}/>} {['id', 'from', 'to'].map(key => <label key={key}>{key === 'id' ? t('ID') : key === 'from' ? t('From state') : t('To state')}<input aria-label={key === 'id' ? t('Transition {index} id', { index }) : key === 'from' ? t('Transition {index} from', { index }) : t('Transition {index} to', { index })} value={typeof transition[key] === 'string' ? transition[key] as string : ''} onChange={event => update(['chains', id, 'transitions', index, key], event.target.value)}/></label>)}
            <label>{t('Priority')} <input aria-label={t('Transition {index} priority', { index })} type="number" value={typeof transition.priority === 'number' ? transition.priority : ''} onChange={event => { if (event.target.value === '') update(['chains', id, 'transitions', index, 'priority'], undefined); else if (Number.isFinite(event.target.valueAsNumber)) update(['chains', id, 'transitions', index, 'priority'], event.target.valueAsNumber); }}/></label>
            <Trigger events={events} actions={Array.isArray(row.transitions) ? row.transitions.filter(mapping).flatMap(item => Array.isArray(item.actions) ? item.actions.filter(mapping).flatMap(action => typeof action.id === 'string' ? [action.id] : []) : []) : []} value={transition.on} onChange={next => update(['chains', id, 'transitions', index, 'on'], next)} label={t('Transition {index} on', { index })} predicates={Object.keys(predicates)}/>
            <Actions capabilities={capabilities} label={t('Transition {index} ordered actions', { index })} value={transition.actions} onChange={next => update(['chains', id, 'transitions', index, 'actions'], next)} roles={mapping(row.roles) ? row.roles : {}} types={types} fields={fields}/>
            <Json label={t('Transition {index} JSON', { index })} value={transition} onChange={next => update(['chains', id, 'transitions', index], next)}/>
          </fieldset> : <Json key={index} label={t('Unsupported transition {index}', { index })} value={transition} onChange={next => update(['chains', id, 'transitions', index], next)}/>)}
        </>}
        <Json label={t('{path} complete JSON', { path: `${tab}.${id}` })} value={row} onChange={next => update([tab, id], next)}/>
      </div>}
    </>}
    {['bindings', 'conflicts', 'injection_points'].includes(tab) && <Declarations section={tab} value={value[tab]} onChange={next => update([tab], next)} types={types}/>}
    {tab === 'raw' && <><Json label={t('Behaviour package JSON')} value={value} onChange={next => { if (!mapping(next)) throw Error(t('Behaviour package must be a mapping')); onChange(next); }}/>
      <label>{t('YAML round trip')} <textarea aria-label={t('Behaviour package YAML')} rows={10} value={yaml} onChange={event => setYaml(event.target.value)}/></label>
      <button type="button" disabled={!onExportYaml} onClick={() => void perform(async () => setYaml(await onExportYaml!()))}>{t('Export package YAML')}</button>
      <button type="button" disabled={!onImportYaml || !yaml} onClick={() => void perform(async () => onImportYaml!(yaml))}>{t('Import package YAML')}</button></>}
    {error && <p role="alert">{t('Request failed: {error}', { error })}</p>}
  </div></AuthoringValidity.Provider></RelationCatalog.Provider>;
}
