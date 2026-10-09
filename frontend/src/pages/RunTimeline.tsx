import { useMemo } from 'react';
import { Button, Select, Slider } from 'antd';
import type { FeedCommit } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import type { PlaybackClock } from '../viewport/clock';
import { displayTime } from './display-time';
import { ConceptHelp } from '../console/ConceptHelp';

const markers: Record<string, {label: string; kind: string}> = {
  'traffic.inject.accident': {label:'Accident',kind:'accident'},
  'traffic.accident': {label:'Accident',kind:'accident'},
  'traffic.award.committed': {label:'Award',kind:'award'},
  'traffic.capture.stored': {label:'Capture',kind:'capture'},
};
export function timelineEvents(commits: FeedCommit[]) {
  return commits.flatMap((commit, offset) => {
    const found = new Map<string, {label:string;kind:string}>();
    for (const message of commit.messages) if (message.kind === 'event' && markers[message.schemaId]) found.set(message.schemaId, markers[message.schemaId]);
    return [...found.values()].map(marker => ({...marker,offset,cut:commit.commitIndex,ns:commit.at.ns}));
  });
}
export function RunTimeline({ store, clock, cut, mode, onSeek, onPlay, onLive, onTick }: {store:TemporalFeedStore;clock:PlaybackClock;cut?:number;mode:string;onSeek:(offset:number)=>void;onPlay:()=>void;onLive:()=>void;onTick:()=>void}) {
  const events = useMemo(() => timelineEvents(store.commits), [store, store.commits.length]);
  let offset = cut === undefined ? store.commits.findIndex(commit => BigInt(commit.at.ns) > BigInt(clock.ns)) - 1 : store.commits.findIndex(commit => commit.commitIndex === cut);
  if (cut === undefined && offset === -2) offset = store.commits.length - 1;
  const end = Math.max(0,store.commits.length-1);
  return <footer className="viewer-timeline inspection-timeline" aria-label="Shared simulation timeline">
    <Button type="primary" aria-label={clock.playing ? 'Pause playback' : 'Play'} onClick={onPlay}>{clock.playing ? 'Pause' : 'Play'}</Button>
    {mode === 'live' && <Button onClick={onLive}>Follow live</Button>}
    <Select aria-label="Playback speed" value={clock.speed} options={[0.25,0.5,1,2,4,8].map(value=>({value,label:`${value}×`}))} onChange={value=>{clock.setSpeed(value);onTick();}} />
    <div className="inspection-timeline-track"><Slider aria-label="Commit timeline" min={0} max={end} value={Math.max(0,offset)} onChange={onSeek} tooltip={{formatter:index=>index === undefined ? '' : displayTime(store.commits[index]?.at.ns ?? clock.ns,store.header.start.ns)}} />
      <div className="inspection-event-markers">{events.map(event=><button key={`${event.cut}/${event.kind}`} className={`inspection-marker marker-${event.kind}`} style={{left:`${end ? event.offset/end*100 : 0}%`}} aria-label={`Seek ${event.label.toLowerCase()} at ${displayTime(event.ns,store.header.start.ns)}`} title={`${event.label} · ${displayTime(event.ns,store.header.start.ns)}`} onClick={()=>onSeek(event.offset)}><span>{event.label}</span></button>)}</div>
    </div>
    <ConceptHelp topic="Timeline" description="Graph, city and entity state share this cursor; event markers seek their recorded moment." guide="console.md"/><time>{displayTime(clock.ns,store.header.start.ns)}<small> / {displayTime(clock.end,store.header.start.ns)}</small></time>
    <div className="inspection-marker-legend"><span className="marker-accident">Accident</span><span className="marker-award">Award</span><span className="marker-capture">Capture</span></div>
  </footer>;
}
