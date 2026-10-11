import { useRef, useState, type ComponentProps, type PointerEvent } from 'react';
import { ViewerPresentation } from '../viewport/ViewerPresentation';
import { BehaviourRunGraph } from './BehaviourRunGraph';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import './run-graph.css';
import { useI18n } from '../i18n/I18nProvider';
export type RunViewLayout = 'split' | 'graph' | 'spatial';
type Props = Omit<ComponentProps<typeof ViewerPresentation>, 'store'> & { store: TemporalFeedStore; onSeek: (index: number) => void; layout?: RunViewLayout };
export function DualRunViews({ onSeek, layout = 'split', ...props }: Props) {
  const { t, locale } = useI18n();
  const container = useRef<HTMLDivElement>(null), [split, setSplit] = useState(48);
  const resize = (event: PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId) || !container.current) return;
    const rect = container.current.getBoundingClientRect();
    setSplit(Math.max(22, Math.min(78, (event.clientX - rect.left) / rect.width * 100)));
  };
  return <div ref={container} className={`dual-run-views run-layout-${layout}`} style={layout === 'split' ? { gridTemplateColumns: `minmax(0, ${split}fr) 7px minmax(0, ${100 - split}fr)` } : { gridTemplateColumns: 'minmax(0, 1fr)' }} data-testid="dual-run-views" data-run={props.store.header.runId} data-epoch={props.store.header.epoch} data-cut={props.store.viewCursor?.knownAt}>
    <div className="dual-graph-pane" hidden={layout === 'spatial'}><BehaviourRunGraph store={props.store} selected={props.selected} onSelect={props.onSelect} onSeek={onSeek} highlights={props.highlights} /></div>
    <div className="run-view-resizer" hidden={layout !== 'split'} role="separator" aria-label={locale === 'zh-CN' ? '调整图与三维视图宽度' : 'Resize graph and 3D views'} aria-orientation="vertical" aria-valuemin={22} aria-valuemax={78} aria-valuenow={Math.round(split)} tabIndex={0} onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={resize} onPointerUp={event => event.currentTarget.releasePointerCapture(event.pointerId)} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setSplit(value => Math.max(22, Math.min(78, value + (event.key === 'ArrowLeft' ? -3 : 3)))); } }} />
    <div className="dual-spatial-pane" hidden={layout === 'graph'} data-testid="synchronized-city" data-cut={props.store.viewCursor?.knownAt}><div className="inspection-city-header"><h2>{t('City view')}</h2><span>{t('Same timeline moment')}</span></div><ViewerPresentation {...props} /></div>
  </div>;
}
