import {useEffect, useMemo, useRef, useState} from 'react';
import {InputNumber, Select, Switch} from 'antd';
import {Details} from '../console/Details';
import {PageState} from '../console/PageState';
import {categoryMetadata, CategoryIcon} from './categories';
import {experimentRunUrl, ExperimentApi, experimentIsTerminal, type ExperimentReport, type ExperimentRequest, type ExperimentVary} from './experiment-api';
import type {KnobValue, ScenarioTemplate, TemplateKnob} from './api';
import {metricHelp} from './metric-help';
import './experiment.css';

/* ExperimentPanel — guided full-factorial experiments over published templates.
 * Pick a template, pick one or two declared knobs, give each a few typed
 * values, choose how many matched-seed repetitions to run, then watch the real
 * progress and read the aggregated table. Every number comes from the
 * backend report; unmeasured values stay null and best-cell highlighting only
 * honors the backend's descriptive per-metric flag. The experiment endpoints
 * are not on every deployment yet, so production errors are shown truthfully. */

const MAX_AXES = 2;
const MAX_VALUES_PER_AXIS = 5;

interface AxisState {
  knobId: string;
  /** Ordered, de-duplicated typed values for this axis. */
  values: KnobValue[];
}

/** Form state for one experiment plan. */
interface PlanState {
  templateId: string;
  axes: AxisState[];
  seeds: number | null;
  jobs: number | null;
  horizon: string;
}

const horizonText = (report: ExperimentReport): string =>
  report.horizon_s === null ? 'template default' : `${report.horizon_s} s`;

const formatMetric = (mean: number | null, std: number | null, unit: string | null): string => {
  if(mean===null)return '—';
  const digits = Number.isInteger(mean) ? 0 : Math.abs(mean) >= 100 ? 1 : 2;
  const value = mean.toFixed(digits);
  const spread = std === null ? '' : ` ± ${std.toFixed(2)}`;
  return `${value}${spread}${unit && unit!=='count' ? ` ${unit}` : ''}`;
};

function AxisValuesEditor({axis, knob, onChange}: {
  axis: AxisState;
  knob: TemplateKnob;
  onChange: (values: KnobValue[]) => void;
}) {
  const addValue = (value: KnobValue) => {
    if (axis.values.some(item => item === value)) return;
    onChange([...axis.values, value].slice(0, MAX_VALUES_PER_AXIS));
  };
  const removeValue = (value: KnobValue) => onChange(axis.values.filter(item => item !== value));
  return (
    <div className="exp-axis" data-testid={`exp-axis-${axis.knobId}`}>
      <span className="exp-axis-knob">{knob.label}</span>
      <div className="exp-axis-values">
        {axis.values.map(value => (
          <span key={String(value)} className="exp-value-chip">
            {typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)}
            <button type="button" aria-label={`Remove ${String(value)} from ${knob.label}`} data-testid={`exp-remove-${axis.knobId}-${String(value)}`} onClick={() => removeValue(value)}>×</button>
          </span>
        ))}
        {axis.values.length === 0 && <span className="exp-axis-empty">No values chosen yet.</span>}
      </div>
      {knob.type === 'choice' ? (
        <Select
          aria-label={`Add a value for ${knob.label}`}
          data-testid={`exp-add-${axis.knobId}`}
          placeholder="Add a value"
          value={null}
          options={knob.options!.filter(option => !axis.values.includes(option)).map(option => ({
            value: String(option),
            label: typeof option === 'boolean' ? (option ? 'yes' : 'no') : String(option),
          }))}
          onChange={key => addValue(knob.options!.find(option => String(option) === key)!)}
        />
      ) : knob.type === 'boolean' ? (
        <Switch aria-label={`Add ${knob.label}: yes`} data-testid={`exp-add-${axis.knobId}-yes`} checked={false} onClick={() => addValue(true)} />
      ) : (
        <ExperimentNumberAdder knob={knob} onAdd={addValue} />
      )}
    </div>
  );
}

/** Typed number adder honoring the knob's declared type and bounds. */
function ExperimentNumberAdder({knob, onAdd}: {knob: TemplateKnob; onAdd: (value: KnobValue) => void}) {
  const [draft, setDraft] = useState<number | null>(null);
  const add = () => {
    if (draft === null) return;
    onAdd(knob.type === 'integer' ? Math.trunc(draft) : draft);
    setDraft(null);
  };
  return (
    <span className="exp-number-adder">
      <InputNumber
        aria-label={`Add a value for ${knob.label}`}
        data-testid={`exp-add-${knob.id}`}
        min={knob.min}
        max={knob.max}
        precision={knob.type === 'integer' ? 0 : undefined}
        step={knob.type === 'integer' ? 1 : 0.1}
        value={draft}
        onChange={next => setDraft(typeof next === 'number' ? next : null)}
        onPressEnter={add}
      />
      <button type="button" className="console-btn" data-testid={`exp-add-${knob.id}-button`} disabled={draft === null} onClick={add}>Add</button>
    </span>
  );
}

