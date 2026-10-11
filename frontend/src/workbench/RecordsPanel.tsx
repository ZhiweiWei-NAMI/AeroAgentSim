import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { runStatusLabel } from '../newcomers/presentation';
import { TERMINAL, type RunRecord, type WorkbenchApi } from './api';

const tone = (status: string) => status === 'completed' ? 'ok' : status === 'stopped' ? 'warn' : TERMINAL.has(status) ? 'bad' : status === 'initialized' ? 'info' : 'live';

/** Run records: open one in replay, compare several, or derive a new draft from one. */
export function RecordsPanel({ api, projectId, current, onOpen, onCompare, onDerive, onClose, refreshKey }: {
  api: WorkbenchApi; projectId?: string; current?: string; refreshKey: number;
  onOpen: (run: RunRecord) => void; onCompare: (ids: string[]) => void; onDerive: (run: RunRecord) => Promise<void>; onClose: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [runs, setRuns] = useState<RunRecord[]>(), [error, setError] = useState<string>();
  const [scope, setScope] = useState<'project' | 'all'>(projectId ? 'project' : 'all'), [chosen, setChosen] = useState<string[]>([]), [busy, setBusy] = useState<string>();
  useEffect(() => {
    const abort = new AbortController();
    const load = () => void api.runs(abort.signal).then(rows => { setRuns(rows.reverse()); setError(undefined); }).catch(problem => { if (!abort.signal.aborted) setError(String(problem)); });
    load(); const timer = setInterval(load, 4000);
    return () => { abort.abort(); clearInterval(timer); };
  }, [api, refreshKey]);
  const shown = (runs ?? []).filter(run => scope === 'all' || run.studio_workspace === projectId);
  const toggle = (id: string) => setChosen(previous => previous.includes(id) ? previous.filter(item => item !== id) : previous.length < 4 ? [...previous, id] : previous);
  return <aside className="wb-panel wb-left wb-glass" aria-label={L('Run records', '运行记录')} data-testid="wb-records" style={{ width: 'calc(var(--wb-left) + 60px)' }}>
    <div className="wb-panel-head"><h3>{L('Run records', '运行记录')}</h3><button className="wb-x" aria-label={L('Close records', '关闭运行记录')} onClick={onClose}>✕</button></div>
    <div style={{ display: 'flex', gap: 6, padding: '0 14px 8px' }}>
      {projectId && <button className="wb-btn small" aria-pressed={scope === 'project'} onClick={() => { setScope('project'); setChosen([]); }}>{L('This project', '本项目')}</button>}
      <button className="wb-btn small" aria-pressed={scope === 'all'} onClick={() => { setScope('all'); setChosen([]); }}>{L('All runs', '全部运行')}</button>
      <span className="wb-spacer" />
      <button className="wb-btn small primary" disabled={chosen.length < 2} onClick={() => onCompare(chosen)} data-testid="wb-compare">{L(`Compare ${chosen.length || ''}`, `对比 ${chosen.length || ''}`)}</button>
    </div>
    <div className="wb-panel-body">
      <p className="hint" aria-live="polite">{L(`Select 2–4 runs to compare (${chosen.length}/4 selected).`, `选择 2–4 次运行进行对比（已选 ${chosen.length}/4）。`)}</p>
      {!runs && !error && <div role="status">{L('Loading run records…', '正在加载运行记录…')}</div>}
      {error && <div className="wb-issues" role="alert">{error}</div>}
      {runs && !shown.length && <div className="wb-empty"><strong>{L('No runs yet', '还没有运行')}</strong>{L('Save a version, initialize it and press Start.', '保存版本、初始化，然后按“启动”。')}</div>}
      {shown.map(run => <div key={run.id} className="wb-record" aria-current={run.id === current}>
        <input type="checkbox" aria-label={L(`Select ${run.id} for comparison`, `选择 ${run.id} 用于对比`)} checked={chosen.includes(run.id)} disabled={chosen.length === 4 && !chosen.includes(run.id)} onChange={() => toggle(run.id)} />
        <div className="wb-row-main">
          <div>{localized(run, 'template_title', locale) ?? run.workspace_name ?? run.title ?? run.scenario}</div>
          <small className="wb-mono">{run.id}</small>
          <div className="wb-card-meta" style={{ marginTop: 4 }}>
            <span className={`wb-tag ${tone(run.status)}`}>{runStatusLabel(run.status, locale)}</span>
            {run.workspace_version !== undefined && <span className="wb-tag info">v{run.workspace_version}</span>}
            {run.derived_from && <span className="wb-tag" title={run.derived_from.run_id}>{L('derived', '派生')}</span>}
            {run.knobs && Object.entries(run.knobs).slice(0, 3).map(([key, value]) => <span key={key} className="wb-tag">{key}={String(value)}</span>)}
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
            <button className="wb-btn small" onClick={() => onOpen(run)} data-testid="wb-open-run">{TERMINAL.has(run.status) ? L('Replay', '回放') : L('Open live', '打开实时')}</button>
            <button className="wb-btn small" disabled={busy === run.id} title={L('Create a new draft from this run’s configuration; the run itself is kept', '以此次运行的配置新建草稿，原运行保留')} onClick={() => { setBusy(run.id); void onDerive(run).finally(() => setBusy(undefined)); }}>{busy === run.id ? L('Creating…', '创建中…') : L('Modify & retry', '修改后再试')}</button>
          </div>
        </div>
      </div>)}
    </div>
  </aside>;
}
