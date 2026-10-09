import { useEffect, useRef, useState } from 'react';
import type { ComponentProps } from 'react';
import { ViewportView } from './ViewportView';
import { NonspatialViews } from './NonspatialViews';
import { MissionTimeline } from './MissionTimeline';
import { resolveBinding } from './bindings';
import './nonspatial.css';

type Props = ComponentProps<typeof ViewportView>;

/** A display choice only: both panels read the same exact replay cut as the inspector. */
export function ViewerPresentation(props: Props) {
  const spatial = props.store.entities.size
    ? [...props.store.entities.values()].some(entity => resolveBinding(props.store.header, entity.typeId))
    : props.store.header.presentation.length > 0;
  const [panel, setPanel] = useState<'graph' | 'state' | undefined>();
  const current = useRef(props); current.current = props;
  useEffect(() => {
    if (spatial) return;
    let frame = 0, notified = 0, lastSeek = '', dirty = false;
    const animate = (now: number) => {
      try {
        const p = current.current;
        p.clock.update(now / 1000);
        const cut = p.clock.playing ? undefined : p.commitCut;
        const seek = `${p.clock.ns}/${cut}/${p.store.commits.length}`;
        if (seek !== lastSeek) { p.store.seek(p.clock.ns, cut); lastSeek = seek; dirty = true; }
        if (dirty && now - notified > 100) { p.onTick(); notified = now; dirty = false; }
        frame = requestAnimationFrame(animate);
      } catch (error) { current.current.onError(error); }
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [spatial, props.store, props.clock]);
  const view = panel ?? 'graph';
  return <div className={`viewer-presentation ${spatial ? 'has-spatial' : 'inspector-first'}`} data-testid="viewer-presentation">
    <nav className="presentation-tabs" aria-label="Presentation views">
      {spatial && <button aria-pressed={panel === undefined} onClick={() => setPanel(undefined)}>3D only</button>}
      <button aria-pressed={!spatial && view === 'graph' || panel === 'graph'} onClick={() => setPanel('graph')}>Relations / topology</button>
      <button aria-pressed={view === 'state'} onClick={() => setPanel('state')}>Queue / state</button>
      {!spatial && <span>Inspector first · recorded facts</span>}
    </nav>
    <div className="presentation-content">
      {spatial && <div className="spatial-pane"><ViewportView {...props} /></div>}
      {(!spatial || panel) && <div className="nonspatial-pane">
        <NonspatialViews store={props.store} selected={props.selected} onSelect={props.onSelect} view={view} />
        {!spatial && <MissionTimeline store={props.store} selected={props.selected} />}
      </div>}
    </div>
  </div>;
}
