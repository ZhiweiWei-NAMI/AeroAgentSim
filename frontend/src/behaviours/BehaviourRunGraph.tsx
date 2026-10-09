import { useMemo, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { NonspatialViews } from '../viewport/NonspatialViews';
import { FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
import './run-graph.css';

interface Props { store: TemporalFeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void; onSeek: (index: number) => void }
/** Composition of Q7's recorded topology and the journaled behaviour extension. */
export function BehaviourRunGraph({ store, selected, onSelect, onSeek }: Props) {
  const [directory, setDirectory] = useState(''), [type, setType] = useState(''), [focus, setFocus] = useState(false);
  const types = new Map(store.header.types.map(row => [row.typeId, row]));
  const selectedId = selected && entityId(selected);
  const neighbors = new Set(selectedId ? [selectedId] : []);
  if (selectedId) for (const edge of store.edges.values()) if (entityId(edge.source) === selectedId || entityId(edge.target) === selectedId) {
    neighbors.add(entityId(edge.source)); neighbors.add(entityId(edge.target));
  }
  // A read-only graph snapshot for filters; the session store still owns time and selection.
  const display = useMemo(() => {
  const display = new FeedStore(store.header);
  for (const [id, entity] of store.entities) if ((!type || entity.typeId === type) && (!directory || types.get(entity.typeId)?.directory === directory) && (!focus || !selectedId || neighbors.has(id))) display.entities.set(id, entity);
  for (const [id, edge] of store.edges) if (display.entities.has(entityId(edge.source)) && display.entities.has(entityId(edge.target))) display.edges.set(id, edge);
  display.commits.push(...store.commits);
  return display;
  }, [store, store.viewCursor?.knownAt, store.viewCursor?.validAt.ns, store.viewCursor?.validAt.microstep, directory, type, focus, selectedId]);
  const related = (roles: Record<string, EntityKey>) => !focus || !selectedId || Object.values(roles).some(key => neighbors.has(entityId(key)));
  const predicates = [...store.predicateTruth.values()].filter(row => related(row.roles));
  const chains = [...store.chainInstances.values()].filter(row => related(row.roles));
  const extensionPresent = store.hasPredicateRecords || store.hasChainRecords;
  const roleLinks = (roles: Record<string, EntityKey>) => Object.entries(roles).map(([role, key]) => <button key={role} onClick={() => onSelect(key)}>{role} → {key.id} · g{key.generation}</button>);
  return <section className="behaviour-run-graph" aria-label="Synchronized AeroGraph view" data-cut={store.viewCursor?.knownAt}>
    <h2>AeroGraph · committed run</h2>
    <div className="behaviour-filters">
      <label>Directory <select aria-label="Graph directory" value={directory} onChange={event => setDirectory(event.target.value)}><option value="">All</option>{[...new Set(store.header.types.flatMap(row => row.directory ? [row.directory] : []))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Type <select aria-label="Graph type" value={type} onChange={event => setType(event.target.value)}><option value="">All</option>{store.header.types.map(row => <option key={row.typeId} value={row.typeId}>{row.displayName}</option>)}</select></label>
      <label><input type="checkbox" checked={focus} onChange={event => setFocus(event.target.checked)} />Focus + neighbors</label>
      <span>{display.entities.size} / {store.entities.size} entities</span>
    </div>
    <NonspatialViews store={display} selected={selected} onSelect={onSelect} view="graph" />
    <div className="behaviour-records" aria-label="Recorded predicate and chain nodes">
      {!extensionPresent && <p>No recorded predicate/chain data in this feed.</p>}
      <div className="behaviour-node-columns">
        <div><h3>Predicate contexts · {predicates.length}</h3>{predicates.map(row => <article className={`behaviour-node truth-${row.value === null ? 'unknown' : String(row.value)}`} key={row.contextId}>
          <strong>{row.predicateId}</strong><span>{row.status === 'known' ? String(row.value) : `unknown · ${row.status}`}</span>
          <small>{row.contextId} · {row.profile}</small><div className="role-links">{roleLinks(row.roles)}</div>
          <p>Evaluation interval [{row.validFrom.ns}:{row.validFrom.microstep}, {row.validTo ? `${row.validTo.ns}:${row.validTo.microstep}` : 'open'})</p>
          <button disabled={!store.commits.some(commit => commit.commitIndex === row.readCut.index)} onClick={() => onSeek(row.readCut.index)}>Seek evidence cut {row.readCut.index}</button>
          {!!row.diagnostics.length && <pre>{exactValue(row.diagnostics)}</pre>}
          <details><summary>Intervals, clocks and causes</summary><pre>{exactValue(row)}</pre>
            <ol>{store.predicateRecords(row.contextId).filter(item=>item.commit.commitIndex<=(store.viewCursor?.knownAt??-1)).map((item,index)=><li key={index}><button onClick={()=>onSeek(item.commit.commitIndex)}>Predicate {item.value.value===null?'unknown':String(item.value.value)} · {item.value.op??'assert'} · [{item.value.validFrom.ns}:{item.value.validFrom.microstep}, {item.value.validTo?`${item.value.validTo.ns}:${item.value.validTo.microstep}`:'open'}) · cut {item.commit.commitIndex}</button></li>)}</ol>
          </details>
        </article>)}</div>
        <div><h3>Chain instances · {chains.length}</h3>{chains.map(row => <article className="behaviour-node chain-node" key={row.instanceId}>
          <strong>{row.templateId}</strong><span>{row.state} · {row.lifecycle} · revision {exactValue(row.revision)}</span>
          <small>{row.instanceId} · {row.bindingId}</small><div className="role-links">{roleLinks(row.roles)}</div>
          <details><summary>Variables / children / causes</summary><pre>{exactValue(row)}</pre></details>
          <ol>{store.chainRecords(row.instanceId).filter(item => item.value.op !== 'close' && item.commit.commitIndex <= (store.viewCursor?.knownAt ?? -1)).map(item => <li key={`${item.commit.commitIndex}/${exactValue(item.value.revision)}`}>
            <button onClick={() => { const key = Object.values(item.value.roles)[0]; if (key) onSelect(key); onSeek(item.commit.commitIndex); }}>Seek {item.value.transitionId ?? item.value.lifecycle} → {item.value.state} · {item.commit.at.ns}:{item.commit.at.microstep} · cut {item.commit.commitIndex}</button>
          </li>)}</ol>
        </article>)}</div>
      </div>
    </div>
  </section>;
}
