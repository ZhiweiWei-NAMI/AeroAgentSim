import { useEffect, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import type { PlaybackClock } from './clock';
import { Viewport, type CameraMode } from './viewport';
import type { Quality } from './pipeline';
import { MissionTimeline } from './MissionTimeline';
import '../scene/presentation';
interface Props {
  store: FeedStore; clock: PlaybackClock; selected?: EntityKey; mode: CameraMode; quality: Quality; trails: boolean;
  commitCut?: number;
  onSelect: (key: EntityKey) => void; onTick: () => void; onError: (error: unknown) => void;
  onQuality: (quality: Quality, fps?: number) => void;
}
export function ViewportView(props: Props) {
  const root=useRef<HTMLDivElement>(null),viewport=useRef<Viewport>();
  const current=useRef(props);current.current=props;
  const [mode,setMode]=useState<CameraMode|undefined>(()=>{
    const value=new URLSearchParams(location.search).get('camera');return ['orbit','follow','chase','cinematic'].includes(value ?? '')?value as CameraMode:undefined;
  });
  const [quality,setQuality]=useState<Quality>(props.quality),[fps,setFps]=useState<number>(),[status,setStatus]=useState(''),[collapsed,setCollapsed]=useState(false);
  useEffect(()=>{
    let frame=0,last=performance.now(),notify=last,lastSeek='';
    try{
      viewport.current=new Viewport(root.current!,props.store.header,{
        quality:props.quality,onSelect:key=>current.current.onSelect(key),onError:error=>current.current.onError(error),onStatus:setStatus,
        onQuality:(quality,fps)=>{setQuality(quality);setFps(fps);current.current.onQuality(quality,fps);},
      });
      viewport.current.setCameraMode(mode ?? props.mode);viewport.current.setSelection(props.selected);
      const animate=(now:number)=>{
        try{
          const p=current.current,delta=(now-last)/1000;last=now;
          p.clock.update(now/1000);const cut=p.clock.playing?undefined:p.commitCut;
          const seekKey=`${p.clock.ns}/${cut}/${p.store.commits.length}`;
          if(seekKey!==lastSeek){p.store.seek(p.clock.ns,cut);lastSeek=seekKey;}
          viewport.current!.render(p.store,p.clock.ns,delta,p.trails);
          if(now-notify>100){p.onTick();notify=now;}frame=requestAnimationFrame(animate);
        }catch(error){current.current.onError(error);}
      };frame=requestAnimationFrame(animate);
    }catch(error){props.onError(error);}
    return()=>{cancelAnimationFrame(frame);viewport.current?.dispose();viewport.current=undefined;root.current?.closest('.viewer-main')?.removeAttribute('data-inspector-collapsed');};
  },[props.store,props.clock]);
  useEffect(()=>{viewport.current?.setSelection(props.selected);},[props.selected]);
  useEffect(()=>{viewport.current?.setCameraMode(mode ?? props.mode);},[props.mode,mode]);
  useEffect(()=>{viewport.current?.setQuality(props.quality);},[props.quality]);
  const events=props.store.commits.flatMap(commit=>commit.messages.filter(message=>message.kind==='event'&&(!props.selected||message.subjects?.some(key=>key.id===props.selected!.id&&key.generation===props.selected!.generation))));
  const duration=BigInt(props.clock.end)-BigInt(props.store.header.start.ns);
  return <>
    <div className="viewport-canvas" data-testid="viewport" ref={root} aria-label="Simulation viewport" />
    <div className="director-toolbar">
      <select aria-label="View camera" value={mode ?? props.mode} onChange={event=>setMode(event.target.value as CameraMode)}>
        <option value="orbit">Orbit</option><option value="follow">Follow unit</option><option value="chase">Chase</option><option value="cinematic">Event director</option>
      </select>
      <select aria-label="View quality" value={quality} onChange={event=>{const q=event.target.value as Quality;setQuality(q);viewport.current?.setQuality(q,true);}}>
        <option value="low">Low</option><option value="med">Balanced</option><option value="high">High</option>
      </select><span>{fps===undefined?'WebGL2':`${fps.toFixed(0)} FPS`}</span>
      <button aria-label="Toggle inspector" aria-expanded={!collapsed} onClick={()=>{const next=!collapsed;setCollapsed(next);root.current?.closest('.viewer-main')?.setAttribute('data-inspector-collapsed',String(next));}}>Inspector {collapsed?'＋':'−'}</button>
    </div>
    <div className="scene-status" title={status}>{status}</div>
    <div className="scene-attribution">{root.current?.dataset.attribution}</div>
    <div className="mission-overlay"><MissionTimeline store={props.store} selected={props.selected} />
      <div className="event-strip" aria-label="Recorded event markers">{duration>0n&&events.slice(-200).map(event=>
        <button key={event.id} title={`${event.schemaId} · ${event.at.ns} ns`} style={{left:`${Number((BigInt(event.at.ns)-BigInt(props.store.header.start.ns))*10000n/duration)/100}%`}}
          onClick={()=>{props.clock.pause();props.clock.seek(event.at.ns);props.onTick();}} aria-label={`Seek ${event.schemaId}`} />)}</div>
    </div>
  </>;
}
