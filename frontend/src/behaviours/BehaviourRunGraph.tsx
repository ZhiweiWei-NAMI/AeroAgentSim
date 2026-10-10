import { ConceptHelp } from '../console/ConceptHelp';
import { useMemo, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { Details } from '../console/Details';
import { EntityGraph } from './EntityGraph';
import { FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
import './run-graph.css';
import { mapping } from './model';
import { displayTime } from '../pages/display-time';
import { displayLabel, entityLabel, readableLabel, stateValue } from '../pages/inspection-format';
import { useI18n } from '../i18n/I18nProvider';

interface Props { store: TemporalFeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void; onSeek: (index: number) => void; highlights?: EntityKey[] }
/** Hide background actors together with their exclusively associated task/route context. */
export function backgroundContext(store:TemporalFeedStore):Set<string> {
  const links=new Map<string,Set<string>>();
  const connect=(a:string,b:string)=>{for(const [source,target] of [[a,b],[b,a]]){const peers=links.get(source)??new Set<string>();peers.add(target);links.set(source,peers);}};
  const references=(value:unknown):EntityKey[]=>{
    if(Array.isArray(value))return value.flatMap(references);
    if(!mapping(value))return [];
    if(mapping(value.$ref)) {
      const ref=value.$ref,generation=mapping(ref.generation)?ref.generation.$integer:ref.generation;
      return typeof ref.id==='string'&&(typeof generation==='string'||typeof generation==='number')?[{id:ref.id,generation}]:[];
    }
    return Object.values(value).flatMap(references);
  };
  for(const [id,entity] of store.entities)for(const fact of entity.fields.values())for(const ref of references(fact.value))if(store.entities.has(entityId(ref)))connect(id,entityId(ref));
  for(const edge of store.edges.values())connect(entityId(edge.source),entityId(edge.target));
  const context=(id:string)=>['aas:TrafficTask','aas:TrafficRoute'].includes(store.entities.get(id)?.typeId??'');
  const walk=(seeds:string[])=>{const found=new Set(seeds),queue=[...seeds];for(let i=0;i<queue.length;i++)for(const peer of links.get(queue[i])??[])if(context(peer)&&!found.has(peer)){found.add(peer);queue.push(peer);}return found;};
  const background:string[]=[],foreground:string[]=[];
  for(const [id,entity] of store.entities){const role=entity.fields.get('traffic.actor.role')?.value;if(role==='background')background.push(id);else if(typeof role==='string')foreground.push(id);}
  const hidden=walk(background),shared=walk(foreground);for(const id of shared)hidden.delete(id);return hidden;
}
/** Composition of Q7's recorded topology and the journaled behaviour extension. */
export function BehaviourRunGraph({ store, selected, onSelect, onSeek, highlights }: Props) {
  const { t, locale } = useI18n();
  const [hideBackground, setHideBackground] = useState(true), [showRuntimeEntities, setShowRuntimeEntities] = useState(false);
  const [directory, setDirectory] = useState(''), [type, setType] = useState(''), [focus, setFocus] = useState(false);
  const types = new Map(store.header.types.map(row => [row.typeId, row]));
  const selectedId = selected && entityId(selected);
  const startNs = store.header.start.ns;
  const background = useMemo(() => backgroundContext(store), [store, store.viewCursor?.knownAt, store.viewCursor?.validAt.ns, store.viewCursor?.validAt.microstep]);
  const highlightKey = (highlights ?? []).map(entityId).join('|');
  const pinned = new Set((highlights ?? []).map(entityId));
  if (selectedId) pinned.add(selectedId);
  const neighbors = new Set(selectedId ? [selectedId] : []);
  if (selectedId) for (const edge of store.edges.values()) if (entityId(edge.source) === selectedId || entityId(edge.target) === selectedId) {
    neighbors.add(entityId(edge.source)); neighbors.add(entityId(edge.target));
  }
  // A read-only graph snapshot for filters; selected/highlighted identities remain visible.
  const display = useMemo(() => {
    const display = new FeedStore(store.header);
    for (const [id, entity] of store.entities) {
      if (pinned.has(id) || (showRuntimeEntities || entity.typeId !== 'aas:BehaviourInstance' || type === entity.typeId) && (!hideBackground || !background.has(id)) && (!type || entity.typeId === type) && (!directory || types.get(entity.typeId)?.directory === directory) && (!focus || !selectedId || neighbors.has(id))) display.entities.set(id, entity);
    }
    for (const [id, edge] of store.edges) if (display.entities.has(entityId(edge.source)) && display.entities.has(entityId(edge.target))) display.edges.set(id, edge);
    display.commits.push(...store.commits);
    return display;
  }, [store, store.viewCursor?.knownAt, store.viewCursor?.validAt.ns, store.viewCursor?.validAt.microstep, directory, type, focus, selectedId, hideBackground, showRuntimeEntities, background, highlightKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const related = (roles: Record<string, EntityKey>) => (!hideBackground || Object.values(roles).some(key => !background.has(entityId(key)) || pinned.has(entityId(key)))) && (!focus || !selectedId || Object.values(roles).some(key => neighbors.has(entityId(key))));
  const predicates = [...store.predicateTruth.values()].filter(row => related(row.roles));
  const chains = [...store.chainInstances.values()].filter(row => related(row.roles));
  const extensionPresent = store.hasPredicateRecords || store.hasChainRecords;
  const roleLinks = (roles: Record<string, EntityKey>) => Object.entries(roles).map(([role, key]) => <button type="button" key={role} onClick={() => onSelect(key)}>{readableLabel(role, locale)} · {store.entities.get(entityId(key)) ? entityLabel(store.entities.get(entityId(key))!, locale) : readableLabel(key.id, locale)}</button>);
  const interval = (from: { ns: string; microstep: number }, to?: { ns: string; microstep: number } | null) => `[${displayTime(from.ns, startNs)}, ${to ? displayTime(to.ns, startNs) : t('open')})`;
  const truthLabel = (value: boolean | null) => value === null ? t('unknown') : value ? t('true') : t('false');
  const directoryLabel = (value: string) => {
    switch (value) {
      case 'objects': return t('Objects');
      case 'tasks': return t('Tasks');
      case 'environment': return t('Environment');
      case 'runtime': return t('Runtime');
      default: return readableLabel(value, locale);
    }
  };
  const diagnosticLabel = (item: unknown) => {
    const reason = typeof item === 'string' ? item : mapping(item) ? item.reason : undefined;
    switch (reason) {
      case 'required source history missing': return t('required source history missing');
      case 'missing phase': return t('missing phase');
      case 'history or operator input undetermined': return t('history or operator input undetermined');
      default: return undefined; // unrecognized technical records remain exact inside Details
    }
  };
  const lifecycleLabel = (value: string) => {
    switch (value) {
      case 'created': return t('created');
      case 'transitioned': return t('transitioned');
      case 'completed': return t('completed');
      case 'failed': return t('failed');
      case 'canceled': return t('canceled');
      default: return value;
    }
  };
  return <section className="behaviour-run-graph" aria-label={t('Synchronized AeroGraph view')} data-cut={store.viewCursor?.knownAt}>
    <div className="run-graph-heading"><h2>{t('Entity graph')}<ConceptHelp topic={t('Entity graph')} description={t('The graph includes spatial and nonspatial entities, their relationships, predicates and running chain instances.')} guide="console.md" /></h2><span>{t('{entities} visible · {relations} relations', { entities: display.entities.size, relations: display.edges.size })}</span></div>
    <div className="behaviour-filters">
      <label>{t('Directory')} <select aria-label={t('Graph directory')} value={directory} onChange={event => setDirectory(event.target.value)}><option value="">{t('All')}</option>{[...new Set(store.header.types.flatMap(row => row.directory ? [row.directory] : []))].sort().map(value => <option key={value} value={value}>{directoryLabel(value)}</option>)}</select></label>
      <label>{t('Type')} <select aria-label={t('Graph type')} value={type} onChange={event => setType(event.target.value)}><option value="">{t('All')}</option>{store.header.types.map(row => <option key={row.typeId} value={row.typeId}>{displayLabel(row.displayName, row.typeId, locale)}</option>)}</select></label>
      <label title={!selectedId ? t('Select an entity to focus on its neighbors.') : undefined}><input type="checkbox" disabled={!selectedId} checked={focus} onChange={event => setFocus(event.target.checked)} />{t('Focus + neighbors')}</label>
      <label><input aria-label={t('Hide background actors')} type="checkbox" checked={hideBackground} onChange={event => setHideBackground(event.target.checked)} />{t('Hide background')}</label>
      <label><input aria-label={t('Show runtime entities')} type="checkbox" checked={showRuntimeEntities} onChange={event => setShowRuntimeEntities(event.target.checked)} />{t('Runtime entities')}</label>
      <span>{t('{visible} / {total} entities', { visible: display.entities.size, total: store.entities.size })}</span>
    </div>
    <EntityGraph store={display} selected={selected} onSelect={onSelect} highlights={highlights} />
    <Details title={t('Relation validity and technical records')} buttonLabel={t('Details')}><pre>{exactValue([...display.edges.values()])}</pre></Details>
    <details className="behaviour-records" aria-label={t('Recorded predicate and chain nodes')}><summary>{t('Conditions and event chains · {predicates} conditions · {chains} chains', { predicates: predicates.length, chains: chains.length })}</summary>
      <p>{t('Recorded condition results and chain transitions at this timeline moment. Use a seek button to inspect the corresponding evaluation or transition.')}</p>
      {!extensionPresent && <p>{t('No recorded predicate/chain data in this feed.')}</p>}
      <div className="behaviour-node-columns">
        <div><h3>{t('Predicate contexts · {count}', { count: predicates.length })}</h3>{extensionPresent && !predicates.length && <p>{t('No predicate contexts at this cut.')}</p>}{predicates.map(row => <article className={`behaviour-node truth-${row.value === null ? 'unknown' : String(row.value)}`} key={row.contextId}>
          <strong>{readableLabel(row.predicateId, locale)}</strong><span>{row.status === 'known' ? truthLabel(row.value) : t('unknown · {status}', { status: row.status === 'required_input' ? t('required input') : t('invalid input') })}</span>
          <div className="role-links">{roleLinks(row.roles)}</div>
          <p>{t('Interval {interval}', { interval: interval(row.validFrom, row.validTo) })}</p>
          <button type="button" data-testid="predicate-transition" data-transition-cut={row.readCut.index} disabled={!store.commits.some(commit => commit.commitIndex === row.readCut.index)} onClick={() => onSeek(row.readCut.index)}>{t('Seek evaluation {time}', { time: displayTime(row.readCut.at.ns, startNs) })}</button>
          {!!row.diagnostics.length && <><ul>{row.diagnostics.map((item, index) => { const label = diagnosticLabel(item); return label ? <li key={index}>{label}</li> : null; })}</ul><Details title={t('Predicate diagnostics')} buttonLabel={t('Details')}><pre>{exactValue(row.diagnostics)}</pre></Details></>}
          <Details title={row.predicateId} buttonLabel={t('Intervals, clocks and causes')}>
            <pre>{exactValue(row)}</pre>
            <ol>{store.predicateRecords(row.contextId).filter(item => item.commit.commitIndex <= (store.viewCursor?.knownAt ?? -1)).map((item, index) => <li key={index}><button type="button" data-testid="predicate-transition" data-transition-cut={item.commit.commitIndex} onClick={() => onSeek(item.commit.commitIndex)}>{t('Predicate {value} · {operation} · {interval}', { value: truthLabel(item.value.value), operation: item.value.op === 'close' ? t('close') : t('assert'), interval: interval(item.value.validFrom, item.value.validTo) })}</button></li>)}</ol>
          </Details>
        </article>)}</div>
        <div><h3>{t('Chain instances · {count}', { count: chains.length })}</h3>{extensionPresent && !chains.length && <p>{t('No chain instances at this cut.')}</p>}{chains.map(row => <article className="behaviour-node chain-node" key={row.instanceId}>
          <strong>{readableLabel(row.templateId, locale)}</strong><span>{stateValue(row.state, locale)} · {lifecycleLabel(row.lifecycle)}</span>
          <div className="role-links">{roleLinks(row.roles)}</div>
          <Details title={row.templateId} buttonLabel={t('Variables / children / causes')}><pre>{exactValue(row)}</pre></Details>
          <ol>{store.chainRecords(row.instanceId).filter(item => item.value.op !== 'close' && item.commit.commitIndex <= (store.viewCursor?.knownAt ?? -1)).map((item, ordinal) => <li key={`${item.commit.commitIndex}/${exactValue(item.value.revision)}/${ordinal}`}>
            <button type="button" data-testid="chain-transition" data-transition-cut={item.commit.commitIndex} onClick={() => { const key = Object.values(item.value.roles)[0]; if (key) onSelect(key); onSeek(item.commit.commitIndex); }}>{t('Seek {transition} → {state} · {time}', { transition: item.value.transitionId ?? lifecycleLabel(item.value.lifecycle), state: stateValue(item.value.state, locale), time: displayTime(item.commit.at.ns, startNs) })}</button>
          </li>)}</ol>
        </article>)}</div>
      </div>
    </details>
  </section>;
}
