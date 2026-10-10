import { useEffect, useMemo, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import type { PlaybackClock } from './clock';
import { Viewport, type CameraMode } from './viewport';
import { LiveFollowClock } from './live-interpolation';
import type { Quality } from './pipeline';
import { MissionTimeline } from './MissionTimeline';
import { initialActionFraming } from './action-framing';
import '../scene/presentation';
interface Props {
  store: FeedStore & { liveFollow?: boolean }; clock: PlaybackClock; selected?: EntityKey; mode: CameraMode; quality: Quality; trails: boolean;
  commitCut?: number;
  /** Entities emphasised together with the selection (cause trace). */
  highlights?: EntityKey[];
  simple?: boolean; focus?: {position:[number,number,number];revision:number};
  onSelect: (key: EntityKey) => void; onTick: () => void; onError: (error: unknown) => void;
  onQuality: (quality: Quality, fps?: number) => void;
}
interface LegendEntry { label: string; color: string }
export function ViewportView(props: Props) {
  const root=useRef<HTMLDivElement>(null),viewport=useRef<Viewport>();
  const current=useRef(props);current.current=props;
  const [mode,setMode]=useState<CameraMode|undefined>(()=>{
    const value=new URLSearchParams(location.search).get('camera');return ['orbit','follow','chase','cinematic'].includes(value ?? '')?value as CameraMode:undefined;
  });
  const [quality,setQuality]=useState<Quality>(props.quality),[fps,setFps]=useState<number>(),[status,setStatus]=useState(''),[collapsed,setCollapsed]=useState(false);
  const [dusk,setDusk]=useState(new URLSearchParams(location.search).get('lighting')==='dusk');
  // Simple-mode semantic overlay state; Expert keeps the plain defaults.
  const [showAllLabels,setShowAllLabels]=useState(false);
  const [legend,setLegend]=useState<LegendEntry[]>([]);
  const [zoneCount,setZoneCount]=useState(0);
  // The lite demo serves a scenario-snapshot catalog; surface the header's own
  // notice when present. No catalog inference is performed here.
  const catalogNotice=(props.store.header as typeof props.store.header & {catalogNotice?:string}).catalogNotice;
  useEffect(()=>{
    let frame=0,last=performance.now(),notify=last,lastSeek='',lastNotified='';
    let actionFramed=false,interacted=false;
    const preserveCamera=()=>{interacted=true;};
    root.current?.addEventListener('pointerdown',preserveCamera);
    const liveClock=new LiveFollowClock();
    try{
      viewport.current=new Viewport(root.current!,props.store.header,{
        quality:props.quality,onSelect:key=>current.current.onSelect(key),onError:error=>current.current.onError(error),onStatus:setStatus,
        onQuality:(quality,fps)=>{setQuality(quality);setFps(fps);current.current.onQuality(quality,fps);},
        semantic:props.simple===true,showAllLabels:showAllLabels,
        onLegend:(entries,zones)=>{setLegend(entries);setZoneCount(zones);},
      });
      viewport.current.setDusk(dusk);viewport.current.setCameraMode(mode ?? props.mode);viewport.current.setSelection(props.selected);viewport.current.setHighlights(props.highlights??[]);
      const animate=(now:number)=>{
        try{
          const p=current.current,delta=(now-last)/1000;last=now;
          p.clock.update(now/1000);const cut=p.clock.playing?undefined:p.commitCut;
          const seekKey=`${p.clock.ns}/${cut}/${p.store.commits.length}`;
          if(seekKey!==lastSeek){p.store.seek(p.clock.ns,cut);lastSeek=seekKey;}
          if(p.simple&&(mode??p.mode)==='orbit'&&!actionFramed&&!interacted&&!p.focus){
            const framing=initialActionFraming(p.store.header,p.store.entities.values());
            if(framing){
              const view=viewport.current!;
              view.focusPosition(framing.center);
              // Fit the actor bounding sphere using the smaller camera field of view.
              const halfVertical=view.camera.fov*Math.PI/360;
              const halfHorizontal=Math.atan(Math.tan(halfVertical)*view.camera.aspect);
              const distance=framing.radius/Math.sin(Math.min(halfVertical,halfHorizontal))*1.12;
              const scale=distance/Math.hypot(65,50,65);
              view.camera.position.x+=65*(scale-1);view.camera.position.y+=50*(scale-1);view.camera.position.z+=65*(scale-1);
              actionFramed=true;if(root.current)root.current.dataset.framing='main-actors';
            }
          }
          // Display-only live-follow: render one pose sample behind the head on
          // the wall clock; the exact store cursor above stays at the live head.
          const liveActive=p.store.liveFollow===true&&cut===undefined;
          liveClock.observe(p.store,liveActive);
          const liveNs=liveActive?liveClock.renderNs(p.clock.ns):undefined;
          if(root.current) {root.current.dataset.displayNs=liveNs??p.clock.ns;root.current.dataset.committedNs=p.clock.ns;}
          viewport.current!.render(p.store,liveNs??p.clock.ns,delta,p.trails);
          if(now-notify>100&&seekKey!==lastNotified){p.onTick();notify=now;lastNotified=seekKey;}frame=requestAnimationFrame(animate);
        }catch(error){current.current.onError(error);}
      };frame=requestAnimationFrame(animate);
    }catch(error){props.onError(error);}
    return()=>{root.current?.removeEventListener('pointerdown',preserveCamera);cancelAnimationFrame(frame);viewport.current?.dispose();viewport.current=undefined;root.current?.closest('.viewer-main')?.removeAttribute('data-inspector-collapsed');};
  },[props.store,props.clock]);
  useEffect(()=>{viewport.current?.setSelection(props.selected);},[props.selected]);
  const highlightKey=(props.highlights??[]).map(key=>`${key.id}:${key.generation}`).join('|');
  useEffect(()=>{viewport.current?.setHighlights(props.highlights??[]);},[highlightKey]);
  useEffect(()=>{viewport.current?.setSemantic({showAllLabels});},[showAllLabels]);
  useEffect(()=>{viewport.current?.setCameraMode(mode ?? props.mode);},[props.mode,mode]);
  useEffect(()=>{viewport.current?.setQuality(props.quality);},[props.quality]);
  useEffect(()=>{if(props.focus){setMode(undefined);viewport.current?.focusPosition(props.focus.position);}},[props.focus]);
  const events=useMemo(()=>props.store.commits.flatMap(commit=>commit.messages.filter(message=>message.kind==='event'&&(!props.selected||message.subjects?.some(key=>key.id===props.selected!.id&&key.generation===props.selected!.generation)))),[props.store,props.store.commits.length,props.selected]);
  const duration=BigInt(props.clock.end)-BigInt(props.store.header.start.ns);
  return <>
    <div className="viewport-canvas" data-testid="viewport" ref={root} aria-label="Simulation viewport" />
    {!props.simple&&<div className="director-toolbar">
      <select aria-label="View camera" value={mode ?? props.mode} onChange={event=>setMode(event.target.value as CameraMode)}>
        <option value="orbit">Orbit</option><option value="follow">Follow unit</option><option value="chase">Chase</option><option value="cinematic">Event director</option>
      </select>
      <select aria-label="View quality" value={quality} onChange={event=>{const q=event.target.value as Quality;setQuality(q);viewport.current?.setQuality(q,true);}}>
        <option value="low">Low</option><option value="med">Balanced</option><option value="high">High</option>
      </select><span>{fps===undefined?'WebGL2':`${fps.toFixed(0)} FPS`}</span>
      <select aria-label="Display lighting" value={dusk?'dusk':'day'} onChange={event=>{const value=event.target.value==='dusk';setDusk(value);viewport.current?.setDusk(value);}}><option value="day">Day · display</option><option value="dusk">Dusk · display</option></select>
      <button aria-label="Toggle inspector" aria-expanded={!collapsed} onClick={()=>{const next=!collapsed;setCollapsed(next);root.current?.closest('.viewer-main')?.setAttribute('data-inspector-collapsed',String(next));}}>Inspector {collapsed?'＋':'−'}</button>
    </div>}
    {!props.simple&&<div className="scene-status" title={status}>{status} · Facades, landscaping, light, haze & screen-sized glyphs are display effects</div>}
    {props.simple&&<div className="semantic-legend" data-testid="semantic-legend" aria-label="Map legend">
      <div className="semantic-legend-title">Airspace &amp; operations</div>
      <div className="semantic-legend-entries">
        {legend.map(entry=><span key={entry.label} className="semantic-legend-entry"><i style={{background:entry.color}}/>{entry.label}</span>)}
        {!legend.length&&<span className="semantic-legend-entry muted">Legend appears with scenario data</span>}
      </div>
      <label className="semantic-showall"><input type="checkbox" checked={showAllLabels} aria-label="Show all labels" onChange={event=>setShowAllLabels(event.target.checked)}/>Show all labels</label>
      <span className="semantic-zone-count" data-testid="semantic-zone-count">{zoneCount===1?'1 active zone':`${zoneCount} active zones`}</span>
    </div>}
    {!props.simple&&catalogNotice&&<div className="scene-attribution" style={{bottom:24}} data-testid="catalog-notice">{catalogNotice}</div>}
    <div className="scene-attribution">{root.current?.dataset.attribution}</div>
    {!props.simple&&<div className="mission-overlay"><MissionTimeline store={props.store} selected={props.selected} />
      <div className="event-strip" aria-label="Recorded event markers">{duration>0n&&events.slice(-200).map(event=>
        <button key={event.id} title={`${event.schemaId} · ${event.at.ns} ns`} style={{left:`${Number((BigInt(event.at.ns)-BigInt(props.store.header.start.ns))*10000n/duration)/100}%`}}
          onClick={()=>{props.clock.pause();props.clock.seek(event.at.ns);props.onTick();}} aria-label={`Seek ${event.schemaId}`} />)}</div>
    </div>}
  </>;
}
