import { useMemo, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { displayTime } from '../pages/display-time';
import { entityId } from '../viewport/bindings';
import { changesFor, momentsFor, writerName, type CauseRef } from './cause-trace';
import './trace.css';

const LIMIT = 8;
/** Notable moments for one entity, each opening the cause-to-effect story. */
export function MomentsList({ store, entity, onOpen }: { store: TemporalFeedStore; entity: EntityKey; onOpen: (ref: CauseRef, cut: number) => void }) {
  const count = store.commits.length, id = entityId(entity);
  const moments = useMemo(() => momentsFor(store, entity), [store, count, id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [all, setAll] = useState(false);
  if (!moments.length) return <p className="inspection-empty">Nothing notable involved this entity (yet). Move the timeline forward.</p>;
  const shown = all ? moments : moments.slice(0, LIMIT);
  return <>
    <ul className="ct-moments">{shown.map(moment => <li key={moment.key}><button type="button" className="ct-moment" onClick={() => onOpen(moment.ref, moment.cut)}>
      {moment.label}<small>{moment.detail} · {displayTime(moment.at.ns, store.header.start.ns)}</small></button></li>)}</ul>
    {moments.length > LIMIT && <button type="button" className="ct-small" onClick={() => setAll(value => !value)}>{all ? 'Show fewer' : `Show all ${moments.length}`}</button>}
  </>;
}

/** What changed for one entity, oldest first, each change tagged with the writer. */
export function ChangeList({ store, entity, onOpen }: { store: TemporalFeedStore; entity: EntityKey; onOpen: (ref: CauseRef, cut: number) => void }) {
  const count = store.commits.length, id = entityId(entity);
  const summary = useMemo(() => changesFor(store, entity), [store, count, id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [all, setAll] = useState(false);
  const known = store.viewCursor?.knownAt ?? Number.MAX_SAFE_INTEGER;
  const rows = summary.rows.filter(row => row.cut <= known);
  if (!rows.length && !summary.continuous.length) return <p className="inspection-empty">No state changes have been recorded for this entity yet.</p>;
  const shown = all ? rows : rows.slice(-LIMIT * 2);
  return <>
    {rows.length > shown.length && <button type="button" className="ct-small" onClick={() => setAll(true)}>Show {rows.length - shown.length} earlier changes</button>}
    <ul className="ct-timeline">{shown.map(row => <li key={row.key}><button type="button" className="ct-row" onClick={() => onOpen(row.ref, row.cut)}>
      <span className="ct-row-time">{displayTime(row.at.ns, store.header.start.ns)}</span>
      <span className="ct-row-delta"><strong>{row.fieldLabel}</strong>: {row.before !== undefined ? <><span className="ct-before">{row.before}</span> → </> : 'set to '}<span className="ct-after">{row.after}</span></span>
      <span className="ct-row-cause">by {row.cause}</span></button></li>)}</ul>
    {summary.continuous.map(item => <p key={item.fieldId} className="ct-note">{item.fieldLabel} updates continuously ({item.updates} times, by the {writerName(item.writer)}); only its latest value is listed in State.</p>)}
  </>;
}
