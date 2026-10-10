import type { ComponentProps } from 'react';
import { ViewerPresentation } from '../viewport/ViewerPresentation';
import { BehaviourRunGraph } from './BehaviourRunGraph';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import './run-graph.css';
import { useI18n } from '../i18n/I18nProvider';
type Props = Omit<ComponentProps<typeof ViewerPresentation>, 'store'> & { store: TemporalFeedStore; onSeek: (index: number) => void };
export function DualRunViews({ onSeek, ...props }: Props) {
  const { t } = useI18n();
  return <div className="dual-run-views" data-testid="dual-run-views" data-run={props.store.header.runId} data-epoch={props.store.header.epoch} data-cut={props.store.viewCursor?.knownAt}>
    <div className="dual-graph-pane"><BehaviourRunGraph store={props.store} selected={props.selected} onSelect={props.onSelect} onSeek={onSeek} highlights={props.highlights} /></div>
    <div className="dual-spatial-pane" data-testid="synchronized-city" data-cut={props.store.viewCursor?.knownAt}><div className="inspection-city-header"><h2>{t('City view')}</h2><span>{t('Same timeline moment')}</span></div><ViewerPresentation {...props} /></div>
  </div>;
}
