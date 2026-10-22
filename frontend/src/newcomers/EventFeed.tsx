import {displayTime} from '../pages/display-time';
import { useMemo, useState } from 'react';
import { categoryMetadata, CategoryIcon, severityLabel } from './categories';
import { useI18n } from '../i18n/I18nProvider';
import { eventNarrative } from './presentation';
import type { BusinessEvent, Category, Severity } from './api';
import { openCauseTrace } from '../trace/bus';
import './results.css';

export interface EventFeedProps {
  events: BusinessEvent[];
  entityNames?:Map<string,string>;
  available?:boolean;onExplore?:()=>void;
  /** Explicit raw-record destination; otherwise derived from the current workbench URL. */
  rawRecordsHref?:string;
  onSelect?: (event: BusinessEvent) => void;
  /** Entity ids currently selected elsewhere (e.g. clicked in the 3D view); matching events are highlighted. */
  selectedEntities?: string[];
}

/**
 * A reachable view of the committed records when no readable business feed was
 * projected. The workbench URL carries the run, not this panel, so derive the
 * destination from it instead of naming a view the workbench does not expose.
 */
function rawRecordsTarget(): string {
  const query = new URLSearchParams(window.location.search);
  const run = query.get('run'), api = query.get('api');
  if (!run) return `/?records=1${api ? `&api=${encodeURIComponent(api)}` : ''}`;
  const mode = query.get('mode') === 'live' ? 'live' : 'replay';
  const cut = query.get('cut');
  return `/agents/${encodeURIComponent(run)}?mode=${mode}${api ? `&api=${encodeURIComponent(api)}` : ''}${cut ? `&cut=${encodeURIComponent(cut)}` : ''}`;
}

const SEVERITIES: Severity[] = ['info', 'notice', 'warning', 'critical'];

/** Severity is always spelled out so color is never the only signal. */
function severityClass(severity: Severity): string {
  return severity === 'warning' || severity === 'critical' ? 'newc-severity newc-severity-' + severity : 'newc-severity';
}

/**
 * Plain-language feed of declared business events. Cards show a category icon,
 * the narrative text, the simulated time and the severity as a readable label.
 * Category chips filter the list; clicking a card reports it via `onSelect`
 * (the Runs page owns what that does, e.g. flying the camera).
 */
export function EventFeed({ events, onSelect, selectedEntities,available=true,onExplore,entityNames,rawRecordsHref }: EventFeedProps) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [category, setCategory] = useState<Category | 'all'>('all');
  const [severity, setSeverity] = useState<Severity | 'all'>('all');

  const categories = useMemo(
    () => Array.from(new Set(events.map(event => event.category))),
    [events],
  );
  const selected = useMemo(() => new Set(selectedEntities ?? []), [selectedEntities]);

  const visible = events.filter(event =>
    (category === 'all' || event.category === category)
    && (severity === 'all' || event.severity === severity));

  return (
    <div className="newc-feed">
      {events.length>0&&<div className="newc-feed-filters" role="group" aria-label={t('Filter events')}>
        {(['all', ...categories] as const).map(name => (
          <button key={name} type="button" className="newc-chip" aria-pressed={category === name}
            onClick={() => setCategory(name)}>
            {name === 'all' ? t('All events') : categoryMetadata(name, locale).label}
          </button>
        ))}
        <span role="separator" aria-orientation="vertical" />
        {(['all', ...SEVERITIES] as const).map(name => (
          <button key={name} type="button" className="newc-chip" aria-pressed={severity === name}
            onClick={() => setSeverity(name)}>
            {name === 'all' ? t('Any importance') : severityLabel(name, locale)}
          </button>
        ))}
      </div>}
      {visible.length === 0 ? (
        <p className="newc-feed-empty" role="status">
          {events.length === 0
            ? (available?t('No events yet at this moment. Follow the run to watch what happens next.'):L('This run has no readable event summaries. Open its raw records instead.', '这次运行没有可读的事件摘要。请改为查看原始记录。'))
            : t('No events match the filters you picked. Clear a filter above to see the rest.')}
          {!available && (onExplore
            ? <button className="console-btn" onClick={onExplore}>{t('Explore recorded activity')}</button>
            : <a className="console-btn" data-testid="event-feed-raw-records" href={rawRecordsHref ?? rawRecordsTarget()}>{t('Explore recorded activity')}</a>)}
        </p>
      ) : (
        <ul className="newc-feed-list">
          {visible.map((event, index) => {
            const style = categoryMetadata(event.category, locale);
            const highlighted = event.entities.some(entity => selected.has(entity));
            const text = eventNarrative(event, locale, entityNames);
            return (
              <li key={`${event.at_ns}-${index}`} className="newc-event-item">
                <button type="button"
                  className={`newc-event${highlighted ? ' is-selected' : ''}`}
                  style={{ borderLeftColor: style.color }}
                  aria-pressed={highlighted}
                  onClick={() => onSelect?.(event)}>
                  <span className="newc-event-icon" style={{ color: style.color }} aria-hidden="true">
                    <CategoryIcon category={event.category} />
                  </span>
                  <span className="newc-event-body">
                    <span className="newc-event-meta">
                      <span className="newc-event-time">{displayTime(event.at_ns)}</span>
                      <span>{style.label}</span>
                      <span className={severityClass(event.severity)}>{severityLabel(event.severity, locale)}</span>
                    </span>
                    <span className="newc-event-text">{text}</span>
                  </span>
                </button>
                {/* Cause lookup requires the original narrative, not translated/name-substituted presentation text. */}
                <button type="button" className="newc-event-why" title={t('Why did this happen?')}
                  aria-label={t('Why did this happen?')}
                  onClick={() => openCauseTrace({ kind: 'event', atNs: event.at_ns, text: event.text, entities: event.entities })}>
                  {t('Why?')}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
