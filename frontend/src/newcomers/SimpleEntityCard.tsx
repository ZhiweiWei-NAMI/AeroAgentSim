import type {TemporalFeedStore} from '../feeds/temporal-store';
import {useI18n} from '../i18n/I18nProvider';
import {displayTime} from '../pages/display-time';
import {openCauseTrace} from '../trace/bus';
import {displayName} from '../viewport/semantic';
import {eventNarrative} from './presentation';
import type {RecordedBusinessEvent} from './projection';
import type {EntityStory} from './entity-story';

export function SimpleEntityCard({store,story,entityNames,onFollow,onEvent,onWhy}:{store:TemporalFeedStore;story:EntityStory;entityNames:Map<string,string>;onFollow:()=>void;onEvent:(event:RecordedBusinessEvent)=>void;onWhy:()=>void}){
 const {t,locale}=useI18n();
 return <aside className="simple-entity-card" data-testid="simple-entity-card" aria-label={t('Selected entity')}>
  <header><strong>{story.label}</strong><span>{story.battery===undefined?t('Battery not recorded'):t('Battery {percent}%',{percent:Math.round(story.battery*100)})}</span></header>
  <div className="simple-entity-business"><span>{story.business}</span><span>{t('Class: {name}',{name:story.vehicleClass})}</span></div>
  <dl><div><dt>{t('Current task')}</dt><dd>{story.task?displayName(story.task,store.header,locale):t('No active task recorded')}</dd></div><div><dt>{t('Next stop')}</dt><dd>{story.nextStop??t('Not recorded')}</dd></div></dl>
  <button className="simple-entity-status" data-testid="entity-status-line" title={story.status} disabled={!story.cause} onClick={()=>{if(story.cause)openCauseTrace(story.cause);}}>{story.status}</button>
  <div className="simple-entity-actions"><button className="console-btn" onClick={onFollow}>{t('Follow')}</button><button className="console-btn" disabled={!story.latestEvent} onClick={onWhy}>{t('Why?')}</button></div>
  <div className="simple-journey"><h3>{t('Journey')}</h3>{!story.journey.length?<p>{t('No business events recorded for this entity yet.')}</p>:<ol>{story.journey.map(event=>{const narrative=eventNarrative(event,locale,entityNames);return <li key={event.key}><time>{displayTime(event.at_ns,store.header.start.ns)}</time><button title={narrative} onClick={()=>onEvent(event)}>{narrative}</button></li>;})}</ol>}</div>
 </aside>;
}
