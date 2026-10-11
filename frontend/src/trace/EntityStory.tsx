import { useEffect, useMemo, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { displayTime } from '../pages/display-time';
import { entityId } from '../viewport/bindings';
import { changesFor, momentsFor, writerName, type CauseRef } from './cause-trace';
import { useI18n } from '../i18n/I18nProvider';
import './trace.css';

const LIMIT = 8;
/** Notable moments for one entity, each opening the cause-to-effect story. */
export function MomentsList({ store, entity, onOpen }: { store: TemporalFeedStore; entity: EntityKey; onOpen: (ref: CauseRef, cut: number) => void }) {
  const { t, locale } = useI18n();
  const count = store.commits.length, id = entityId(entity), cursor = store.viewCursor;
  const moments = useMemo(() => momentsFor(store, entity,locale).filter(moment => !cursor || moment.cut <= cursor.knownAt && (BigInt(moment.at.ns) < BigInt(cursor.validAt.ns) || moment.at.ns === cursor.validAt.ns && moment.at.microstep <= cursor.validAt.microstep)), [store, count, id, locale, cursor?.knownAt, cursor?.validAt.ns, cursor?.validAt.microstep]); // eslint-disable-line react-hooks/exhaustive-deps
  const [all, setAll] = useState(false);
  useEffect(() => setAll(false), [store,id]);
  if (!moments.length) return <p className="inspection-empty">{t('Nothing notable involved this entity (yet). Move the timeline forward.')}</p>;
  const shown = all ? moments : moments.slice(0, LIMIT);
  return <>
    <ul className="ct-moments">{shown.map(moment => <li key={moment.key}><button type="button" className="ct-moment" onClick={() => onOpen(moment.ref, moment.cut)}>
      {moment.label}<small>{moment.detail} · {displayTime(moment.at.ns, store.header.start.ns)}</small></button></li>)}</ul>
    {moments.length > LIMIT && <button type="button" className="ct-small" onClick={() => setAll(value => !value)}>{all ? t('Show fewer') : t('Show all {count}', {count:moments.length})}</button>}
  </>;
}

/** What changed for one entity, oldest first, each change tagged with the writer. */
export function ChangeList({ store, entity, onOpen }: { store: TemporalFeedStore; entity: EntityKey; onOpen: (ref: CauseRef, cut: number) => void }) {
  const { t, locale } = useI18n();
  const count = store.commits.length, id = entityId(entity), known = store.viewCursor?.knownAt ?? Number.MAX_SAFE_INTEGER;
  const summary = useMemo(() => changesFor(store, entity,24,locale), [store, count, id, known, locale]); // eslint-disable-line react-hooks/exhaustive-deps
  const [all, setAll] = useState(false);
  useEffect(() => setAll(false), [store,id]);
  const rows = summary.rows;
  if (!rows.length && !summary.continuous.length) return <p className="inspection-empty">{t('No state changes have been recorded for this entity yet.')}</p>;
  const shown = all ? rows : rows.slice(-LIMIT * 2);
  return <>
    {rows.length > shown.length && <button type="button" className="ct-small" onClick={() => setAll(true)}>{t('Show {count} earlier changes', {count:rows.length-shown.length})}</button>}
    <ul className="ct-timeline">{shown.map(row => <li key={row.key}><button type="button" className="ct-row" onClick={() => onOpen(row.ref, row.cut)}>
      <span className="ct-row-time">{displayTime(row.at.ns, store.header.start.ns)}</span>
      <span className="ct-row-delta"><strong>{row.fieldLabel}</strong>: {row.before !== undefined ? <><span className="ct-before">{row.before}</span> → </> : t('set to ')}<span className="ct-after">{row.after}</span></span>
      <span className="ct-row-cause">{t('by {cause}', {cause:row.cause})}</span></button></li>)}</ul>
    {summary.continuous.map(item => <p key={item.fieldId} className="ct-note">{t('{field} updates continuously ({count} times, by the {writer}); only its latest value is listed in State.', {field:item.fieldLabel,count:item.updates,writer:writerName(item.writer,locale)})}</p>)}
  </>;
}
