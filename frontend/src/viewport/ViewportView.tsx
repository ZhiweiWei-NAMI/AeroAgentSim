import { useEffect, useMemo, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import type { PlaybackClock } from './clock';
import { Viewport, type CameraMode } from './viewport';
import { LiveFollowClock } from './live-interpolation';
import type { Quality } from './pipeline';
import type { CityProgress } from '../scene/traffic-city';
import { MissionTimeline } from './MissionTimeline';
import { initialActionFraming } from './action-framing';
import { entityId } from './bindings';
import { useI18n } from '../i18n/I18nProvider';
import '../scene/presentation';
import '../workbench/run-status.css';
import {BusinessDirector,directorTarget,type DirectorTarget} from './business-director';
import type {RecordedBusinessEvent} from '../newcomers/projection';
interface Props {
  store: FeedStore & { liveFollow?: boolean }; clock: PlaybackClock; selected?: EntityKey; mode: CameraMode; quality: Quality; trails: boolean;
  commitCut?: number;
  /** Entities emphasised together with the selection (cause trace). */
  highlights?: EntityKey[];
  simple?: boolean; focus?: {position:[number,number,number];revision:number};
  cameraRevision?:number;
  director?:{enabled:boolean;active:boolean;paused:boolean;events:RecordedBusinessEvent[]};
  onDirectorPause?:()=>void;
  onDirectorSubject?:(key:EntityKey)=>void;
  onSelect: (key: EntityKey) => void; onTick: () => void; onError: (error: unknown) => void;
  /** Keep the owner's exact-cut controls in sync with a recorded event marker. */
  onSeekCommit?: (ns: string, commitIndex: number) => void;
  onCameraMode?: (mode: CameraMode) => void;
  onQuality: (quality: Quality, fps?: number) => void;
  /** The owner's handle for display-only camera controls. */
  onViewport?: (viewport: Viewport | undefined) => void;
  /** Hide built-in toolbars and overlays; the owner provides its own panels. */
  bare?: boolean;
  /** Owner action for "Change source…" on a persistent city-load failure (e.g. open Configure > Space). */
  onChangeSource?: () => void;
}
interface LegendEntry { label: string; color: string }
export function ViewportView(props: Props) {
  const { locale, t } = useI18n();
  const root=useRef<HTMLDivElement>(null),viewport=useRef<Viewport>();
  const current=useRef(props);current.current=props;
  const [mode,setMode]=useState<CameraMode|undefined>(()=>{
    const value=new URLSearchParams(location.search).get('camera');return ['orbit','follow','chase','cinematic'].includes(value ?? '')?value as CameraMode:undefined;
  });
  const [quality,setQuality]=useState<Quality>(props.quality),[fps,setFps]=useState<number>(),[status,setStatus]=useState(''),[collapsed,setCollapsed]=useState(false);
  const [cityProgress,setCityProgress]=useState<CityProgress>();
  // A failed city must stay visible: the asset issues live in the viewport's
  // own `data-asset-status`, so mirror them into React state for an alert.
  const [cityIssue,setCityIssue]=useState<string>();
  const [hintDismissed,setHintDismissed]=useState(false);
  const sceneHint=(props.store.header.scene as {hint?:string}|undefined)?.hint;
  const [dusk,setDusk]=useState(new URLSearchParams(location.search).get('lighting')==='dusk');
  const activeMode=useRef(mode ?? props.mode);activeMode.current=mode ?? props.mode;
  const previousMode=useRef(props.mode);
  const eventCut=useRef<{ns:string;index:number;parentCut?:number}>();
  // Simple-mode semantic overlay state; Expert keeps the plain defaults.
  const [showAllLabels,setShowAllLabels]=useState(false);
  const [legend,setLegend]=useState<LegendEntry[]>([]);
  const [zoneCount,setZoneCount]=useState(0);
  const floodScene=[...props.store.entities.values()].some(entity=>entity.fields.has('rescue.flood_bounds'));
  // The lite demo serves a scenario-snapshot catalog; surface the header's own
  // notice when present. No catalog inference is performed here.
  const catalogNotice=(props.store.header as typeof props.store.header & {catalogNotice?:string}).catalogNotice;
  useEffect(()=>{
    let frame=0,last=performance.now(),notify=last,lastSeek='',lastNotified='';
    let actionFramed=false,interacted=false;
    const director=new BusinessDirector();let shot:DirectorTarget|undefined,autoActive=false,wasPaused=false;
    const preserveCamera=()=>{interacted=true;};
    const container=root.current;
    container?.addEventListener('pointerdown',preserveCamera);
    const liveClock=new LiveFollowClock();
    try{
      viewport.current=new Viewport(root.current!,props.store.header,{
        quality:props.quality,locale,onSelect:key=>current.current.onSelect(key),onError:error=>current.current.onError(error),onStatus:setStatus,onCityProgress:setCityProgress,
        onCityIssue:setCityIssue,
        onQuality:(quality,fps)=>{setQuality(quality);setFps(fps);current.current.onQuality(quality,fps);},
        onCameraInteraction:()=>{
          interacted=true;director.pauseOnInteraction();autoActive=false;
          setMode('orbit');activeMode.current='orbit';current.current.onDirectorPause?.();
          current.current.onCameraMode?.('orbit');
        },
        semantic:props.simple===true,showAllLabels:showAllLabels,
        onLegend:(entries,zones)=>{setLegend(entries);setZoneCount(zones);},
      });
      props.onViewport?.(viewport.current);
      viewport.current.setDusk(dusk);viewport.current.setCameraMode(mode ?? props.mode);viewport.current.setSelection(props.selected);viewport.current.setHighlights(props.highlights??[]);
      const animate=(now:number)=>{
        try{
          const p=current.current,delta=(now-last)/1000;last=now;
          p.clock.update(now/1000);
          const saved=eventCut.current;
          if(saved&&(p.clock.playing||p.clock.ns!==saved.ns||p.commitCut!==saved.parentCut&&p.commitCut!==saved.index))eventCut.current=undefined;
          const cut=p.clock.playing?undefined:eventCut.current?.index??p.commitCut;
          const seekKey=`${p.clock.ns}/${cut}/${p.store.commits.length}`;
          if(seekKey!==lastSeek){p.store.seek(p.clock.ns,cut);lastSeek=seekKey;}
          if(p.simple&&activeMode.current==='orbit'&&!actionFramed&&!interacted&&!p.focus){
            const framing=initialActionFraming(p.store.header,p.store.entities.values());
            if(framing){
              const view=viewport.current!;
              view.focusPosition(framing.center,framing.radius,true);
              actionFramed=true;if(root.current)root.current.dataset.framing='main-actors';
            }
          }
          // Display-only live-follow: render one pose sample behind the head on
          // the wall clock; the exact store cursor above stays at the live head.
          const liveActive=p.store.liveFollow===true&&cut===undefined;
          liveClock.observe(p.store,liveActive);
          const liveNs=liveActive?liveClock.renderNs(p.clock.ns):undefined;
          if(root.current) {root.current.dataset.displayNs=liveNs??p.clock.ns;root.current.dataset.committedNs=p.clock.ns;}
          const config=p.director;
          if(config){
            if(config.paused)director.pauseOnInteraction();
            else if(wasPaused)director.resume();
            wasPaused=config.paused;
            const enabled=config.enabled&&config.active&&!director.interactionPaused;
            const known=(p.store as FeedStore & {viewCursor?:{knownAt:number}}).viewCursor?.knownAt??p.store.commits.at(-1)?.commitIndex??-1;
            const cue=director.tick(config.events,now,enabled,p.clock.ns,known);
            if(!director.current)shot=undefined;
            if(cue){shot=directorTarget(p.store,cue);if(shot?.entity)p.onDirectorSubject?.(shot.entity);}
            viewport.current!.setDirectorTarget(enabled?shot:undefined);
            if(enabled!==autoActive){viewport.current!.setCameraMode(enabled?'cinematic':activeMode.current);autoActive=enabled;}
          }
          viewport.current!.render(p.store,liveNs??p.clock.ns,delta,p.trails);
          if(now-notify>100&&seekKey!==lastNotified){p.onTick();notify=now;lastNotified=seekKey;}frame=requestAnimationFrame(animate);
        }catch(error){current.current.onError(error);}
      };frame=requestAnimationFrame(animate);
    }catch(error){props.onError(error);}
    return()=>{container?.removeEventListener('pointerdown',preserveCamera);cancelAnimationFrame(frame);current.current.onViewport?.(undefined);viewport.current?.dispose();viewport.current=undefined;eventCut.current=undefined;container?.closest('.viewer-main')?.removeAttribute('data-inspector-collapsed');};
  },[props.store,props.clock]);
  useEffect(()=>{viewport.current?.setSelection(props.selected);},[props.selected]);
  const highlightKey=(props.highlights??[]).map(key=>`${key.id}:${key.generation}`).join('|');
  useEffect(()=>{viewport.current?.setHighlights(props.highlights??[]);},[highlightKey]);
  useEffect(()=>{viewport.current?.setLocale(locale);},[locale]);
  useEffect(()=>{viewport.current?.setSemantic({semantic:props.simple===true,showAllLabels});},[props.simple,showAllLabels]);
  useEffect(()=>{
    if(previousMode.current!==props.mode){previousMode.current=props.mode;setMode(undefined);activeMode.current=props.mode;viewport.current?.setCameraMode(props.mode);}
    else viewport.current?.setCameraMode(mode ?? props.mode);
  },[props.mode,mode]);
  useEffect(()=>{if(props.cameraRevision!==undefined){setMode(undefined);activeMode.current=props.mode;viewport.current?.setCameraMode(props.mode);}},[props.cameraRevision]);
  useEffect(()=>{setQuality(props.quality);viewport.current?.setQuality(props.quality);},[props.quality]);
  useEffect(()=>{if(props.focus){setMode('orbit');activeMode.current='orbit';viewport.current?.focusPosition(props.focus.position);}},[props.focus]);
  const events=useMemo(()=>props.store.commits.flatMap(commit=>commit.messages.filter(message=>message.kind==='event'&&(!props.selected||message.subjects?.some(key=>entityId(key)===entityId(props.selected!)))).map(message=>({...message,commitIndex:commit.commitIndex}))),[props.store,props.store.commits.length,props.selected]);
  const duration=BigInt(props.clock.end)-BigInt(props.store.header.start.ns);
  return <>
    <div className="viewport-canvas" data-testid="viewport" ref={root} aria-label={t('Simulation viewport')} />
    {cityProgress&&<div className="viewport-city-progress" role="status" aria-live="polite">
      <span>{locale.startsWith('zh')?'黄浦实景资源':'Huangpu city assets'}</span>
      <strong>{cityProgress.phase==='roads'?(locale.startsWith('zh')?'正在构建道路':'Preparing roads'):`${cityProgress.loaded} / ${cityProgress.total} ${locale.startsWith('zh')?'栋建筑':'buildings'}`}</strong>
      <progress aria-label={locale.startsWith('zh')?'城市资源加载进度':'City asset loading progress'} max={cityProgress.total||1} value={cityProgress.total?cityProgress.loaded:undefined}/>
      <small>{locale.startsWith('zh')?'场景逐步加载，运行控制可用':'Scene loads progressively; run controls are available'}</small>
    </div>}
    {cityIssue&&<div className="viewport-city-alert" role="alert" data-testid="city-load-alert">
      <strong>{locale.startsWith('zh')?'城市加载失败':'City failed to load'}</strong>
      <p>{cityIssue}</p>
      <div className="viewport-city-alert-actions">
        <button type="button" onClick={()=>{setCityIssue(undefined);location.reload();}}>{locale.startsWith('zh')?'重试':'Retry'}</button>
        {props.onChangeSource&&<button type="button" onClick={props.onChangeSource}>{locale.startsWith('zh')?'更改数据源…':'Change source…'}</button>}
      </div>
    </div>}
    {sceneHint&&!hintDismissed&&<div className="viewport-scene-hint" role="note" data-testid="scene-hint">
      <span>{sceneHint}</span>
      <button type="button" aria-label={locale.startsWith('zh')?'关闭提示':'Dismiss hint'} onClick={()=>setHintDismissed(true)}>✕</button>
    </div>}
    {!props.simple&&!props.bare&&<div className="director-toolbar">
      <select aria-label={t('View camera')} value={mode ?? props.mode} onChange={event=>{const next=event.target.value as CameraMode;setMode(next);props.onCameraMode?.(next);}}>
        <option value="orbit">{t('Orbit')}</option><option value="follow">{t('Follow unit')}</option><option value="chase">{t('Chase')}</option><option value="cinematic">{t('Event director')}</option>
      </select>
      <select aria-label={t('View quality')} value={quality} onChange={event=>{const q=event.target.value as Quality;setQuality(q);viewport.current?.setQuality(q,true);}}>
        <option value="low">{t('Low')}</option><option value="med">{t('Balanced')}</option><option value="high">{t('High')}</option>
      </select>{fps!==undefined&&<span>{t('{fps} FPS', {fps:fps.toFixed(0)})}</span>}
      <select aria-label={t('Display lighting')} value={dusk?'dusk':'day'} onChange={event=>{const value=event.target.value==='dusk';setDusk(value);viewport.current?.setDusk(value);}}><option value="day">{t('Day · display')}</option><option value="dusk">{t('Dusk · display')}</option></select>
      <button aria-label={t('Toggle inspector')} aria-expanded={!collapsed} onClick={()=>{const next=!collapsed;setCollapsed(next);root.current?.closest('.viewer-main')?.setAttribute('data-inspector-collapsed',String(next));}}>{t('Inspector')} {collapsed?'＋':'−'}</button>
    </div>}
    {!props.simple&&!props.bare&&<div className="scene-status"><span>{t('Buildings, lighting and symbols are display effects.')}</span>{status&&<details><summary>{t('Display details')}</summary><div>{status}</div></details>}</div>}
    {props.simple&&(!props.bare||floodScene)&&<div className="semantic-legend" data-testid="semantic-legend" aria-label={t('Map legend')}
      style={props.bare?{left:'calc(var(--wb-left, 0px) + 16px)',bottom:'calc(var(--wb-dock, 0px) + 16px)',maxWidth:360}:undefined}>
      <div className="semantic-legend-title">{floodScene?(locale.startsWith('zh')?'洪涝救援':'Flood rescue'):t('Airspace & operations')}</div>
      <div className="semantic-legend-entries">
        {legend.map(entry=><span key={entry.label} className="semantic-legend-entry"><i style={{background:entry.color}}/>{entry.label}</span>)}
        {!legend.length&&<span className="semantic-legend-entry muted">{t('Legend appears with scenario data')}</span>}
      </div>
      {!props.bare&&<><label className="semantic-showall"><input type="checkbox" checked={showAllLabels} aria-label={t('Show all labels')} onChange={event=>setShowAllLabels(event.target.checked)}/>{t('Show all labels')}</label>
      <span className="semantic-zone-count" data-testid="semantic-zone-count">{zoneCount===1?t('1 active zone'):t('{count} active zones', {count:zoneCount})}</span></>}
    </div>}
    {!props.simple&&catalogNotice&&<div className="scene-attribution" style={{bottom:24}} data-testid="catalog-notice">{catalogNotice}</div>}
    <div className="scene-attribution">{root.current?.dataset.attribution}</div>
    {!props.simple&&!props.bare&&<div className="mission-overlay"><MissionTimeline store={props.store} selected={props.selected} />
      <div className="event-strip" aria-label={t('Recorded event markers')}>{duration>0n&&events.slice(-200).map(event=>
        <button key={event.id} title={t('{schema} · {ns} ns', {schema:event.schemaId,ns:event.at.ns})} style={{left:`${Math.max(0,Math.min(100,Number((BigInt(event.at.ns)-BigInt(props.store.header.start.ns))*10000n/duration)/100))}%`}}
          onClick={()=>{props.clock.pause();props.clock.seek(event.at.ns);eventCut.current={ns:props.clock.ns,index:event.commitIndex,parentCut:props.commitCut};props.store.seek(props.clock.ns,event.commitIndex);props.onSeekCommit?.(props.clock.ns,event.commitIndex);props.onTick();}} aria-label={t('Seek {schema}', {schema:event.schemaId})} />)}</div>
    </div>}
  </>;
}
