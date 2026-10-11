import { useMemo } from 'react';
import { Button, Select, Slider } from 'antd';
import type { FeedCommit } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import type { PlaybackClock } from '../viewport/clock';
import { displayTime } from './display-time';
import { ConceptHelp } from '../console/ConceptHelp';
import { useI18n } from '../i18n/I18nProvider';
import { categoryMetadata } from '../newcomers/categories';
import { readableLabel } from './inspection-format';

const markers: Record<string, {label: string; kind: string}> = {
  'traffic.inject.accident': {label:'Accident',kind:'accident'},
  'traffic.accident': {label:'Accident',kind:'accident'},
  'traffic.award.committed': {label:'Award',kind:'award'},
  'traffic.capture.stored': {label:'Capture',kind:'capture'},
};
export function timelineEvents(commits: FeedCommit[], locale = 'en-US') {
  return commits.flatMap((commit, offset) => {
    const found = new Map<string, {label:string;kind:string;detail?:string}>();
    for (const message of commit.messages) if (message.kind === 'event' && markers[message.schemaId]) found.set(markers[message.schemaId].kind, markers[message.schemaId]);
    if (!found.size) for (const event of commit.events ?? []) {
      const label = categoryMetadata(event.category, locale).label;
      const detail = locale === 'zh-CN' ? event.text_zh ?? event.text : event.text;
      const previous = found.get(event.category);
      found.set(event.category, {label, kind:event.category, detail: previous?.detail ? `${previous.detail} · ${detail}` : detail});
    }
    return [...found.values()].map(marker => ({...marker,offset,cut:commit.commitIndex,ns:commit.at.ns}));
  });
}
export function RunTimeline({ store, clock, cut, mode, onSeek, onPlay, onLive, onTick }: {store:TemporalFeedStore;clock:PlaybackClock;cut?:number;mode:string;onSeek:(offset:number)=>void;onPlay:()=>void;onLive:()=>void;onTick:()=>void}) {
  const { t, locale } = useI18n();
  const events = useMemo(() => timelineEvents(store.commits, locale), [store, store.commits.length, locale]);
  let offset = cut === undefined ? store.commits.findIndex(commit => BigInt(commit.at.ns) > BigInt(clock.ns)) - 1 : store.commits.findIndex(commit => commit.commitIndex === cut);
  if (cut === undefined && offset === -2) offset = store.commits.length - 1;
  const end = Math.max(0,store.commits.length-1), empty = store.commits.length === 0;
  const markerLabel = (kind: string) => kind === 'accident' ? t('Accident') : kind === 'award' ? t('Award') : kind === 'capture' ? t('Capture') : events.find(event => event.kind === kind)?.label ?? readableLabel(kind,locale);
  const legend = [...new Map(events.map(event => [event.kind, markerLabel(event.kind)])).entries()];
  const markerAction = (kind: string, at: string) => kind === 'accident' ? t('Seek accident at {at}', {at}) : kind === 'award' ? t('Seek award at {at}', {at}) : kind === 'capture' ? t('Seek capture at {at}', {at}) : locale === 'zh-CN' ? `跳到 ${at} 的${markerLabel(kind)}` : `Seek ${markerLabel(kind)} at ${at}`;
  return <footer className="viewer-timeline inspection-timeline" aria-label={t('Shared simulation timeline')}>
    <Button type="primary" disabled={empty} aria-label={clock.playing ? t('Pause playback') : t('Play')} onClick={onPlay}>{clock.playing ? t('Pause') : t('Play')}</Button>
    {mode === 'live' && <Button disabled={empty} aria-pressed={store.liveFollow} onClick={onLive}>{t('Follow live')}</Button>}
    <Select aria-label={t('Playback speed')} value={clock.speed} options={[0.25,0.5,1,2,4,8].map(value=>({value,label:`${value}×`}))} onChange={value=>{clock.setSpeed(value);onTick();}} />
    <div className="inspection-timeline-track"><Slider ariaLabelForHandle={t('Commit timeline')} disabled={empty} min={0} max={end} value={Math.max(0,offset)} onChange={onSeek} tooltip={{formatter:index=>index === undefined ? '' : displayTime(store.commits[index]?.at.ns ?? clock.ns,store.header.start.ns)}} />
      <div className="inspection-event-markers">{events.map(event=><button key={`${event.cut}/${event.kind}`} className={`inspection-marker marker-${event.kind}`} style={{left:`${end ? event.offset/end*100 : 0}%`}} aria-label={markerAction(event.kind,displayTime(event.ns,store.header.start.ns))} title={`${event.detail ?? markerLabel(event.kind)} · ${displayTime(event.ns,store.header.start.ns)}`} onClick={()=>onSeek(event.offset)}><span>{markerLabel(event.kind)}</span></button>)}</div>
    </div>
    <ConceptHelp topic={t('Timeline')} description={t('Graph, city and entity state share this cursor; event markers seek their recorded moment.')} guide="console.md"/><time>{displayTime(clock.ns,store.header.start.ns)}<small> / {displayTime(clock.end,store.header.start.ns)}</small></time>
    {legend.length > 0 && <div className="inspection-marker-legend">{legend.map(([kind,label]) => <span className={`marker-${kind}`} key={kind}>{label}</span>)}</div>}
  </footer>;
}
