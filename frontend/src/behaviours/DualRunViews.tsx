import type { ComponentProps } from 'react';
import { ViewerPresentation } from '../viewport/ViewerPresentation';
import { BehaviourRunGraph } from './BehaviourRunGraph';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import './run-graph.css';
type Props = Omit<ComponentProps<typeof ViewerPresentation>, 'store'> & { store: TemporalFeedStore; onSeek: (index: number) => void };
export function DualRunViews({ onSeek, ...props }: Props) {
  return <div className="dual-run-views" data-testid="dual-run-views" data-run={props.store.header.runId} data-epoch={props.store.header.epoch} data-cut={props.store.viewCursor?.knownAt}>
    <div className="dual-graph-pane"><BehaviourRunGraph store={props.store} selected={props.selected} onSelect={props.onSelect} onSeek={onSeek} /></div>
    <div className="dual-spatial-pane" data-testid="synchronized-city" data-cut={props.store.viewCursor?.knownAt}><h2>3D · same committed run</h2><ViewerPresentation {...props} /></div>
  </div>;
}
