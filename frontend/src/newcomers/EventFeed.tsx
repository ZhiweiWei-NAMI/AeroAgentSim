import {displayTime} from '../pages/display-time';
import { useMemo, useState } from 'react';
import { CATEGORY_STYLE, CategoryIcon } from './categories';
import type { BusinessEvent, Category, Severity } from './api';
import './results.css';

export interface EventFeedProps {
  events: BusinessEvent[];
  entityNames?:Map<string,string>;
  available?:boolean;onExplore?:()=>void;
  onSelect?: (event: BusinessEvent) => void;
  /** Entity ids currently selected elsewhere (e.g. clicked in the 3D view); matching events are highlighted. */
  selectedEntities?: string[];
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
export function EventFeed({ events, onSelect, selectedEntities,available=true,onExplore,entityNames }: EventFeedProps) {
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
      {events.length>0&&<div className="newc-feed-filters" role="group" aria-label="Filter events">
        {(['all', ...categories] as const).map(name => (
          <button key={name} type="button" className="newc-chip" aria-pressed={category === name}
            onClick={() => setCategory(name)}>
            {name === 'all' ? 'All events' : CATEGORY_STYLE[name].label}
          </button>
        ))}
        <span role="separator" aria-orientation="vertical" />
        {(['all', ...SEVERITIES] as const).map(name => (
          <button key={name} type="button" className="newc-chip" aria-pressed={severity === name}
            onClick={() => setSeverity(name)}>
            {name === 'all' ? 'Any importance' : name}
          </button>
        ))}
      </div>}
      {visible.length === 0 ? (
        <p className="newc-feed-empty" role="status">
          {events.length === 0
            ? (available?'No events yet at this moment. Follow the run to watch what happens next.':'This run does not include readable event updates. Open Expert view to explore its recorded activity.')
            : 'No events match the filters you picked. Clear a filter above to see the rest.'}
          {!available&&onExplore&&<button className="console-btn" onClick={onExplore}>Explore recorded activity</button>}
        </p>
      ) : (
        <ul className="newc-feed-list">
          {visible.map((event, index) => {
            const style = CATEGORY_STYLE[event.category];
            const highlighted = event.entities.some(entity => selected.has(entity));
            return (
              <li key={`${event.at_ns}-${index}`}>
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
                      <span className={severityClass(event.severity)}>{event.severity}</span>
                    </span>
                    <span className="newc-event-text">{event.entities.reduce((text,id)=>text.replace(new RegExp(`(?<![\\w-])${id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}(?![\\w-])`,'g'),entityNames?.get(id)??id),event.text)}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
