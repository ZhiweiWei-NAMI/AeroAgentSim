import { useMemo, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue, seconds } from '../feeds/format';
import { Details } from '../console/Details';
import { NonspatialViews } from '../viewport/NonspatialViews';
import { FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
import './run-graph.css';
import {mapping} from './model';

interface Props { store: TemporalFeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void; onSeek: (index: number) => void }
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
export function BehaviourRunGraph({ store, selected, onSelect, onSeek }: Props) {
  const [hideBackground,setHideBackground]=useState(true), [showRuntimeEntities,setShowRuntimeEntities]=useState(false);
  const [directory, setDirectory] = useState(''), [type, setType] = useState(''), [focus, setFocus] = useState(false);
  const types = new Map(store.header.types.map(row => [row.typeId, row]));
  const selectedId = selected && entityId(selected);
  const startNs = store.header.start.ns;
  const background=useMemo(()=>backgroundContext(store),[store,store.viewCursor?.knownAt,store.viewCursor?.validAt.ns,store.viewCursor?.validAt.microstep]);
  const neighbors = new Set(selectedId ? [selectedId] : []);
  if (selectedId) for (const edge of store.edges.values()) if (entityId(edge.source) === selectedId || entityId(edge.target) === selectedId) {
    neighbors.add(entityId(edge.source)); neighbors.add(entityId(edge.target));
  }
  // A read-only graph snapshot for filters; the session store still owns time and selection.
  const display = useMemo(() => {
  const display = new FeedStore(store.header);
  for (const [id, entity] of store.entities) if ((showRuntimeEntities || entity.typeId !== 'aas:BehaviourInstance' || type === entity.typeId) && (!hideBackground || !background.has(id)) && (!type || entity.typeId === type) && (!directory || types.get(entity.typeId)?.directory === directory) && (!focus || !selectedId || neighbors.has(id))) display.entities.set(id, entity);
  for (const [id, edge] of store.edges) if (display.entities.has(entityId(edge.source)) && display.entities.has(entityId(edge.target))) display.edges.set(id, edge);
  display.commits.push(...store.commits);
  return display;
  }, [store, store.viewCursor?.knownAt, store.viewCursor?.validAt.ns, store.viewCursor?.validAt.microstep, directory, type, focus, selectedId,hideBackground,showRuntimeEntities,background]);
  const related = (roles: Record<string, EntityKey>) => (!hideBackground || Object.values(roles).some(key => !background.has(entityId(key)))) && (!focus || !selectedId || Object.values(roles).some(key => neighbors.has(entityId(key))));
  const predicates = [...store.predicateTruth.values()].filter(row => related(row.roles));
  const chains = [...store.chainInstances.values()].filter(row => related(row.roles));
  const extensionPresent = store.hasPredicateRecords || store.hasChainRecords;
  const roleLinks = (roles: Record<string, EntityKey>) => Object.entries(roles).map(([role, key]) => <button key={role} onClick={() => onSelect(key)}>{role} → {key.id} · g{key.generation}</button>);
  const interval = (from: { ns: string; microstep: number }, to?: { ns: string; microstep: number } | null) => `[${seconds(from.ns, startNs)}, ${to ? seconds(to.ns, startNs) : 'open'})`;
  return <section className="behaviour-run-graph" aria-label="Synchronized AeroGraph view" data-cut={store.viewCursor?.knownAt}>
    <h2>AeroGraph · committed run</h2>
    <div className="behaviour-filters">
      <label>Directory <select aria-label="Graph directory" value={directory} onChange={event => setDirectory(event.target.value)}><option value="">All</option>{[...new Set(store.header.types.flatMap(row => row.directory ? [row.directory] : []))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Type <select aria-label="Graph type" value={type} onChange={event => setType(event.target.value)}><option value="">All</option>{store.header.types.map(row => <option key={row.typeId} value={row.typeId}>{row.displayName}</option>)}</select></label>
      <label><input type="checkbox" checked={focus} onChange={event => setFocus(event.target.checked)} />Focus + neighbors</label>
      <label><input aria-label="Hide background actors" type="checkbox" checked={hideBackground} onChange={e=>setHideBackground(e.target.checked)}/>Hide background actors</label><label><input aria-label="Show runtime entities" type="checkbox" checked={showRuntimeEntities} onChange={e=>setShowRuntimeEntities(e.target.checked)}/>Show runtime entities (also available in chain overlays)</label><span>{display.entities.size} / {store.entities.size} entities</span>
    </div>
    <NonspatialViews store={display} selected={selected} onSelect={onSelect} view="graph" /><Details title="Relation validity and technical records"><pre>{exactValue([...display.edges.values()])}</pre></Details>
    <div className="behaviour-records" aria-label="Recorded predicate and chain nodes">
      {!extensionPresent && <p>No recorded predicate/chain data in this feed.</p>}
      <div className="behaviour-node-columns">
        <div><h3>Predicate contexts · {predicates.length}</h3>{predicates.map(row => <article className={`behaviour-node truth-${row.value === null ? 'unknown' : String(row.value)}`} key={row.contextId}>
          <strong>{row.predicateId}</strong><span>{row.status === 'known' ? String(row.value) : `unknown · ${row.status}`}</span>
          <small>{row.profile}</small><div className="role-links">{roleLinks(row.roles)}</div>
          <p>Interval {interval(row.validFrom, row.validTo)}</p>
          <button data-testid="predicate-transition" data-transition-cut={row.readCut.index} disabled={!store.commits.some(commit => commit.commitIndex === row.readCut.index)} onClick={() => onSeek(row.readCut.index)}>Seek evaluation {seconds(row.readCut.at.ns, startNs)}</button>
          {!!row.diagnostics.length && <><ul>{row.diagnostics.map((item,index)=>typeof item==='string'?<li key={index}>{item}</li>:mapping(item)&&typeof item.reason==='string'?<li key={index}>{item.reason}</li>:null)}</ul><Details title="Predicate diagnostics"><pre>{exactValue(row.diagnostics)}</pre></Details></>}
          <Details title={row.predicateId} buttonLabel="Intervals, clocks and causes">
            <pre>{exactValue(row)}</pre>
            <ol>{store.predicateRecords(row.contextId).filter(item=>item.commit.commitIndex<=(store.viewCursor?.knownAt??-1)).map((item,index)=><li key={index}><button data-testid="predicate-transition" data-transition-cut={item.commit.commitIndex} onClick={()=>onSeek(item.commit.commitIndex)}>Predicate {item.value.value===null?'unknown':String(item.value.value)} · {item.value.op??'assert'} · {interval(item.value.validFrom, item.value.validTo)}</button></li>)}</ol>
          </Details>
        </article>)}</div>
        <div><h3>Chain instances · {chains.length}</h3>{chains.map(row => <article className="behaviour-node chain-node" key={row.instanceId}>
          <strong>{row.templateId}</strong><span>{row.state} · {row.lifecycle}</span>
          <small>{row.bindingId}</small><div className="role-links">{roleLinks(row.roles)}</div>
          <Details title={row.templateId} buttonLabel="Variables / children / causes"><pre>{exactValue(row)}</pre></Details>
          <ol>{store.chainRecords(row.instanceId).filter(item => item.value.op !== 'close' && item.commit.commitIndex <= (store.viewCursor?.knownAt ?? -1)).map(item => <li key={`${item.commit.commitIndex}/${exactValue(item.value.revision)}`}>
            <button data-testid="chain-transition" data-transition-cut={item.commit.commitIndex} onClick={() => { const key = Object.values(item.value.roles)[0]; if (key) onSelect(key); onSeek(item.commit.commitIndex); }}>Seek {item.value.transitionId ?? item.value.lifecycle} → {item.value.state} · {seconds(item.commit.at.ns, startNs)}</button>
          </li>)}</ol>
        </article>)}</div>
      </div>
    </div>
  </section>;
}
