import {useEffect, useMemo, useRef, useState} from 'react';
import {InputNumber, Select} from 'antd';
import {Details} from '../console/Details';
import {PageState} from '../console/PageState';
import {useI18n} from '../i18n/I18nProvider';
import {localized} from '../i18n/translate';
import {categoryMetadata, CategoryIcon} from './categories';
import {experimentRunUrl, ExperimentApi, experimentIsTerminal, type ExperimentReport, type ExperimentRequest, type ExperimentVary} from './experiment-api';
import type {KnobValue, ScenarioTemplate, TemplateKnob} from './api';
import {metricHelp, metricLabel} from './metric-help';
import {knobOptionLabel, runStatusLabel} from './presentation';
import './experiment.css';

/* Guided full-factorial experiments over real published templates. Values keep
 * their declared types; all results and best-cell flags come from the service. */
const MAX_AXES = 2;
const MAX_VALUES_PER_AXIS = 5;
interface AxisState { knobId: string; values: KnobValue[] }
interface PlanState { templateId: string; axes: AxisState[]; seeds: number | null; jobs: number | null; horizon: string }

const formatMetric = (mean: number | null, std: number | null, unit: string | null): string => {
  if (mean === null) return '—';
  const digits = Number.isInteger(mean) ? 0 : Math.abs(mean) >= 100 ? 1 : 2;
  const spread = std === null ? '' : ` ± ${std.toFixed(2)}`;
  return `${mean.toFixed(digits)}${spread}${unit && unit !== 'count' ? ` ${unit}` : ''}`;
};

function AxisValuesEditor({axis, knob, onChange}: {axis: AxisState; knob: TemplateKnob; onChange: (values: KnobValue[]) => void}) {
  const {t, locale} = useI18n();
  const label = localized(knob, 'label', locale);
  const full = axis.values.length >= MAX_VALUES_PER_AXIS;
  const addValue = (value: KnobValue) => {
    if (full || axis.values.includes(value)) return;
    onChange([...axis.values, value]);
  };
  return <div className="exp-axis" data-testid={`exp-axis-${axis.knobId}`}>
    <span className="exp-axis-knob">{label}</span>
    <div className="exp-axis-values">
      {axis.values.map((value, index) => <span key={index} className="exp-value-chip">
        {knobOptionLabel(knob, value, locale)}
        <button type="button" aria-label={t('Remove {value} from {setting}', {value: knobOptionLabel(knob, value, locale), setting: label})} data-testid={`exp-remove-${axis.knobId}-${String(value)}`} onClick={() => onChange(axis.values.filter(item => item !== value))}>×</button>
      </span>)}
      {axis.values.length === 0 && <span className="exp-axis-empty">{t('No values chosen yet.')}</span>}
    </div>
    {knob.type === 'choice' ? <Select
      aria-label={t('Add a value for {setting}', {setting: label})}
      data-testid={`exp-add-${axis.knobId}`}
      placeholder={t('Add a value')}
      disabled={full}
      value={null}
      options={knob.options!.map((option, index) => ({value: String(index), label: knobOptionLabel(knob, option, locale), disabled: axis.values.includes(option)}))}
      onChange={index => addValue(knob.options![Number(index)])}
    /> : knob.type === 'boolean' ? <span className="exp-number-adder">
      <button type="button" className="console-btn" aria-label={t('Add {setting}: yes', {setting: label})} data-testid={`exp-add-${axis.knobId}-yes`} disabled={full || axis.values.includes(true)} onClick={() => addValue(true)}>{t('yes')}</button>
      <button type="button" className="console-btn" aria-label={t('Add {setting}: no', {setting: label})} data-testid={`exp-add-${axis.knobId}-no`} disabled={full || axis.values.includes(false)} onClick={() => addValue(false)}>{t('no')}</button>
    </span> : <ExperimentNumberAdder knob={knob} values={axis.values} full={full} onAdd={addValue} />}
    {full && <span className="exp-axis-empty">{t('Up to {count} values per setting. Remove one to add another.', {count: MAX_VALUES_PER_AXIS})}</span>}
  </div>;
}