function ResultTable({report, apiBase, template}: {report: ExperimentReport; apiBase: string; template?:ScenarioTemplate}) {
  const metricNames = useMemo(() => {
    const names = new Set<string>();
    for (const row of report.table) for (const name of Object.keys(row.metrics)) names.add(name);
    return [...names].sort();
  }, [report.table]);
  const primary=template?template.kpis.filter(name=>metricNames.includes(name)):metricNames.filter(name=>report.table.some(row=>typeof row.metrics[name]?.mean==='number'&&row.metrics[name].mean!==0));
  const more=metricNames.filter(name=>!primary.includes(name));
  const table=(names:string[])=><div className="exp-table-scroll"><table className="exp-table">
        <thead>
          <tr>
            <th scope="col">Configuration</th>
            {names.map(name => <th key={name} scope="col" title={metricHelp(name)}>{report.table.find(row=>row.metrics[name])?.metrics[name].label ?? name}{metricHelp(name)&&<span className="exp-metric-help" aria-label={metricHelp(name)}> ?</span>}</th>)}
          </tr>
        </thead>
        <tbody>
          {report.table.map(row => (
            <tr key={row.config_id} data-testid={`exp-row-${row.config_id}`}>
              <th scope="row">
                <span className="exp-config-id">{`Option ${report.table.findIndex(item=>item.config_id===row.config_id)+1}`}</span>
                <span className="exp-config-knobs">{Object.entries(row.knobs).filter(([key])=>key in report.vary).map(([key, value]) => `${template?.knobs.find(knob=>knob.id===key)?.label??key}: ${typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)}`).join(', ') || 'defaults'}</span>
              </th>
              {names.map(name => {
                const cell = row.metrics[name];
                if (!cell) return <td key={name} className="exp-cell" data-testid={`exp-cell-${row.config_id}-${name}`}>—</td>;
                return (
                  <td key={name} className={`exp-cell${cell.best ? ' is-best' : ''}`} data-testid={`exp-cell-${row.config_id}-${name}`} title={`mean of ${cell.n} observed run${cell.n === 1 ? '' : 's'}${cell.std === null ? '' : ', sample std'}`}>
                    {formatMetric(cell.mean, cell.std, cell.unit)}
                    <span className="exp-cell-n"> n={cell.n}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table></div>;
  return <div className="exp-results" data-testid="experiment-results">
      {table(primary)}
      {more.length>0&&<details className="exp-more"><summary>More metrics ({more.length})</summary>{table(more)}</details>}
      <p className="exp-results-note">Highlighted values are best for that measure; ties are highlighted too. Values show the average ± variation across repeats. “—” means it was not measured.</p>
      {report.runs.length > 0 && (
        <section aria-label="Individual runs" className="exp-runs">
          <h3>Runs behind these numbers</h3>
          <ul className="exp-run-list">
            {report.runs.map(run => (
              <li key={`${run.config_id}-seed-${run.seed}`} data-testid={`exp-run-${run.config_id}-seed-${run.seed}`}>
                <span className="exp-run-label">Option {report.table.findIndex(row=>row.config_id===run.config_id)+1} · repeat {run.seed+1}</span>
                {run.status && <span className={`exp-run-status is-${run.status}`}>{run.status}</span>}
                {run.run_id
                  ? <a href={experimentRunUrl(run.run_id, apiBase, report.template_id)} data-testid={`exp-open-run-${run.config_id}-seed-${run.seed}`}>Open replay</a>
                  : <span className="exp-run-error" title={run.error ?? undefined}>{run.error ?? 'no run was produced'}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>;
}

/** Guided experiment builder and live monitor for one deployment. */
export default function ExperimentPanel({base, templates, onClose}: {base: string; templates: ScenarioTemplate[]; onClose?: () => void}) {
  const api = useMemo(() => new ExperimentApi(base), [base]);
  const [plan, setPlan] = useState<PlanState>(() => ({
    templateId: templates[0]?.id ?? '',
    axes: [],
    seeds: 3,
    jobs: 1,
    horizon: '',
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [report, setReport] = useState<ExperimentReport>();
  const pollRef = useRef<number>();
  const attemptRef = useRef(0);

  const template = templates.find(candidate => candidate.id === plan.templateId);

  // Stop polling when the panel unmounts or when a finished state is reached.
  useEffect(() => () => { if (pollRef.current !== undefined) window.clearInterval(pollRef.current); }, []);

  const stopPolling = () => {
    if (pollRef.current !== undefined) { window.clearInterval(pollRef.current); pollRef.current = undefined; }
  };

  const startPolling = (experimentId: string) => {
    stopPolling();
    let inFlight = false;
    pollRef.current = window.setInterval(() => {
      if (inFlight) return; // Never overlap the previous GET.
      inFlight = true;
      const attempt = attemptRef.current;
      api.get(experimentId)
        .then(next => {
          if (attemptRef.current !== attempt) return;
          setReport(next);
          setError('');
          if (experimentIsTerminal(next.status)) stopPolling();
        })
        .catch(problem => {
          if (attemptRef.current !== attempt) return;
          setError(String(problem instanceof Error ? problem.message : problem));
          stopPolling();
        })
        .finally(() => { inFlight = false; });
    }, 2000);
  };

  const start = async () => {
    if (!template || plan.seeds===null || plan.jobs===null) return;
    setBusy(true);
    setError('');
    setReport(undefined);
    const attempt = ++attemptRef.current;
    try {
      const vary: ExperimentVary = Object.fromEntries(plan.axes.map(axis => [axis.knobId, axis.values]));
      const body: ExperimentRequest = {
        vary,
        seeds: plan.seeds,
        jobs: plan.jobs,
        ...(plan.horizon.trim() ? {horizon_s: Number(plan.horizon)} : {}),
      };
      const created = await api.create(template.id, body);
      if (attemptRef.current !== attempt) return;
      setReport(created);
      if (experimentIsTerminal(created.status)) return;
      startPolling(created.id);
    } catch (problem) {
      if (attemptRef.current === attempt) setError(String(problem instanceof Error ? problem.message : problem));
    } finally {
      setBusy(false);
    }
  };

  const toggleAxis = (knob: TemplateKnob) => {
    setPlan(current => {
      const axes = current.axes.some(axis => axis.knobId === knob.id)
        ? current.axes.filter(axis => axis.knobId !== knob.id)
        : current.axes.length >= MAX_AXES ? current.axes : [...current.axes, {knobId: knob.id, values: []}];
      return {...current, axes};
    });
  };

  const configurations = plan.axes.reduce((total, axis) => total * Math.max(1, axis.values.length), 1);
  const seedsValid = plan.seeds!==null && Number.isSafeInteger(plan.seeds) && plan.seeds > 0;
  const jobsValid = plan.jobs!==null && Number.isSafeInteger(plan.jobs) && plan.jobs > 0;
  const horizonValid = plan.horizon.trim() === '' || (Number(plan.horizon) > 0 && Number.isFinite(Number(plan.horizon)));
  const ready = !!template && configurations >= 2 && plan.axes.every(axis => axis.values.length > 0) && seedsValid && jobsValid && horizonValid;
  const running = report !== undefined && !experimentIsTerminal(report.status);
  const progress = report && report.total > 0 ? Math.round(((report.completed + report.failed) / report.total) * 100) : 0;

  return (
    <section className="exp-panel" data-testid="experiment-panel" aria-label="Run an experiment">
      <header className="exp-panel-header">
        <div>
          <h2>{report?report.template_title:"Run an experiment"}</h2>
          <p className="exp-panel-sub">Vary one or two settings across a few values and compare the averaged results, repetitions included.</p>
        </div>
        {onClose && <button type="button" className="console-btn" data-testid="experiment-close" onClick={onClose}>Close</button>}
      </header>

      {templates.length === 0 ? (
        <PageState kind="empty" title="No templates to experiment with" description="Experiments need a published scenario template. Publish one first, then come back." />
      ) : (
        <details className="exp-plan" open={!report}><summary>Experiment settings{report?` · ${report.total} runs`:""}</summary>
          <div className="exp-step">
            <h3>1 · Scenario</h3>
            <Select
              aria-label="Scenario template"
              data-testid="experiment-template"
              value={plan.templateId || undefined}
              placeholder="Choose a scenario"
              options={templates.map(candidate => ({value: candidate.id, label: candidate.title}))}
              onChange={value => setPlan(current => ({...current, templateId: String(value), axes: []}))}
            />
            {template && (
              <p className="exp-template-meta">
                <CategoryIcon category={template.category} size={13} /> {categoryMetadata(template.category).label} · {template.summary}
              </p>
            )}
          </div>

          <div className="exp-step">
            <h3>2 · Settings to vary</h3>
            {!template?.knobs.length && template && <p>This scenario has no adjustable settings to vary.</p>}
            <div className="exp-knob-picker" role="group" aria-label="Choose settings to vary">
              {template?.knobs.map(knob => (
                <label key={knob.id} className={`exp-knob-toggle${plan.axes.some(axis => axis.knobId === knob.id) ? ' is-active' : ''}`}>
                  <input
                    type="checkbox"
                    data-testid={`exp-knob-${knob.id}`}
                    checked={plan.axes.some(axis => axis.knobId === knob.id)}
                    disabled={plan.axes.length >= MAX_AXES && !plan.axes.some(axis => axis.knobId === knob.id)}
                    onChange={() => toggleAxis(knob)}
                  />
                  {knob.label}
                </label>
              ))}
            </div>
            {plan.axes.map(axis => (
              <AxisValuesEditor
                key={axis.knobId}
                axis={axis}
                knob={template!.knobs.find(knob => knob.id === axis.knobId)!}
                onChange={values => setPlan(current => ({
                  ...current,
                  axes: current.axes.map(item => item.knobId === axis.knobId ? {...item, values} : item),
                }))}
              />
            ))}
          </div>

          <div className="exp-step">
            <h3>3 · Repetitions and horizon</h3>
            <div className="exp-run-options">
              <label className="exp-option">Repeats per configuration (same seeds each time)
                <InputNumber aria-label="Repeats per configuration" data-testid="experiment-seeds" min={1} precision={0} value={plan.seeds} onChange={next => setPlan(current => ({...current, seeds: typeof next === 'number' ? next : null}))} />
              </label>
              <label className="exp-option">Parallel runs
                <InputNumber aria-label="Parallel runs" data-testid="experiment-jobs" min={1} precision={0} value={plan.jobs} onChange={next => setPlan(current => ({...current, jobs: typeof next === 'number' ? next : null}))} />
              </label>
              <label className="exp-option">Horizon (seconds, blank for template default)
                <InputNumber aria-label="Horizon in seconds" data-testid="experiment-horizon" min={0.1} value={plan.horizon.trim() === '' ? null : Number(plan.horizon)} onChange={next => setPlan(current => ({...current, horizon: next === null ? '' : String(next)}))} />
              </label>
            </div>
            <p className="exp-preview" data-testid="experiment-preview">
              {ready&&plan.seeds!==null
                ? `Will start ${configurations} configuration${configurations === 1 ? '' : 's'} × ${plan.seeds} repeat${plan.seeds === 1 ? '' : 's'} = ${configurations * plan.seeds} runs.`
                : 'Choose one or two settings and at least two total configurations to compare.'}
            </p>
          </div>

          <div className="exp-panel-actions">
            <button
              type="button"
              className="console-btn console-btn-primary"
              data-testid="experiment-start"
              disabled={busy || running || !ready}
              onClick={() => void start()}
            >
              {busy ? 'Starting…' : running ? 'Experiment in progress…' : 'Start experiment'}
            </button>
          </div>
        </details>
      )}

      {error && (
        <div className="template-error-card" role="alert" data-testid="experiment-error">
          <strong>The experiment could not be {report ? 'followed' : 'started'}.</strong>
          <p>{report ? 'Progress updates stopped arriving. See the request details or reopen the experiment to check its status.' : 'The service could not start this experiment. Check its settings and the request details below.'}</p>
          <Details title="Experiment request"><p>{error}</p></Details>
        </div>
      )}

      {report && (
        <div className="exp-report" data-testid="experiment-report">
          <div className="exp-progress-row" data-status={report.status}>
            <span className="exp-progress-label">
              {report.status === 'completed' && `Completed — ${report.completed} of ${report.total} runs succeeded${report.failed ? `, ${report.failed} failed` : ''}.`}
              {report.status === 'faulted' && `The experiment stopped early: ${report.error ?? 'unknown error'}.`}
              {report.status === 'interrupted' && 'The experiment was interrupted before every run finished.'}
              {running && `Running — ${report.completed + report.failed} of ${report.total} runs finished.`}
            </span>
            <span className="exp-progress-count" data-testid="experiment-progress">{report.completed + report.failed}/{report.total}</span>
            <progress max={100} value={progress} aria-label="Experiment progress" />
          </div>
          {report.status !== 'faulted' || report.table.some(row => Object.keys(row.metrics).length > 0) ? (
            report.table.some(row => Object.keys(row.metrics).length > 0)
              ? <ResultTable report={report} apiBase={base} template={templates.find(item=>item.id===report.template_id)} />
              : <PageState kind="loading" title="Waiting for the first finished runs" description="Averages appear here as soon as the first runs complete." />
          ) : (
            <PageState kind="error" title="No results were produced" description={report.error ?? 'The experiment stopped before any run finished.'} />
          )}
          <p className="exp-report-meta">Horizon: {horizonText(report)} · seeds 0–{report.seeds - 1} reused for every configuration.</p>
        </div>
      )}
    </section>
  );
}
