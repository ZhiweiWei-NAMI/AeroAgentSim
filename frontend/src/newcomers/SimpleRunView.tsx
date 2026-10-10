import {useEffect,useMemo,useRef,useState} from 'react';
import type {EntityKey} from '../contracts/viewer-feed';
import type {TemporalFeedStore} from '../feeds/temporal-store';
import type {PlaybackClock} from '../viewport/clock';
import {ViewerPresentation} from '../viewport/ViewerPresentation';
import {EventFeed} from './EventFeed';
import {KpiPanel} from './KpiPanel';
import {businessProjection,resultsAt,type RecordedBusinessEvent} from './projection';
import {TemplateApi,type BusinessEvent,type RunKpis} from './api';
import {CATEGORY_STYLE} from './categories';
import {displayTime} from '../pages/display-time';
import {PageState} from '../console/PageState';
import {Select,Slider} from 'antd';
import {entityLabel} from '../pages/inspection-format';
import './simple.css';
interface Props {
 api:TemplateApi;runId:string;store:TemporalFeedStore;clock:PlaybackClock;status:string;mode:string;selected?:EntityKey;cut?:number;
 camera:'orbit'|'follow'|'chase';focus?:{position:[number,number,number];revision:number};
 onSelect:(key:EntityKey)=>void;onEvent:(event:RecordedBusinessEvent)=>void;onTick:()=>void;onError:(error:unknown)=>void;
 onSeek:(offset:number)=>void;onPlay:()=>void;onLive:()=>void;onControl:(action:'pause'|'resume'|'stop')=>Promise<void>;onInject:()=>void;hasInjection:boolean;followingLive:boolean;untilNs?:string;onExpert:()=>void;
}
export function SimpleRunView(p:Props){
 const [tab,setTab]=useState<'events'|'results'>('events'),[result,setResult]=useState<RunKpis>(),[loading,setLoading]=useState(true),[error,setError]=useState(''),[revision,reload]=useState(0);
 const status=useRef(p.status);status.current=p.status;
 const loadKey=`${p.runId}/${revision}`;
 useEffect(()=>{
  const abort=new AbortController();let inFlight=false,unavailable=false;
  setResult(undefined);setError('');setLoading(true);
  const load=async()=>{if(inFlight)return;inFlight=true;try{const data=await p.api.kpis(p.runId,abort.signal);if(!abort.signal.aborted){setResult(data);setError('');}}catch(problem){if(!abort.signal.aborted){setError(String(problem));unavailable=String(problem).includes('HTTP 404');}}finally{inFlight=false;if(!abort.signal.aborted)setLoading(false);}};
  void load();const timer=setInterval(()=>{if(!unavailable&&!['completed','stopped','faulted','interrupted','input_timeout'].includes(status.current))void load();},3000);
  return()=>{abort.abort();clearInterval(timer);};
 },[p.api,loadKey]);
 const knownAt=p.store.viewCursor?.knownAt??-1;
 const projected=useMemo(()=>businessProjection(p.store.commits,knownAt,p.clock.ns),[p.store,p.store.commits.length,knownAt,p.clock.ns]);
 const atEnd=knownAt===p.store.commits.at(-1)?.commitIndex&&!p.store.liveFollow&&BigInt(p.clock.ns)>=BigInt(p.clock.end)&&['completed','stopped'].includes(p.status);
 const fromApi=resultsAt(result,p.clock.ns,atEnd,knownAt);
 const hasKpiFeed=p.store.commits.some(commit=>commit.kpis!==undefined);
 const values=hasKpiFeed?projected.values:fromApi.values;
 const series=hasKpiFeed?projected.series:fromApi.series;
 const markers=useMemo(()=>p.store.commits.flatMap((commit,offset)=>(commit.events??[]).map((event,index)=>({...event,cut:commit.commitIndex,offset,key:`${commit.commitIndex}/${index}`}))),[p.store,p.store.commits.length]);
 const end=Math.max(0,p.store.commits.length-1);
 const cursor=p.cut===undefined?p.store.commits.reduce((last,commit,index)=>BigInt(commit.at.ns)<=BigInt(p.clock.ns)?index:last,-1):p.store.commits.findIndex(commit=>commit.commitIndex===p.cut);
 const selectEvent=(event:BusinessEvent)=>{const row=projected.events.find(row=>row===event);if(!row)throw Error('This event is not in the displayed timeline.');p.onEvent(row);};
 const active=p.mode==='live'&&!['completed','stopped','faulted','interrupted','input_timeout'].includes(p.status);
 const live=active&&p.followingLive;
 const selected=p.selected&&[...p.store.entities.values()].find(row=>row.key.id===p.selected?.id&&row.key.generation===p.selected?.generation);
 return <section className="simple-console" data-testid="simple-run-view" data-cut={knownAt}>
  <div className="simple-run-grid" data-testid="dual-run-views" data-cut={knownAt} data-run={p.store.header.runId}>
   <section className="simple-city" data-testid="synchronized-city" data-cut={knownAt} aria-label="City view">
    <div className="simple-city-title"><span>Watch the scenario</span>{selected&&<span className="simple-selected">Selected: {entityLabel(selected)}</span>}<button className="console-btn" onClick={p.onLive} disabled={!active}>Follow live</button></div>
    <ViewerPresentation store={p.store} clock={p.clock} selected={p.selected} mode={p.camera} quality="med" trails commitCut={p.cut} simple focus={p.focus} onSelect={p.onSelect} onTick={p.onTick} onError={p.onError} onQuality={()=>{}}/>
   </section>
   <aside className="simple-activity" aria-label="Scenario activity"><div className="simple-panel-tabs" role="tablist" aria-label="Activity and results"><button role="tab" aria-selected={tab==='events'} onClick={()=>setTab('events')}>Events<span>{projected.events.length}</span></button><button role="tab" aria-selected={tab==='results'} onClick={()=>setTab('results')}>Results</button></div>
    <div role="tabpanel" aria-label={tab==='events'?'Events':'Results'}>{tab==='events'?(p.store.commits.length?<EventFeed available={p.store.commits.some(commit=>commit.events!==undefined)} onExplore={p.onExpert} events={projected.events} selectedEntities={p.selected?[p.selected.id]:[]} onSelect={selectEvent}/>:<PageState kind="loading" title="Connecting to the simulation" description="The city is ready. Waiting for the first recorded activity."/>):<KpiPanel values={values} series={series} loading={loading&&!Object.keys(values).length} error={error&&!Object.keys(values).length?error:undefined} onRetry={()=>reload(n=>n+1)}/>}</div>
   </aside>
  </div>
  <footer className="simple-timeline" aria-label="Scenario timeline">
   <div className="simple-playback"><button className="console-btn console-btn-primary" disabled={!p.store.commits.length} aria-label={live?(p.status==='paused'?'Resume simulation':'Pause simulation'):(p.clock.playing?'Pause playback':'Play')} onClick={()=>{if(live)void p.onControl(p.status==='paused'?'resume':'pause');else p.onPlay();}}>{live?(p.status==='paused'?'Resume':'Pause'):(p.clock.playing?'Pause':'Play')}</button><Select aria-label="Playback speed" disabled={live} title="Replay speed is available when you explore recorded moments" value={p.clock.speed} options={[0.5,1,2,4].map(value=>({value,label:`${value}×`}))} onChange={value=>{p.clock.setSpeed(value);p.onTick();}}/>{p.hasInjection&&active&&<button className="console-btn" data-testid="simple-inject" onClick={p.onInject}>Introduce an event</button>}</div>
   <div className="simple-slider"><Slider aria-label="Scenario timeline" min={0} max={end} value={Math.max(0,cursor)} onChange={p.onSeek} tooltip={{formatter:value=>value===undefined?'':displayTime(p.store.commits[value]?.at.ns??p.clock.ns,p.store.header.start.ns)}}/>
    <div className="simple-event-markers">{markers.slice(-250).map(event=><button key={event.key} style={{left:`${end?event.offset/end*100:0}%`,background:CATEGORY_STYLE[event.category].color}} aria-label={`Seek event: ${event.text}`} title={`${displayTime(event.at_ns,p.store.header.start.ns)} · ${event.text}`} onClick={()=>p.onEvent(event)}/>)}</div>
   </div><time>{displayTime(p.clock.ns,p.store.header.start.ns)} / {p.mode==='live'?(p.untilNs?displayTime(p.untilNs,p.store.header.start.ns):'Live'):displayTime(p.clock.end,p.store.header.start.ns)}</time>
  </footer>
 </section>;
}