/** Reject invalid drafts on Enter too, not only on InputNumber blur. */
function ExperimentNumberAdder({knob, values, full, onAdd}: {knob: TemplateKnob; values: KnobValue[]; full: boolean; onAdd: (value: KnobValue) => void}) {
  const {t, locale} = useI18n();
  const [draft, setDraft] = useState<number | null>(null);
  const valid = draft !== null && Number.isFinite(draft) && (knob.type !== 'integer' || Number.isSafeInteger(draft))
    && (knob.min === undefined || draft >= knob.min) && (knob.max === undefined || draft <= knob.max);
  const enabled = valid && !full && !values.includes(draft!);
  const add = () => { if (enabled) { onAdd(draft!); setDraft(null); } };
  return <span className="exp-number-adder">
    <InputNumber aria-label={t('Add a value for {setting}', {setting: localized(knob, 'label', locale)})} data-testid={`exp-add-${knob.id}`}
      min={knob.min} max={knob.max} precision={knob.type === 'integer' ? 0 : undefined} step={knob.type === 'integer' ? 1 : 0.1}
      disabled={full} value={draft} onChange={next => setDraft(typeof next === 'number' ? next : null)} onPressEnter={add} />
    <button type="button" className="console-btn" data-testid={`exp-add-${knob.id}-button`} disabled={!enabled} onClick={add}>{t('Add')}</button>
  </span>;
}

function ResultTable({report, apiBase, template}: {report: ExperimentReport; apiBase: string; template?: ScenarioTemplate}) {
  const {t, locale} = useI18n();
  const metricNames = useMemo(() => {
    const names = new Set<string>();
    for (const row of report.table) for (const name of Object.keys(row.metrics)) names.add(name);
    return [...names].sort();
  }, [report.table]);
  const primary = template ? template.kpis.filter(name => metricNames.includes(name)) : metricNames.filter(name => report.table.some(row => typeof row.metrics[name]?.mean === 'number' && row.metrics[name].mean !== 0));
  const more = metricNames.filter(name => !primary.includes(name));
  const settingValue = (key: string, value: KnobValue) => {
    const knob = template?.knobs.find(knob => knob.id === key);
    return t('{setting}: {value}', {setting: knob ? localized(knob, 'label', locale) : key, value: knob ? knobOptionLabel(knob, value, locale) : typeof value === 'boolean' ? value ? t('yes') : t('no') : String(value)});
  };
  const table = (names: string[]) => <div className="exp-table-scroll"><table className="exp-table">
    <thead><tr><th scope="col">{t('Configuration')}</th>{names.map(name => {
      const cell = report.table.find(row => row.metrics[name])?.metrics[name];
      const help = metricHelp(name, locale);
      return <th key={name} scope="col" title={help}>{metricLabel(name, cell?.label ?? name, locale, cell?.label_zh)}{help && <span className="exp-metric-help" aria-label={help} tabIndex={0}> ?</span>}</th>;
    })}</tr></thead>
    <tbody>{report.table.map((row, index) => <tr key={row.config_id} data-testid={`exp-row-${row.config_id}`}>
      <th scope="row"><span className="exp-config-id">{t('Option {number}', {number: index + 1})}</span>
        <span className="exp-config-knobs">{Object.entries(row.knobs).filter(([key]) => key in report.vary).map(([key, value]) => settingValue(key, value)).join(', ') || t('defaults')}</span>
      </th>
      {names.map(name => {
        const cell = row.metrics[name];
        if (!cell) return <td key={name} className="exp-cell" data-testid={`exp-cell-${row.config_id}-${name}`}>—</td>;
        const title = cell.std === null
          ? cell.n === 1 ? t('mean of {count} observed run', {count: cell.n}) : t('mean of {count} observed runs', {count: cell.n})
          : cell.n === 1 ? t('mean of {count} observed run, sample std', {count: cell.n}) : t('mean of {count} observed runs, sample std', {count: cell.n});
        return <td key={name} className={`exp-cell${cell.best ? ' is-best' : ''}`} data-testid={`exp-cell-${row.config_id}-${name}`} title={title}>
          {formatMetric(cell.mean, cell.std, cell.unit)}<span className="exp-cell-n"> n={cell.n}</span>
        </td>;
      })}
    </tr>)}</tbody>
  </table></div>;
  return <div className="exp-results" data-testid="experiment-results">
    {table(primary)}
    {more.length > 0 && <details className="exp-more"><summary>{t('More metrics ({count})', {count: more.length})}</summary>{table(more)}</details>}
    <p className="exp-results-note">{t('Highlighted values are best for that measure; ties are highlighted too. Values show the average ± variation across repeats. “—” means it was not measured.')}</p>
    {report.runs.length > 0 && <section aria-label={t('Individual runs')} className="exp-runs">
      <h3>{t('Runs behind these numbers')}</h3>
      <ul className="exp-run-list">{report.runs.map(run => <li key={`${run.config_id}-seed-${run.seed}`} data-testid={`exp-run-${run.config_id}-seed-${run.seed}`}>
        <span className="exp-run-label">{t('Option {number} · repeat {repeat}', {number: report.table.findIndex(row => row.config_id === run.config_id) + 1, repeat: run.seed + 1})}</span>
        {run.status && <span className={`exp-run-status is-${run.status}`}>{runStatusLabel(run.status, locale, true)}</span>}
        {run.run_id ? <a href={experimentRunUrl(run.run_id, apiBase, report.template_id)} data-testid={`exp-open-run-${run.config_id}-seed-${run.seed}`}>{t('Open replay')}</a>
          : <span className="exp-run-error" title={run.error ?? undefined}>{run.error ?? t('no run was produced')}</span>}
      </li>)}</ul>
    </section>}
  </div>;
}

