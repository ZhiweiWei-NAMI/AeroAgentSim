import { useState } from 'react';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { operation } from '../operations';
import type { Dependency } from './model';
import { OperationDiff, operationKey, operationText, useOperationsReview } from './Operations';

type Props = {
  api: WorkbenchApi; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: { id: string; revision: number }; draft: Scenario;
  onProject: (project: Project) => void; L: (en: string, zh: string) => string; entityId: string; onDone: () => void;
};

/** Lowalt places stay in C11; generic objects use the server's dependency-removal endpoint. */
export function RemoveEntity(props: Props) {
  const entities = Array.isArray(props.draft.entities) ? props.draft.entities as Array<Record<string, any>> : [];
  const entity = entities.find(row => row.id === props.entityId);
  return entity?.facts && 'lowalt.site_position' in entity.facts ? <RemovePlace {...props} /> : <RemoveGeneric {...props} />;
}

function RemovePlace({ api, project, onProject, L, entityId, onDone }: Props) {
  const review = useOperationsReview(api, project as Project, [operation('remove_place', { id: entityId })]);
  const apply = async () => { const updated = await review.apply(); if (updated) { onProject(updated); onDone(); } };
  return <div className="wb-object-form">
    <p className="hint">{L(`Remove place ${entityId}? Review the changes before confirming.`, `移除设施 ${entityId}？请查看变更后再确认。`)}</p>
    {review.preview && <OperationDiff {...review.preview} stale={review.stale} L={L} />}
    {review.problem && <p className="wb-field-error" role="alert">{operationText(review.problem.message, L)}</p>}
    <div className="wb-object-actions">
      <button className="wb-btn small" disabled={!!review.busy} onClick={() => void review.load()}>{review.preview ? L('Preview again', '重新预览') : L('Preview removal', '预览移除')}</button>
      {review.preview && <button className="wb-btn small danger" disabled={review.stale || !!review.busy} onClick={() => void apply()}>{L('Confirm removal', '确认移除')}</button>}
      <button className="wb-btn small ghost" disabled={!!review.busy} onClick={onDone}>{L('Cancel', '取消')}</button>
    </div>
  </div>;
}

function RemoveGeneric({ api, studioApi, project, draft, onProject, L, entityId, onDone }: Props) {
  const [receipt, setReceipt] = useState<{ dependencies: Dependency[]; saved: Project; key: string; sourceRevision: number }>();
  const [busy, setBusy] = useState(false), [error, setError] = useState<string>(), [gone, setGone] = useState(false);
  const [rejected, setRejected] = useState(false);
  const key = operationKey({ id: project.id, revision: 0 }, [operation('remove_instance', { id: entityId, draft })]);
  const stale = !!receipt && (receipt.key !== key || rejected || project.revision !== receipt.saved.revision && project.revision !== receipt.sourceRevision);
  const load = async () => {
    setBusy(true); setError(undefined); setReceipt(undefined);
    try {
      const saved = await api.save(project.id, draft, project.revision);
      onProject(saved);
      const answer = await studioApi.request<{ dependencies: Dependency[] }>(
        `/v1/studio/workspaces/${encodeURIComponent(project.id)}/remove`, { id: entityId, revision: saved.revision, dry_run: true });
      if (!Array.isArray(answer.dependencies)) throw Error(L('The removal review did not include a dependency list.', '移除预览没有返回依赖清单。'));
      setReceipt({ dependencies: answer.dependencies, saved, key, sourceRevision: project.revision }); setRejected(false);
    } catch (problem) {
      const text = problem instanceof Error ? problem.message : String(problem);
      setError(/HTTP (404|501):/.test(text) ? L('This server does not offer removal yet, so the instance cannot be deleted from here.', '当前服务器尚不支持删除，因此无法在此移除该实例。') : operationText(text, L));
    } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!receipt || stale || busy) return;
    setBusy(true); setError(undefined);
    try {
      // Do not save again here: confirmation must use the revision whose dependencies were reviewed.
      const updated = await studioApi.request<Project>(
        `/v1/studio/workspaces/${encodeURIComponent(project.id)}/remove`, { id: entityId, revision: receipt.saved.revision });
      setGone(true); onProject(updated); onDone();
    } catch (problem) { setError(operationText(problem instanceof Error ? problem.message : String(problem), L)); setRejected(true); }
    finally { setBusy(false); }
  };
  if (gone) return <p className="hint" role="status">{L(`Removed ${entityId} and its dependencies.`, `已移除 ${entityId} 及其依赖。`)}</p>;
  return <div className="wb-object-form">
    {receipt && <>
      <p className="hint">{L(`Removing ${entityId} also removes ${receipt.dependencies.length} reference(s).`, `移除 ${entityId} 还会移除 ${receipt.dependencies.length} 处引用。`)}</p>
      <OperationDiff L={L} stale={stale} diff={{ added: [], changed: [], removed: [
        { kind: 'entity', id: entityId, label: entityId },
        ...receipt.dependencies.map(dep => ({ kind: dep.kind, id: dep.path, label: dep.detail })),
      ] }} />
    </>}
    {error && <p className="wb-field-error" role="alert">{error}</p>}
    <div className="wb-object-actions">
      <button className="wb-btn small danger" disabled={busy} onClick={() => void load()}>{busy ? L('Checking…', '检查中…') : receipt ? L('Preview again', '重新预览') : L('Remove', '移除')}</button>
      {receipt && <button className="wb-btn small danger" disabled={busy || stale} onClick={() => void confirm()}>{L('Confirm removal', '确认移除')}</button>}
      <button className="wb-btn small ghost" disabled={busy} onClick={onDone}>{L('Cancel', '取消')}</button>
    </div>
  </div>;
}
