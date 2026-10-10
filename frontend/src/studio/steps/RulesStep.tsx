import { useMemo, useState } from 'react';
import { Alert, Button, Card, Collapse, Input, InputNumber, Select, Space, Table, Tag } from 'antd';
import { useI18n } from '../../i18n/I18nProvider';
import { translate } from '../../i18n/translate';
import { BehavioursPanel } from '../BehavioursPanel';
import { Details } from '../../console/Details';
import { TypedPayloadForm } from '../../behaviours/TypedPayloadForm';
import { applicableFields, applicableRelations, isA, mapping, type Draft } from '../../behaviours/model';
import { readableName, registryLabel } from '../guided-model';
import { packagesOf, isInline, type GuidedStepProps, type CatalogProps } from './PredicatesStep';
import './RulesStep.css';

interface Props extends GuidedStepProps, CatalogProps { scenario: Record<string, any>; onValidityChange?: (valid: boolean) => void }
/** Authored labels are data, not fabricated descriptions. */
export function entityLabel(entity: Draft): string {
  const facts = mapping(entity.facts) ? entity.facts : {};
  for (const key of ['name', 'label', 'display_name']) if (typeof facts[key] === 'string' && (facts[key] as string).trim()) return (facts[key] as string).trim();
  return readableName(String(entity.id));
}
export interface RulePlan { actions: Draft[]; transitions?: Draft[] }
export function appendPredicateRule(scenario: Record<string, any>, index: number, id: string, predicate: string, match: Draft, action: Draft, plan?: RulePlan, locale = 'en-US'): Record<string, any> {
  const packages = packagesOf(scenario), pkg = packages[index], name = id.trim();
  if (!isInline(pkg) || !mapping(pkg.predicates) || !mapping(pkg.predicates[predicate])) throw Error(translate(locale, 'Choose an inline package predicate'));
  const row = pkg.predicates[predicate] as Draft;
  if (!name || mapping(pkg.chains) && name in pkg.chains) throw Error(translate(locale, 'Choose a unique rule name'));
  const actions = plan?.actions ?? [{ ...action, id: 'action' }, { id: 'finish', kind: 'complete', status: 'completed' }];
  const transitions = plan?.transitions ?? [{ id: 'act', from: 'waiting', on: { instance: 'activated' }, to: 'done', actions }];
  const chain = { roles: row.roles, trigger: { predicate, edge: 'entered' }, initial: 'waiting', terminal: ['done'], states: ['waiting', 'done'], transitions };
  const next = { ...pkg, chains: { ...(mapping(pkg.chains) ? pkg.chains : {}), [name]: chain }, bindings: [...(Array.isArray(pkg.bindings) ? pkg.bindings : []), { id: `${name}-binding`, chain: name, match, multiplicity: 'once_per_entity', on_unbind: 'retain_until_terminal' }] };
  return { ...scenario, behaviours: packages.map((value, i) => i === index ? next : value) };
}
export function commandRulePlan(action: Draft): RulePlan {
  return { actions: [action], transitions: [
    { id: 'issue', from: 'waiting', on: { instance: 'activated' }, to: 'waiting', actions: [action] },
    { id: 'succeeded', from: 'waiting', on: { receipt: action.id, status: 'succeeded' }, to: 'done', actions: [{ id: 'finish', kind: 'complete', status: 'completed' }] },
    { id: 'failed', from: 'waiting', on: { receipt: action.id, status: ['failed', 'rejected', 'canceled'] }, to: 'done', actions: [{ id: 'finish', kind: 'complete', status: 'failed' }] },
  ] };
}
type RuleKind = 'set' | 'emit' | 'command' | 'delay' | 'assert_relation';
export function RulesStep({ workspace, api, save, onApply, onChange, scenario, types, fields, relations, onValidityChange }: Props) {
  const { t, locale } = useI18n();
  const packages = packagesOf(scenario), inline = packages.map((pkg, index) => ({ pkg, index })).filter(row => isInline(row.pkg));
  const [predicateKey, setPredicateKey] = useState<string>(), [name, setName] = useState(''), [roleBindings, setRoleBindings] = useState<Record<string, string>>({}), [targetRole, setTargetRole] = useState<string>(), [field, setField] = useState<string>(), [value, setValue] = useState<unknown>(), [valueValid, setValueValid] = useState(true), [error, setError] = useState('');
  const [kind, setKind] = useState<RuleKind>('set'), [emitSchema, setEmitSchema] = useState<string>(), [topic, setTopic] = useState(''), [payload, setPayload] = useState<unknown>(), [payloadValid, setPayloadValid] = useState(true), [commandCap, setCommandCap] = useState<string>(), [commandTarget, setCommandTarget] = useState(''), [duration, setDuration] = useState<number | undefined>(), [relation, setRelation] = useState<string>(), [edgeId, setEdgeId] = useState(''), [sourceRole, setSourceRole] = useState<string>(), [relTargetRole, setRelTargetRole] = useState<string>();
  const predicateOptions = inline.flatMap(({ pkg, index }) => Object.entries(mapping(pkg.predicates) ? pkg.predicates : {}).filter(([, row]) => mapping(row)).map(([id]) => ({ value: `${index}/${id}`, label: readableName(id) })));
  const pkgIndex = predicateKey ? Number(predicateKey.slice(0, predicateKey.indexOf('/'))) : undefined, predicateId = predicateKey?.slice(predicateKey.indexOf('/') + 1);
  const pkg = pkgIndex === undefined ? undefined : packages[pkgIndex], predicate = pkg && predicateId && mapping(pkg.predicates) ? pkg.predicates[predicateId] : undefined;
  const roles = mapping(predicate) && mapping(predicate.roles) ? predicate.roles : {};
  const targetType = targetRole && typeof roles[targetRole] === 'string' ? roles[targetRole] as string : undefined;
  const applicable = targetType ? applicableFields(targetType, types, fields) : [];
  const messages: Array<Draft> = scenario.registry?.messages ?? [], schemas: Record<string, Draft> = scenario.registry?.schemas ?? {};
  const behaviourEngines = Object.values(scenario.engines ?? {}).filter((row): row is Draft => mapping(row) && row.plugin === 'behaviour');
  const capabilityValues = behaviourEngines.length === 1 && mapping(behaviourEngines[0].config) && mapping(behaviourEngines[0].config.capabilities) ? behaviourEngines[0].config.capabilities : {};
  const capabilities: Record<string, Draft> = Object.fromEntries(Object.entries(capabilityValues).flatMap(([id, row]) => mapping(row) ? [[id, row]] : []));
  const messageSchema = (id: string | undefined) => { const schema = messages.find(item => item.id === id)?.schema; return typeof schema === 'string' ? schemas[schema] : schema; };
  const eventOptions = messages.filter(row => row.kind === 'event').map(row => ({ value: String(row.id), label: readableName(String(row.id)) }));
  const commandOptions = messages.filter(row => row.kind === 'command').map(row => ({ value: String(row.id), label: readableName(String(row.id)) }));
  const emitSchemaResolved = messageSchema(emitSchema), capabilitySchemaId = capabilities[commandCap ?? '']?.schema;
  const commandCapSchema = typeof capabilitySchemaId === 'string' ? messageSchema(capabilitySchemaId) ?? schemas[capabilitySchemaId] : capabilitySchemaId;
  const relSource = sourceRole && typeof roles[sourceRole] === 'string' ? roles[sourceRole] as string : undefined, relTarget = relTargetRole && typeof roles[relTargetRole] === 'string' ? roles[relTargetRole] as string : undefined;
  const relationOptions = useMemo(() => applicableRelations(relSource, relTarget, types, relations).map(row => ({ value: String(row.id), label: registryLabel(String(row.id), locale) })), [relSource, relTarget, types, relations, locale]);
  const entityOptions = (type: string) => [{ value: '@type', label: t('Every {type}', { type: registryLabel(type, locale) }) }, ...(Array.isArray(scenario.entities) ? scenario.entities : []).filter((entity: any) => isA(entity.type, type, types)).map((entity: any) => ({ value: String(entity.id), label: entityLabel(entity) }))];
  const rows = useMemo(() => inline.flatMap(({ pkg, index }) => Object.entries(mapping(pkg.chains) ? pkg.chains : {}).filter(([, chain]) => mapping(chain)).map(([id, chain]) => ({ key: `${index}/${id}`, id, chain: chain as Draft, bindings: (Array.isArray(pkg.bindings) ? pkg.bindings : []).filter((binding: any) => binding.chain === id) }))), [scenario.behaviours]);
  const common = { api, workspace, scenario, types, fields, relations, onChange, onApply, save, onValidityChange: onValidityChange ?? (() => {}) };
  const kindOptions = [{ value: 'set', label: t('Set state') }, { value: 'emit', label: t('Emit event') }, { value: 'command', label: t('Issue command') }, { value: 'delay', label: t('Delay, then complete') }, { value: 'assert_relation', label: t('Assert relation') }];
  const completeAction = { id: 'finish', kind: 'complete', status: 'completed' };
  const delayReady = kind !== 'delay' || typeof duration === 'number' && Number.isSafeInteger(duration) && duration >= 1;
  const duplicateName = Boolean(pkg && mapping(pkg.chains) && name.trim() in pkg.chains);
  const ready = name.trim() && !duplicateName && predicateId && pkgIndex !== undefined && mapping(predicate) && Object.keys(roles).every(role => roleBindings[role]) && delayReady && (
    kind === 'set' ? targetRole && field && value !== undefined && valueValid : kind === 'emit' ? emitSchema && topic.trim() && payload !== undefined && payloadValid :
      kind === 'command' ? payload !== undefined && payloadValid && (commandCap ? true : Boolean(commandTarget.trim() && emitSchema && messages.find(row => row.id === emitSchema)?.kind === 'command')) :
        kind === 'assert_relation' ? relation && edgeId.trim() && sourceRole && relTargetRole : kind === 'delay');
  const buildPlan = (): RulePlan => {
    if (kind === 'set') return { actions: [{ id: 'action', kind: 'set', entity: { $role: targetRole }, field, value }, completeAction] };
    if (kind === 'emit') return { actions: [{ id: 'action', kind: 'emit', schema: emitSchema, topic: topic.trim(), payload }, completeAction] };
    if (kind === 'command') return commandRulePlan(commandCap ? { id: 'action', kind: 'command', capability: commandCap, payload } : { id: 'action', kind: 'command', schema: emitSchema, target: commandTarget.trim(), payload });
    if (kind === 'assert_relation') return { actions: [{ id: 'action', kind: 'assert_relation', edge_id: edgeId.trim(), relation, source: { $role: sourceRole }, target: { $role: relTargetRole } }, completeAction] };
    return { actions: [{ id: 'wait', kind: 'delay', duration_ns: duration }], transitions: [
      { id: 'wait-timer', from: 'waiting', on: { instance: 'activated' }, to: 'waiting', actions: [{ id: 'wait', kind: 'delay', duration_ns: duration }] },
      { id: 'finish', from: 'waiting', on: { timer: 'wait' }, to: 'done', actions: [completeAction] },
    ] };
  };
  const bindingSummary = (binding: any) => {
    const repeat = binding.multiplicity === 'once_per_task_episode' ? t('for each task episode') : binding.multiplicity === 'once_per_relation' ? t('for each matching relationship') : t('for each matching entity');
    const actors = Object.entries(mapping(binding.match) ? binding.match : {}).map(([role, selector]) => {
      const value = mapping(selector) ? selector.entity ?? selector.is_a : undefined, entity = typeof value === 'string' ? (scenario.entities ?? []).find((row: any) => row.id === value) : undefined;
      return `${readableName(role)}: ${entity ? entityLabel(entity) : mapping(selector) ? selector.is_a ? registryLabel(String(selector.is_a), locale) : selector.entity ? readableName(String(selector.entity)) : t('relation match') : t('unsupported selector')}`;
    }).join(' · ');
    return t('Auto-instantiated {repeat} · {actors}', { repeat, actors });
  };
  return <div className="guided-root">
    <Card title={t('Create a predicate rule')}><p className="guided-muted">{t('When a predicate becomes true, start a chain for the chosen entity or every matching type, then perform an action.')}</p>
      {!predicateOptions.length && <Alert type="info" message={t('Create a predicate in the Predicates step first.')} />}
      <div className="guided-rule-builder"><label>{t('Rule name')}<Input aria-label={t('Rule name')} value={name} onChange={event => setName(event.target.value)} placeholder={t('e.g. incident-alert')} /></label><label>{t('When')}<Select aria-label={t('Rule predicate')} placeholder={t('Choose a predicate')} value={predicateKey} options={predicateOptions} onChange={key => { setPredicateKey(key); setRoleBindings({}); setTargetRole(undefined); setField(undefined); setValue(undefined); setValueValid(true); setSourceRole(undefined); setRelTargetRole(undefined); setRelation(undefined); }} /></label><span className="guided-rule-connector">{t('becomes true')}</span></div>
      {duplicateName && <p role="alert">{t('This rule name already exists. Choose a different name.')}</p>}
      {Object.entries(roles).map(([role, type]) => <label className="guided-rule-role" key={role}>{t('For {role}', { role: readableName(role) })}<Select aria-label={t('Rule entity {role}', { role })} placeholder={t('Choose a {type}', { type: registryLabel(String(type), locale) })} value={roleBindings[role]} options={entityOptions(String(type))} onChange={next => setRoleBindings({ ...roleBindings, [role]: next })} /></label>)}
      <div className="guided-rule-builder guided-rule-actions"><span className="guided-rule-connector">→</span><label>{t('Rule action')}<Select aria-label={t('Rule action kind')} value={kind} options={kindOptions} onChange={next => { setKind(next as RuleKind); setPayloadValid(true); setValueValid(true); }} /></label>
        {kind === 'set' && <><label>{t('Target role')}<Select aria-label={t('Rule action role')} value={targetRole} options={Object.keys(roles).map(role => ({ value: role, label: readableName(role) }))} onChange={next => { setTargetRole(next); setField(undefined); setValue(undefined); }} /></label><label>{t('Field')}<Select aria-label={t('Rule action field')} showSearch optionFilterProp="label" value={field} options={applicable.map(row => ({ value: String(row.id), label: registryLabel(String(row.id), locale) }))} onChange={next => { setField(next); setValue(undefined); setValueValid(true); }} /></label></>}
        {kind === 'emit' && <><label>{t('Event')}<Select aria-label={t('Rule emit event')} placeholder={t('Registry event')} showSearch optionFilterProp="label" value={emitSchema} options={eventOptions} onChange={id => { setEmitSchema(id); setPayload(undefined); }} /></label><label>{t('Topic')}<Input aria-label={t('Rule emit topic')} value={topic} onChange={event => setTopic(event.target.value)} placeholder={t('e.g. ops.events')} /></label></>}
        {kind === 'command' && <><label>{t('By')}<Select aria-label={t('Rule command mode')} value={commandCap ? 'capability' : 'explicit'} options={[{ value: 'capability', label: t('Configured capability'), disabled: !Object.keys(capabilities).length }, { value: 'explicit', label: t('Schema + target') }]} onChange={next => { setCommandCap(next === 'capability' ? commandCap ?? Object.keys(capabilities)[0] : undefined); setPayload(undefined); }} /></label>{commandCap ? <label>{t('Capability')}<Select aria-label={t('Rule command capability')} value={commandCap} options={Object.keys(capabilities).map(id => ({ value: id, label: readableName(id) }))} onChange={id => { setCommandCap(id); setPayload(undefined); }} /></label> : <><label>{t('Command schema')}<Select aria-label={t('Rule command schema')} showSearch optionFilterProp="label" value={emitSchema && commandOptions.some(row => row.value === emitSchema) ? emitSchema : undefined} options={commandOptions} onChange={id => { setEmitSchema(id); setCommandCap(undefined); setPayload(undefined); }} /></label><label>{t('Target')}<Input aria-label={t('Rule command target')} list="rule-command-targets" value={commandTarget} onChange={event => setCommandTarget(event.target.value)} /><datalist id="rule-command-targets">{Object.keys(scenario.engines ?? {}).map(id => <option key={id} value={id} />)}</datalist></label></>}</>}
        {kind === 'delay' && <label>{t('Duration (ns)')}<InputNumber aria-label={t('Rule delay duration')} min={1} max={Number.MAX_SAFE_INTEGER} precision={0} value={duration} onChange={next => setDuration(typeof next === 'number' ? next : undefined)} /></label>}
        {kind === 'assert_relation' && <><label>{t('Relation')}<Select aria-label={t('Rule relation')} showSearch optionFilterProp="label" value={relation} options={relationOptions} onChange={setRelation} /></label><label>{t('Edge ID')}<Input aria-label={t('Rule relation edge')} value={edgeId} onChange={event => setEdgeId(event.target.value)} /></label><label>{t('Source role')}<Select aria-label={t('Rule relation source')} value={sourceRole} options={Object.keys(roles).map(role => ({ value: role, label: readableName(role) }))} onChange={next => { setSourceRole(next); setRelation(undefined); }} /></label><label>{t('Target role')}<Select aria-label={t('Rule relation target')} value={relTargetRole} options={Object.keys(roles).map(role => ({ value: role, label: readableName(role) }))} onChange={next => { setRelTargetRole(next); setRelation(undefined); }} /></label></>}
      </div>
      {kind === 'command' && <p className="guided-muted">{t('The target is the configured engine that executes the command. A capability already includes its target and command schema.')}</p>}
      {kind === 'delay' && <p className="guided-muted">{t('Duration uses nanoseconds: 1,000,000,000 ns = 1 second. Enter a positive whole number.')}</p>}
      {kind === 'set' && field && <TypedPayloadForm key={`field/${field}`} schema={applicable.find(row => row.id === field)?.schema} definitions={schemas} value={value} path={t('Rule state value')} onChange={setValue} onValidityChange={setValueValid} />}
      {kind === 'emit' && emitSchema && <div className="guided-rule-payload"><TypedPayloadForm key={`event/${emitSchema}`} schema={emitSchemaResolved} definitions={schemas} value={payload} path={t('Event payload')} onChange={setPayload} onValidityChange={setPayloadValid} /></div>}
      {kind === 'command' && (commandCap || emitSchema && commandOptions.some(row => row.value === emitSchema)) && <div className="guided-rule-payload"><TypedPayloadForm key={`command/${commandCap ?? emitSchema}`} schema={commandCap ? commandCapSchema : messageSchema(emitSchema)} definitions={schemas} value={payload} path={t('Command payload')} onChange={setPayload} onValidityChange={setPayloadValid} /></div>}
      <Space direction="vertical"><Button disabled={!ready} onClick={() => { try { const match = Object.fromEntries(Object.entries(roles).map(([role, type]) => [role, roleBindings[role] === '@type' ? { is_a: type } : { entity: roleBindings[role] }])); onChange(appendPredicateRule(scenario, pkgIndex!, name, predicateId!, match, {}, buildPlan(), locale)); setName(''); setError(''); } catch (problem) { setError(String(problem)); } }}>{t('Add rule to draft')}</Button>{error && <Alert type="error" message={error} />}<p className="guided-muted">{t('Commands wait for their execution receipt; delays wait for their timer. Edit the state-machine graph below to add subsequent steps.')}</p></Space>
    </Card>
    <Card title={t('Event chains')}><Table size="small" rowKey="key" pagination={{ pageSize: 6 }} dataSource={rows} locale={{ emptyText: t('No event chains yet. Create a predicate rule above or add a chain in the editor below.') }} columns={[
      { title: t('Chain'), dataIndex: 'id', render: readableName },
      { title: t('States'), key: 'states', render: (_, row) => Array.isArray(row.chain.states) ? row.chain.states.map((state: string) => <Tag key={state}>{readableName(state)}</Tag>) : t('No states') },
      { title: t('Binding'), key: 'bindings', render: (_, row) => row.bindings.map((binding: any) => <p key={binding.id} className="guided-binding">{bindingSummary(binding)}</p>) },
    ]} expandable={{ expandedRowRender: row => <Details title={t('{name} chain', { name: readableName(row.id) })} buttonLabel={t('Chain details')}><pre>{JSON.stringify(row.chain, null, 2)}</pre></Details> }} /></Card>
    <Collapse className="guided-rules-editor" defaultActiveKey={['editor']} items={[{ key: 'editor', label: t('State-machine graphs, transitions and actions'), children: <BehavioursPanel {...common} initialTab="chains" /> }]} />
    <Card title={t('Conflict rules & injection points')}>{!inline.length && <p>{t('Add or edit a behaviour package in the editor above to configure conflict rules and external events.')}</p>}{inline.map(({ pkg, index }) => <div key={index}><h3>{readableName(String(pkg.id))}</h3><Table size="small" rowKey="id" pagination={false} dataSource={Array.isArray(pkg.conflicts) ? pkg.conflicts : []} locale={{ emptyText: t('No conflict rules in this package.') }} columns={[
      { title: t('Conflict rule'), dataIndex: 'id', render: readableName }, { title: t('Condition'), dataIndex: 'predicate', render: readableName }, { title: t('Event'), dataIndex: 'emits', render: readableName },
    ]} /><h4>{t('External events an operator can fire')}</h4><Table size="small" rowKey="id" pagination={false} dataSource={Array.isArray(pkg.injection_points) ? pkg.injection_points : []} locale={{ emptyText: t('No external events configured in this package.') }} columns={[
      { title: t('Injection point'), dataIndex: 'id', render: readableName },
      { title: t('Event'), dataIndex: 'emits', render: (id: string) => id ? readableName(id) : t('Typed command') },
      { title: t('Payload fields'), key: 'payload', render: (_, point) => { const schema = scenario.registry?.messages?.find((row: any) => row.id === point.emits)?.schema, resolved = typeof schema === 'string' ? scenario.registry?.schemas?.[schema] : schema; return mapping(resolved?.members) ? Object.keys(resolved.members).map(key => registryLabel(key, locale)).join(', ') : <Details title={t('Payload schema')} buttonLabel={t('Payload schema')}><pre>{JSON.stringify(point.schema ?? schema ?? point, null, 2)}</pre></Details>; } },
    ]} /></div>)}</Card>
  </div>;
}
