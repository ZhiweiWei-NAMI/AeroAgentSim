import { operation } from '../operations';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { OperationDiff, operationText, useOperationsReview } from './Operations';

/** One bootstrap card, with the persistent generic-object entry as its secondary action. */
export function LowaltStart({ api, project, onProject, L, onAddObjects, genericOpen }: {
  api: WorkbenchApi; project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; onAddObjects?: () => void; genericOpen?: boolean;
}) {
  const review = useOperationsReview(api, project, [operation('add_lowalt_components')]);
  const apply = async () => { const updated = await review.apply(); if (updated) onProject(updated); };
  return <div className="wb-empty wb-lowalt-start" data-testid="wb-lowalt-start">
    <strong>{L('Start with standard low-altitude components', '先安装标准低空组件')}</strong>
    <p className="hint">{L('Adds the wind engine, task engine and execution components a drone scene needs. Review the changes before applying.', '添加无人机场景所需的风场引擎、任务引擎和执行组件。查看变更后再应用。')}</p>
    {review.preview && <OperationDiff {...review.preview} stale={review.stale} L={L} />}
    {review.problem && <p className="wb-field-error" role="alert">{operationText(review.problem.message, L)}</p>}
    <div className="wb-object-actions">
      <button className="wb-btn primary" disabled={!!review.busy} onClick={() => void review.load()}>
        {review.busy === 'preview' ? L('Previewing…', '预览中…') : review.preview ? L('Preview again', '重新预览') : L('Add standard low-altitude components', '添加标准低空组件')}</button>
      {review.preview && <button className="wb-btn primary" disabled={!!review.busy || review.stale} onClick={() => void apply()}>
        {review.busy === 'apply' ? L('Applying…', '应用中…') : L('Apply', '应用')}</button>}
      {review.preview && <button className="wb-btn ghost" disabled={!!review.busy} onClick={review.clear}>{L('Close review', '关闭变更预览')}</button>}
      {onAddObjects && <button className="wb-btn ghost" aria-expanded={genericOpen} onClick={onAddObjects}>{genericOpen ? L('Close add objects', '收起添加对象') : L('Add objects', '添加对象')}</button>}
    </div>
  </div>;
}
