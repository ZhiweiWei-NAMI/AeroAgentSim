import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { metricLabel } from '../newcomers/metric-help';
import { runStatusLabel } from '../newcomers/presentation';
import { TERMINAL } from '../workbench/api';
import type { ExperimentReport, ExperimentRun } from './api';

const tone = (status: string) => status === 'completed'
  ? 'ok'
  : status === 'stopped' || status === 'interrupted'
    ? 'warn'
    : status === 'failed' || status === 'faulted' || TERMINAL.has(status)
      ? 'bad'
      : 'live';

function unavailable(zh: boolean) {
  return zh ? '不可用' : 'unavailable';
}

/** Exact server number; null is never rendered as zero. */
function numberText(value: number | null, zh: boolean): string {
  return value === null ? unavailable(zh) : String(value);
}

/**
 * Report view for one experiment id. It shows the server's run cells and
 * aggregate rows without adding a combined score or a ranking claim.
 */
export function ExperimentResults({ report, onOpenRun, onCompare }: {
  report: ExperimentReport;
  onOpenRun: (runId: string, status: string) => void;
  onCompare: (ids: string[]) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [chosen, setChosen] = useState<string[]>([]);
  useEffect(() => setChosen([]), [report.id]);

  const title = localized(report, 'template_title', locale) ?? report.template_title;
  const cells = new Map<string, ExperimentRun>();
  for (const run of report.runs) cells.set(`${run.config_id}:${run.seed}`, run);
  const toggle = (id: string) => setChosen(previous => previous.includes(id)
    ? previous.filter(item => item !== id)
    : previous.length < 4 ? [...previous, id] : previous);

  return <section className="wb-experiment-results" data-testid="experiment-results" aria-label={L('Experiment results', '实验结果')}>
    <div className="wb-card-meta">
      <span className="wb-tag info">{report.id}</span>
      <span className={`wb-tag ${tone(report.status)}`} data-testid="experiment-status">{runStatusLabel(report.status, locale)}</span>
      <span className="wb-tag">{report.completed}/{report.total} {L('completed', '已完成')}</span>
      <span className={`wb-tag ${report.failed ? 'bad' : ''}`}>{report.failed} {L('failed', '失败')}</span>
    </div>
    <h4>{title}</h4>
    <p className="hint">
      {L(`Template ${report.template_id} · seeds 0–${Math.max(report.seeds - 1, 0)} · ${report.jobs} worker job(s) · horizon `, `模板 ${report.template_id} · 种子 0–${Math.max(report.seeds - 1, 0)} · ${report.jobs} 个工作进程 · 时长 `)}
      {report.horizon_s === null ? L('template default', '模板默认') : `${report.horizon_s} s`}
    </p>
    {report.error && <div className="wb-issues" role="alert">{report.error}</div>}

    <div style={{ display: 'grid', gap: 10 }}>
      {report.table.map(row => <div key={row.config_id} className="wb-record" style={{ display: 'block' }} data-testid={`experiment-config-${row.config_id}`}>
        <div className="wb-row-main">
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <strong className="wb-mono">{row.config_id}</strong>
            {Object.entries(row.knobs).length
              ? Object.entries(row.knobs).map(([name, value]) => <span key={name} className="wb-tag">{name}={String(value)}</span>)
              : <span className="wb-tag">{L('template defaults', '模板默认')}</span>}
          </div>

          <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
            {Array.from({ length: report.seeds }, (_, seed) => {
              const run = cells.get(`${row.config_id}:${seed}`);
              if (!run) return <div key={seed} className="wb-tag" data-testid={`experiment-cell-${row.config_id}-${seed}`}>
                {L(`seed ${seed}: no cell reported by the server`, `种子 ${seed}：服务器未报告单元`)}
              </div>;
              const terminal = TERMINAL.has(run.status), runId = run.run_id ?? '';
              return <div key={seed} className="wb-record" style={{ display: 'block', padding: 6 }} data-testid={`experiment-cell-${row.config_id}-${seed}`}>
                <div className="wb-card-meta" style={{ alignItems: 'center' }}>
                  <span className="wb-tag">{L(`seed ${seed}`, `种子 ${seed}`)}</span>
                  <span className={`wb-tag ${tone(run.status)}`}>{runStatusLabel(run.status, locale)}</span>
                  {runId && <code className="wb-mono">{runId}</code>}
                </div>
                {run.error && <p className="hint" style={{ color: 'var(--wb-bad)', margin: '4px 0' }}>{L('Error', '错误')}: <span className="wb-mono">{run.error}</span></p>}
                {runId && <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
                  <button className="wb-btn small" onClick={() => onOpenRun(runId, run.status)} data-testid={`experiment-open-${runId}`}>
                    {terminal ? L('Replay', '回放') : L('Open run', '打开运行')}
                  </button>
                  <label className="hint" style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <input
                      type="checkbox"
                      aria-label={L(`Select ${runId} for comparison`, `选择 ${runId} 用于对比`)}
                      checked={chosen.includes(runId)}
                      disabled={chosen.length === 4 && !chosen.includes(runId)}
                      onChange={() => toggle(runId)}
                    />
                    {L('Compare', '对比')}
                  </label>
                </div>}
              </div>;
            })}
          </div>

          {Object.keys(row.metrics).length
            ? <table className="wb-table" style={{ marginTop: 10 }}>
              <thead><tr>
                <th>{L('Metric', '指标')}</th>
                <th>{L('Mean', '均值')}</th>
                <th>{L('Std', '标准差')}</th>
                <th>n</th>
                <th>{L('Unit', '单位')}</th>
              </tr></thead>
              <tbody>
                {Object.entries(row.metrics).map(([name, metric]) => <tr key={name} data-testid={`experiment-metric-${row.config_id}-${name}`}>
                  <td>{metricLabel(name, metric.label ?? name, locale)}<small className="wb-mono"> {name}</small></td>
                  <td className="wb-mono">{numberText(metric.mean, zh)}</td>
                  <td className="wb-mono">{numberText(metric.std, zh)}</td>
                  <td className="wb-mono">{String(metric.n)}</td>
                  <td>{metric.unit === null ? unavailable(zh) : metric.unit}</td>
                </tr>)}
              </tbody>
            </table>
            : <p className="hint">{L('No numeric KPI measurements have been reported for this configuration yet.', '此配置尚未报告数值指标测量值。')}</p>}
        </div>
      </div>)}
    </div>

    <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 10 }}>
      <button className="wb-btn small" disabled={chosen.length < 2} onClick={() => onCompare(chosen)} data-testid="experiment-compare">
        {L(`Compare ${chosen.length || ''}`, `对比 ${chosen.length || ''}`)}
      </button>
      <span className="hint">{L('Select 2–4 run cells. Selection is kept per experiment.', '选择 2–4 个运行单元。所选内容按实验分别保留。')}</span>
    </div>

    <p className="hint" style={{ marginTop: 10 }}>
      {L('Mean, sample standard deviation and n are copied from the server report; null is shown as unavailable. Failed runs are not counted as observations. This panel does not combine metrics into a score or rank configurations.', '均值、样本标准差和 n 直接来自服务器报告；null 显示为不可用。失败的运行不会计为观测。本面板不会把指标合并为总分，也不会对配置排名。')}
    </p>
  </section>;
}