export default function ExperimentPanel({base, templates, onClose}: {base: string; templates: ScenarioTemplate[]; onClose?: () => void}) {
  const {t, locale} = useI18n();
  const api = useMemo(() => new ExperimentApi(base), [base]);
  const [plan, setPlan] = useState<PlanState>(() => ({templateId: templates[0]?.id ?? '', axes: [], seeds: 3, jobs: 1, horizon: ''}));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [report, setReport] = useState<ExperimentReport>();
  const pollRef = useRef<number>();
  const attemptRef = useRef(0);
  const template = templates.find(candidate => candidate.id === plan.templateId);
  useEffect(() => () => { ++attemptRef.current; if (pollRef.current !== undefined) window.clearInterval(pollRef.current); }, []);
  const stopPolling = () => { if (pollRef.current !== undefined) { window.clearInterval(pollRef.current); pollRef.current = undefined; } };
  const startPolling = (experimentId: string) => {
    stopPolling();
    let inFlight = false;
    const attempt = attemptRef.current;
    pollRef.current = window.setInterval(() => {
      if (inFlight) return;
      inFlight = true;
      api.get(experimentId).then(next => {
        if (attemptRef.current !== attempt) return;
        setReport(next); setError('');
        if (experimentIsTerminal(next.status)) stopPolling();
      }).catch(problem => {
        if (attemptRef.current !== attempt) return;
        setError(String(problem instanceof Error ? problem.message : problem)); stopPolling();
      }).finally(() => { inFlight = false; });
    }, 2000);
  };
  const retryProgress = async () => {
    if (!report) return;
    setBusy(true); setError('');
    const attempt = attemptRef.current;
    try {
      const next = await api.get(report.id);
      if (attemptRef.current !== attempt) return;
      setReport(next);
      if (!experimentIsTerminal(next.status)) startPolling(next.id);
    } catch (problem) {
      if (attemptRef.current === attempt) setError(String(problem instanceof Error ? problem.message : problem));
    } finally { setBusy(false); }
  };
  const start = async () => {
    if (!template || plan.seeds === null || plan.jobs === null) return;
    stopPolling(); setBusy(true); setError(''); setReport(undefined);
    const attempt = ++attemptRef.current;
    try {
      const vary: ExperimentVary = Object.fromEntries(plan.axes.map(axis => [axis.knobId, axis.values]));
      const body: ExperimentRequest = {vary, seeds: plan.seeds, jobs: plan.jobs, ...(plan.horizon.trim() ? {horizon_s: Number(plan.horizon)} : {})};
      const created = await api.create(template.id, body);
      if (attemptRef.current !== attempt) return;
      setReport(created);
      if (!experimentIsTerminal(created.status)) startPolling(created.id);
    } catch (problem) {
      if (attemptRef.current === attempt) setError(String(problem instanceof Error ? problem.message : problem));
    } finally { setBusy(false); }
  };
  const toggleAxis = (knob: TemplateKnob) => setPlan(current => ({...current, axes: current.axes.some(axis => axis.knobId === knob.id)
    ? current.axes.filter(axis => axis.knobId !== knob.id)
    : current.axes.length >= MAX_AXES ? current.axes : [...current.axes, {knobId: knob.id, values: []}]}));
  const configurations = plan.axes.reduce((total, axis) => total * Math.max(1, axis.values.length), 1);
  const seedsValid = plan.seeds !== null && Number.isSafeInteger(plan.seeds) && plan.seeds > 0;
  const jobsValid = plan.jobs !== null && Number.isSafeInteger(plan.jobs) && plan.jobs > 0;
  const horizonValid = plan.horizon.trim() === '' || (Number(plan.horizon) > 0 && Number.isFinite(Number(plan.horizon)));
  const ready = !!template && configurations >= 2 && plan.axes.every(axis => axis.values.length > 0) && seedsValid && jobsValid && horizonValid;
  const running = report !== undefined && !experimentIsTerminal(report.status);
  const progress = report && report.total > 0 ? Math.round(((report.completed + report.failed) / report.total) * 100) : 0;
  const reportTemplate = templates.find(candidate => candidate.id === report?.template_id);
  const reportTitle = reportTemplate ? localized(reportTemplate, 'title', locale) : localized(report, 'template_title', locale);
  const horizon = report?.horizon_s === null ? t('template default') : t('{seconds} s', {seconds: report?.horizon_s});

  return <section className="exp-panel" data-testid="experiment-panel" aria-label={t('Run an experiment')}>
    <header className="exp-panel-header"><div><h2>{report ? reportTitle : t('Run an experiment')}</h2><p className="exp-panel-sub">{t('Vary one or two settings across a few values and compare the averaged results, repetitions included.')}</p></div>
      {onClose && <button type="button" className="console-btn" data-testid="experiment-close" onClick={onClose}>{t('Close')}</button>}
    </header>
    {templates.length === 0 ? <PageState kind="empty" title={t('No templates to experiment with')} description={t('Experiments need a published scenario template. Publish one first, then come back.')} />
      : <details className="exp-plan" open={!report}><summary>{report ? t('Experiment settings · {count} runs', {count: report.total}) : t('Experiment settings')}</summary>
        <div className="exp-step"><h3>{t('1 · Scenario')}</h3>
          <Select aria-label={t('Scenario template')} data-testid="experiment-template" value={plan.templateId || undefined} placeholder={t('Choose a scenario')}
            options={templates.map(candidate => ({value: candidate.id, label: localized(candidate, 'title', locale)}))}
            onChange={value => setPlan(current => ({...current, templateId: String(value), axes: []}))} />
          {template && <p className="exp-template-meta"><CategoryIcon category={template.category} size={13} /> {categoryMetadata(template.category, locale).label} · {localized(template, 'summary', locale)}</p>}
        </div>
        <div className="exp-step"><h3>{t('2 · Settings to vary')}</h3>
          {!template?.knobs.length && template && <p>{t('This scenario has no adjustable settings to vary.')}</p>}
          <div className="exp-knob-picker" role="group" aria-label={t('Choose settings to vary')}>{template?.knobs.map(knob => <label key={knob.id} className={`exp-knob-toggle${plan.axes.some(axis => axis.knobId === knob.id) ? ' is-active' : ''}`}>
            <input type="checkbox" data-testid={`exp-knob-${knob.id}`} checked={plan.axes.some(axis => axis.knobId === knob.id)} disabled={plan.axes.length >= MAX_AXES && !plan.axes.some(axis => axis.knobId === knob.id)} onChange={() => toggleAxis(knob)} />{localized(knob, 'label', locale)}
          </label>)}</div>
          {plan.axes.map(axis => <AxisValuesEditor key={axis.knobId} axis={axis} knob={template!.knobs.find(knob => knob.id === axis.knobId)!} onChange={values => setPlan(current => ({...current, axes: current.axes.map(item => item.knobId === axis.knobId ? {...item, values} : item)}))} />)}
        </div>
        <div className="exp-step"><h3>{t('3 · Repeats and duration')}</h3><div className="exp-run-options">
          <label className="exp-option">{t('Repeats per configuration (same seeds each time)')}<InputNumber aria-label={t('Repeats per configuration')} data-testid="experiment-seeds" min={1} precision={0} value={plan.seeds} onChange={next => setPlan(current => ({...current, seeds: typeof next === 'number' ? next : null}))} /></label>
          <label className="exp-option">{t('Parallel runs')}<InputNumber aria-label={t('Parallel runs')} data-testid="experiment-jobs" min={1} precision={0} value={plan.jobs} onChange={next => setPlan(current => ({...current, jobs: typeof next === 'number' ? next : null}))} /></label>
          <label className="exp-option">{t('Simulation duration (seconds, blank for template default)')}<InputNumber aria-label={t('Simulation duration in seconds')} data-testid="experiment-horizon" min={0.1} value={plan.horizon.trim() === '' ? null : Number(plan.horizon)} onChange={next => setPlan(current => ({...current, horizon: next === null ? '' : String(next)}))} /></label>
        </div><p className="exp-preview" data-testid="experiment-preview">{ready && plan.seeds !== null
          ? plan.seeds === 1 ? t('Will start {configurations} configurations × {repeats} repeat = {runs} runs.', {configurations, repeats: plan.seeds, runs: configurations * plan.seeds}) : t('Will start {configurations} configurations × {repeats} repeats = {runs} runs.', {configurations, repeats: plan.seeds, runs: configurations * plan.seeds})
          : t('Choose one or two settings and at least two total configurations to compare.')}</p></div>
        <div className="exp-panel-actions"><button type="button" className="console-btn console-btn-primary" data-testid="experiment-start" disabled={busy || running || !ready} onClick={() => void start()}>{busy ? t('Starting…') : running ? t('Experiment in progress…') : t('Start experiment')}</button></div>
      </details>}
    {error && <div className="template-error-card" role="alert" data-testid="experiment-error">
      <strong>{report ? t('The experiment could not be followed.') : t('The experiment could not be started.')}</strong>
      <p>{report ? t('Progress updates stopped arriving. Retry to check the experiment and continue following it.') : t('The service could not start this experiment. Check its settings and the request details below.')}</p>
      {report && <button type="button" className="console-btn" data-testid="experiment-retry-progress" disabled={busy} onClick={() => void retryProgress()}>{t('Retry progress updates')}</button>}
      <Details title={t('Experiment request')}><p>{error}</p></Details>
    </div>}
    {report && <div className="exp-report" data-testid="experiment-report"><div className="exp-progress-row" data-status={report.status}>
      <span className="exp-progress-label">
        {report.status === 'completed' && (report.failed ? t('Completed — {completed} of {total} runs succeeded, {failed} failed.', {completed: report.completed, total: report.total, failed: report.failed}) : t('Completed — {completed} of {total} runs succeeded.', {completed: report.completed, total: report.total}))}
        {report.status === 'faulted' && t('The experiment stopped early: {error}.', {error: report.error ?? t('unknown error')})}
        {report.status === 'interrupted' && t('The experiment was interrupted before every run finished.')}
        {running && t('Running — {finished} of {total} runs finished.', {finished: report.completed + report.failed, total: report.total})}
      </span><span className="exp-progress-count" data-testid="experiment-progress">{report.completed + report.failed}/{report.total}</span><progress max={100} value={progress} aria-label={t('Experiment progress')} />
    </div>
      {report.status !== 'faulted' || report.table.some(row => Object.keys(row.metrics).length > 0)
        ? report.table.some(row => Object.keys(row.metrics).length > 0) ? <ResultTable report={report} apiBase={base} template={reportTemplate} /> : <PageState kind="loading" title={t('Waiting for the first finished runs')} description={t('Averages appear here as soon as the first runs complete.')} />
        : <PageState kind="error" title={t('No results were produced')} description={report.error ?? t('The experiment stopped before any run finished.')} />}
      <p className="exp-report-meta">{t('Simulation duration: {horizon} · seeds 0–{lastSeed} reused for every configuration.', {horizon, lastSeed: report.seeds - 1})}</p>
    </div>}
  </section>;
}
