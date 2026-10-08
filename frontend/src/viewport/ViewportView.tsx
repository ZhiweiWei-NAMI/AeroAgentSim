import { useEffect, useRef } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import type { PlaybackClock } from './clock';
import { Viewport, type CameraMode } from './viewport';
import type { Quality } from './pipeline';

interface Props {
  store: FeedStore; clock: PlaybackClock; selected?: EntityKey; mode: CameraMode; quality: Quality; trails: boolean;
  commitCut?: number;
  onSelect: (key: EntityKey) => void; onTick: () => void; onError: (error: unknown) => void;
  onQuality: (quality: Quality, fps?: number) => void;
}
export function ViewportView(props: Props) {
  const root = useRef<HTMLDivElement>(null), viewport = useRef<Viewport>();
  const current = useRef(props); current.current = props;
  useEffect(() => {
    let frame = 0, last = performance.now(), notify = last;
    try {
      viewport.current = new Viewport(root.current!, props.store.header, {
        quality: props.quality, onSelect: key => current.current.onSelect(key),
        onError: error => current.current.onError(error), onQuality: (quality, fps) => current.current.onQuality(quality, fps),
      });
      const animate = (now: number) => {
        try {
          const p = current.current, delta = Math.min((now - last) / 1000, 0.25); last = now;
          p.clock.update(now / 1000); p.store.seek(p.clock.ns, p.clock.playing ? undefined : p.commitCut);
          viewport.current!.render(p.store, p.clock.ns, delta, p.trails);
          if (now - notify > 100) { p.onTick(); notify = now; }
          frame = requestAnimationFrame(animate);
        } catch (error) { current.current.onError(error); }
      };
      frame = requestAnimationFrame(animate);
    } catch (error) { props.onError(error); }
    return () => { cancelAnimationFrame(frame); viewport.current?.dispose(); viewport.current = undefined; };
  }, [props.store, props.clock]);
  useEffect(() => { viewport.current?.setSelection(props.selected); }, [props.selected]);
  useEffect(() => { viewport.current?.setCameraMode(props.mode); }, [props.mode]);
  useEffect(() => { viewport.current?.setQuality(props.quality); }, [props.quality]);
  return <div className="viewport-canvas" data-testid="viewport" ref={root} aria-label="Simulation viewport" />;
}
