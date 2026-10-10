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
import {eventNarrative} from './presentation';
import {displayTime} from '../pages/display-time';
import {PageState} from '../console/PageState';
import {Details} from '../console/Details';
import {useI18n} from '../i18n/I18nProvider';
import {Select,Slider} from 'antd';
import {displayName} from '../viewport/semantic';
import {SimpleShortcuts} from '../console/shortcuts';
import {entityStory} from './entity-story';
import {SimpleEntityCard} from './SimpleEntityCard';
import {openCauseTrace} from '../trace/bus';
import './simple.css';
import './watch.css';
interface Props {
 api:TemplateApi;runId:string;store:TemporalFeedStore;clock:PlaybackClock;status:string;mode:string;selected?:EntityKey;cut?:number;interactive?:boolean;
 camera:'orbit'|'follow'|'chase';focus?:{position:[number,number,number];revision:number};
 onSelect:(key:EntityKey)=>void;onEvent:(event:RecordedBusinessEvent)=>void;onTick:()=>void;onError:(error:unknown)=>void;
 onSeek:(offset:number)=>void;onPlay:()=>void;onLive:()=>void;onControl:(action:'pause'|'resume'|'stop')=>Promise<void>;onInject:()=>void;hasInjection:boolean;followingLive:boolean;untilNs?:string;onExpert:()=>void;
}
export function SimpleRunView(p:Props){
 const {t,locale,setLocale}=useI18n();
 const [director,setDirector]=useState(true),[directorPaused,setDirectorPaused]=useState(false),[cinema,setCinema]=useState(false);
 const [camera,setCamera]=useState(p.camera),[cameraRevision,setCameraRevision]=useState(0);
 const interactive=p.interactive!==false,cinemaVisible=cinema&&interactive;
 useEffect(()=>{setCamera(p.camera);},[p.camera,p.focus]);
 // The shell retains a run across Home navigation. A new run starts its own
 // watch controls; hidden retained views must not own global cinema/shortcuts.
 useEffect(()=>{setDirector(true);setDirectorPaused(false);setCinema(false);setCamera(p.camera);setCameraRevision(value=>value+1);},[p.runId]);
 useEffect(()=>{document.body.classList.toggle('aas-cinema',cinemaVisible);return()=>document.body.classList.remove('aas-cinema');},[cinemaVisible]);
 const toggleDirector=()=>{if(directorPaused){setDirector(true);setDirectorPaused(false);}else setDirector(value=>!value);};
 const followSelected=()=>{if(!p.selected)return;setDirector(false);setDirectorPaused(false);setCamera('follow');setCameraRevision(value=>value+1);};
 const [tab,setTab]=useState<'events'|'results'>('events'),[result,setResult]=useState<RunKpis>(),[loading,setLoading]=useState(true),[error,setError]=useState(''),[revision,reload]=useState(0);
 const [kpiIds,setKpiIds]=useState<string[]>(),[templateError,setTemplateError]=useState('');
 const templateId=new URLSearchParams(location.search).get('template');
 useEffect(()=>{setKpiIds(undefined);setTemplateError('');if(!templateId)return;const abort=new AbortController();void p.api.template(templateId,abort.signal).then(template=>{if(!abort.signal.aborted)setKpiIds(template.kpis);}).catch(problem=>{if(!abort.signal.aborted)setTemplateError(String(problem));});return()=>abort.abort();},[p.api,templateId]);
 const status=useRef(p.status);status.current=p.status;
 const hasKpiFeed=p.store.commits.some(commit=>commit.kpis!==undefined);
 const loadKey=`${p.runId}/${revision}`;
 useEffect(()=>{
  if(tab!=='results')return;
  const abort=new AbortController();let inFlight=false,unavailable=false;
  setResult(undefined);setError('');setLoading(true);
  const load=async()=>{if(inFlight)return;inFlight=true;try{const data=await p.api.kpis(p.runId,abort.signal);if(!abort.signal.aborted){setResult(data);setError('');}}catch(problem){if(!abort.signal.aborted){setError(String(problem));unavailable=String(problem).includes('HTTP 404');}}finally{inFlight=false;if(!abort.signal.aborted)setLoading(false);}};
  void load();const timer=setInterval(()=>{if(!hasKpiFeed&&!unavailable&&!['completed','stopped','faulted','interrupted','input_timeout'].includes(status.current))void load();},3000);
  return()=>{abort.abort();clearInterval(timer);};
 },[p.api,loadKey,tab,hasKpiFeed]);
 const knownAt=p.store.viewCursor?.knownAt??-1;
 const projected=useMemo(()=>businessProjection(p.store.commits,knownAt,p.clock.ns),[p.store,p.store.commits.length,knownAt,p.clock.ns]);
 const atEnd=knownAt===p.store.commits.at(-1)?.commitIndex&&!p.store.liveFollow&&BigInt(p.clock.ns)>=BigInt(p.clock.end)&&['completed','stopped'].includes(p.status);
 const fromApi=resultsAt(result,p.clock.ns,atEnd,knownAt);
 const values=hasKpiFeed?projected.values:fromApi.values;
 const series=hasKpiFeed?projected.series:fromApi.series;
 const markers=useMemo(()=>p.store.commits.flatMap((commit,offset)=>(commit.events??[]).map((event,index)=>({...event,cut:commit.commitIndex,offset,key:`${commit.commitIndex}/${index}`}))),[p.store,p.store.commits.length]);
 const end=Math.max(0,p.store.commits.length-1);
 const cursor=p.cut===undefined?p.store.commits.reduce((last,commit,index)=>BigInt(commit.at.ns)<=BigInt(p.clock.ns)?index:last,-1):p.store.commits.findIndex(commit=>commit.commitIndex===p.cut);
 const selectRecordedEvent=(event:RecordedBusinessEvent)=>{setDirectorPaused(true);p.onEvent(event);};
 const selectEvent=(event:BusinessEvent)=>{const row=projected.events.find(row=>row===event);if(!row)throw Error('This event is not in the displayed timeline.');selectRecordedEvent(row);};
 const active=p.mode==='live'&&!['completed','stopped','faulted','interrupted','input_timeout'].includes(p.status);
 const live=active&&p.followingLive;
 const selected=p.selected&&[...p.store.entities.values()].find(row=>row.key.id===p.selected?.id&&String(row.key.generation)===String(p.selected?.generation));
 const story=selected?entityStory(p.store,selected,projected.events,locale):undefined;
 const whySelected=()=>{const event=story?.latestEvent;if(event){setCinema(false);openCauseTrace({kind:'event',cut:event.cut,atNs:event.at_ns,text:event.text,entities:event.entities});}};
 const entityNames=new Map([...p.store.entities.values()].map(entity=>[entity.key.id,displayName(entity,p.store.header,locale)]));
 const togglePlay=()=>{if(!p.store.commits.length)return;if(live)void p.onControl(p.status==='paused'?'resume':'pause').catch(p.onError);else p.onPlay();};
 return <section className={`simple-console${cinemaVisible?' is-cinema':''}`} data-testid="simple-run-view" data-cut={knownAt} data-cinema={cinemaVisible}>
   <SimpleShortcuts enabled={interactive} onPlay={togglePlay} onFollow={followSelected} onDirector={toggleDirector} onCinema={()=>setCinema(value=>!value)} onExitCinema={()=>setCinema(false)} onWhy={whySelected} onLanguage={()=>setLocale(locale==='en-US'?'zh-CN':'en-US')}/>
  <div className="simple-run-grid" data-testid="dual-run-views" data-cut={knownAt} data-run={p.store.header.runId}>
   <section className="simple-city" data-testid="synchronized-city" data-cut={knownAt} aria-label={t('City view')}>
    <div className="simple-city-title"><span>{t('Watch the scenario')}</span><div className="simple-camera-actions">
      <button className="console-btn" data-testid="simple-director" aria-pressed={director} onClick={toggleDirector}>{t('Director')}</button>
      {director&&directorPaused&&<button className="console-btn director-resume" data-testid="director-resume" onClick={()=>setDirectorPaused(false)}>{t('Director paused — resume')}</button>}
      <button className="console-btn" aria-pressed={cinema} onClick={()=>setCinema(true)}>{t('Cinema')}</button>
      <button className="console-btn" onClick={p.onLive} disabled={!active} aria-pressed={live}>{t('Follow live')}</button>
     </div></div>
    <ViewerPresentation store={p.store} clock={p.clock} selected={p.selected} mode={camera} cameraRevision={cameraRevision} quality="med" trails commitCut={p.cut} simple focus={p.focus}
      director={{enabled:director,active:interactive&&((live&&p.store.liveFollow)||p.clock.playing),paused:directorPaused,events:projected.events}} onDirectorPause={()=>setDirectorPaused(true)} onDirectorSubject={p.onSelect}
      onSelect={p.onSelect} onTick={p.onTick} onError={p.onError} onQuality={()=>{}}/>
    {story&&<SimpleEntityCard store={p.store} story={story} entityNames={entityNames} onFollow={followSelected} onEvent={selectRecordedEvent} onWhy={whySelected}/>}
   </section>
   <aside className="simple-activity" aria-label={t('Scenario activity')}><div className="simple-panel-tabs" role="tablist" aria-label={t('Activity and results')}>
    <button role="tab" aria-selected={tab==='events'} aria-controls="simple-events-panel" id="simple-events-tab" aria-label={t('Events ({count})',{count:projected.events.length})} onClick={()=>setTab('events')}>{t('Events')}<span aria-hidden="true">{projected.events.length}</span></button>
    <button role="tab" aria-selected={tab==='results'} aria-controls="simple-results-panel" id="simple-results-tab" onClick={()=>setTab('results')}>{t('Results')}</button>
   </div>
    <div role="tabpanel" id={tab==='events'?'simple-events-panel':'simple-results-panel'} aria-labelledby={tab==='events'?'simple-events-tab':'simple-results-tab'}>{tab==='events'
     ?(p.store.commits.length?<EventFeed entityNames={entityNames} available={p.store.commits.some(commit=>commit.events!==undefined)} onExplore={p.onExpert} events={projected.events} selectedEntities={p.selected?[p.selected.id]:[]} onSelect={selectEvent}/>:<PageState kind="loading" title={t('Connecting to the simulation')} description={t('The city is ready. Waiting for the first recorded activity.')}/>)
     :<>{templateError&&<div className="console-muted"><p>{t('Scenario details are unavailable. All recorded metrics are still shown.')}</p><Details title={t('Template request')}><p>{templateError}</p></Details></div>}<KpiPanel ids={kpiIds} values={values} series={series} loading={loading&&!Object.keys(values).length} error={error&&!Object.keys(values).length?error:undefined} onRetry={()=>reload(n=>n+1)}/></>}
    </div>
   </aside>
  </div>
  <footer className="simple-timeline" aria-label={t('Scenario timeline')}>
   <div className="simple-playback"><button className="console-btn console-btn-primary" disabled={!p.store.commits.length} aria-label={live?(p.status==='paused'?t('Resume simulation'):t('Pause simulation')):(p.clock.playing?t('Pause playback'):t('Play'))} onClick={togglePlay}>{live?(p.status==='paused'?t('Resume'):t('Pause')):(p.clock.playing?t('Pause'):t('Play'))}</button>
    <Select aria-label={t('Playback speed')} disabled={live} title={t('Replay speed is available when you explore recorded moments')} value={p.clock.speed} options={[0.5,1,2,4,8].map(value=>({value,label:t('{speed}×',{speed:value})}))} onChange={value=>{p.clock.setSpeed(value);p.onTick();}}/>
    {p.hasInjection&&active&&<button className="console-btn" data-testid="simple-inject" onClick={p.onInject}>{t('Introduce an event')}</button>}
   </div>
   <div className="simple-slider" data-testid="simple-timeline-slider"><Slider ariaLabelForHandle={t('Scenario timeline')} min={0} max={end} disabled={end===0} value={Math.max(0,cursor)} onChange={p.onSeek} tooltip={{formatter:value=>value===undefined?'':displayTime(p.store.commits[value]?.at.ns??p.clock.ns,p.store.header.start.ns)}}/>
    <div className="simple-event-markers">{markers.slice(-250).map(event=>{const text=eventNarrative(event,locale,entityNames);return <button key={event.key} style={{left:`${end?event.offset/end*100:0}%`,background:CATEGORY_STYLE[event.category].color}} aria-label={t('Seek event: {text}',{text})} title={t('{time} · {text}',{time:displayTime(event.at_ns,p.store.header.start.ns),text})} onClick={()=>selectRecordedEvent(event)}/>;})}</div>
   </div><time>{displayTime(p.clock.ns,p.store.header.start.ns)} / {p.mode==='live'?(p.untilNs?displayTime(p.untilNs,p.store.header.start.ns):t('Live')):displayTime(p.clock.end,p.store.header.start.ns)}</time>
  </footer>
  {cinemaVisible&&<footer className="cinema-ticker" data-testid="cinema-ticker" aria-label={t('Latest scenario events')}>
   <ol>{projected.events.slice(-3).map(event=><li key={event.key} title={eventNarrative(event,locale,entityNames)}>{eventNarrative(event,locale,entityNames)}</li>)}</ol>
   <time aria-label={t('Simulation clock')}>{displayTime(p.clock.ns,p.store.header.start.ns)}</time>
   {director&&directorPaused&&<button onClick={()=>setDirectorPaused(false)}>{t('Director paused — resume')}</button>}
   <button aria-label={t('Exit cinema mode')} onClick={()=>setCinema(false)}>{t('Exit cinema · Esc')}</button>
  </footer>}
 </section>;
}
