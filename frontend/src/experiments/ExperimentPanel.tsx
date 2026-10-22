import { useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { localized } from '../i18n/translate';
import { knobOptionLabel } from '../newcomers/presentation';
import type { KnobValue, ScenarioTemplate, TemplateKnob } from '../newcomers/api';
import { ApiError, type RunRecord, type WorkbenchApi } from '../workbench/api';
import { ExperimentApi, type ExperimentPlan, type ExperimentReport } from './api';
import { ExperimentResults } from './ExperimentResults';
import {
  MAX_AXIS_VALUES,
  MAX_EXPERIMENT_JOBS,
  MAX_EXPERIMENT_RUNS,
  MAX_EXPERIMENT_SEEDS,
  blockedAxis,
  configurationCount,
  horizonSeconds,
  parseAxisValues,
  positiveInteger,
  runCount,
  valueKey,
  type AxisProblem,
} from './plan';

interface AxisDraft {
  enabled: boolean;
  values: KnobValue[];
  text: string;
  problem?: AxisProblem;
}

type Phase = 'draft' | 'review' | 'submitting' | 'ambiguous';

const POLL_MS = 2500;

function emptyAxis(): AxisDraft {
  return { enabled: false, values: [], text: '' };
}

/** All typed values offered for a choice/boolean axis. */
function axisOptions(knob: TemplateKnob): KnobValue[] {
  return knob.type === 'boolean' ? [true, false] : (knob.options ?? []);
}

/**
 * The bounded experiment journey inside Records. It only knows the two routes
 * that exist: POST a template experiment and GET an experiment by id. There is
 * no list, resume or cancel call, and a failed POST is never retried here.
 */
export function ExperimentPanel({ api, experimentId, onExperimentId, onOpen, onCompare }: {
  api: WorkbenchApi;
  experimentId?: string;
  onExperimentId: (id: string | undefined) => void;
  onOpen: (run: RunRecord) => void;
  onCompare: (ids: string[]) => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const client = useMemo(() => new ExperimentApi(api), [api]);

  const [templates, setTemplates] = useState<ScenarioTemplate[]>();
  const [templateId, setTemplateId] = useState<string>();
  const [templateError, setTemplateError] = useState<string>();
  const [templateAttempt, setTemplateAttempt] = useState(0);
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [axes, setAxes] = useState<Record<string, AxisDraft>>({});
  const [seedsText, setSeedsText] = useState('3');
  const [jobsText, setJobsText] = useState('1');
  const [horizonText, setHorizonText] = useState('');
  const [openId, setOpenId] = useState('');
  const [phase, setPhase] = useState<Phase>('draft');
  const [submitError, setSubmitError] = useState<string>();
  const [report, setReport] = useState<ExperimentReport>();
  const [loadError, setLoadError] = useState<string>();
  const [reloadKey, setReloadKey] = useState(0);
  const reportIdRef = useRef<string>();
  const submitting = useRef(false);

  const template = templates?.find(row => row.id === templateId);

  // Templates are only needed for a new plan; a bookmarked id is a GET.
  useEffect(() => {
    if (experimentId || templates !== undefined) return;
    let live = true;
    const abort = new AbortController();
    setLoadingTemplates(true);
    setTemplateError(undefined);
    api.templates(abort.signal).then(rows => {
      if (!live) return;
      setTemplates(rows);
      setTemplateId(current => current ?? rows[0]?.id);
      setLoadingTemplates(false);
    }).catch(problem => {
      if (!live) return;
      setTemplateError(String(problem));
      setLoadingTemplates(false);
    });
    return () => { live = false; abort.abort(); };
  }, [api, experimentId, templates, templateAttempt]);

  useEffect(() => {
    setAxes({});
    setHorizonText('');
  }, [templateId]);

  useEffect(() => {
    if (reportIdRef.current === experimentId) return;
    reportIdRef.current = experimentId;
    setReport(undefined);
    setLoadError(undefined);
    setReloadKey(0);
  }, [experimentId]);

  // Polling is a safe GET. It never re-submits a POST.
  useEffect(() => {
    if (!experimentId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    const load = async () => {
      try {
        const next = await client.get(experimentId, abort.signal);
        if (!live) return;
        setReport(next);
        setLoadError(undefined);
        if (next.status === 'created' || next.status === 'running') timer = setTimeout(load, POLL_MS);
      } catch (problem) {
        if (!live) return;
        setLoadError(String(problem));
        // A definite 4xx will not heal by polling; 5xx/network errors may.
        if (problem instanceof ApiError && problem.status >= 400 && problem.status < 500) return;
        timer = setTimeout(load, POLL_MS);
      }
    };
    void load();
    return () => {
      live = false;
      abort.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [client, experimentId, reloadKey]);

  const hasHorizon = horizonText.trim() !== '';
  const vary = useMemo(() => {
    const next: Record<string, KnobValue[]> = {};
    if (!template) return next;
    for (const knob of template.knobs) {
      const draft = axes[knob.id];
      if (!draft?.enabled || blockedAxis(knob, hasHorizon)) continue;
      next[knob.id] = draft.values;
    }
    return next;
  }, [template, axes, hasHorizon]);

  const seeds = positiveInteger(seedsText, MAX_EXPERIMENT_SEEDS);
  const jobs = positiveInteger(jobsText, MAX_EXPERIMENT_JOBS);
  const horizon = horizonSeconds(horizonText);
  const configurations = configurationCount(vary);
  const total = seeds === undefined ? undefined : runCount(vary, seeds);
  const axisInvalid = !!template?.knobs.some(knob => {
    const draft = axes[knob.id];
    return !!draft?.enabled && (!draft.values.length || blockedAxis(knob, hasHorizon) !== undefined);
  });
  const overLimit = total !== undefined && total > MAX_EXPERIMENT_RUNS;

  const planProblem = !template
    ? L('Choose a template.', '请选择模板。')
    : seeds === undefined
      ? L(`Seeds must be an integer from 1 to ${MAX_EXPERIMENT_SEEDS}.`, `种子数必须是 1 到 ${MAX_EXPERIMENT_SEEDS} 的整数。`)
      : jobs === undefined
        ? L(`Worker jobs must be an integer from 1 to ${MAX_EXPERIMENT_JOBS}.`, `工作进程数必须是 1 到 ${MAX_EXPERIMENT_JOBS} 的整数。`)
        : horizon === undefined
          ? L('Horizon must be a positive number of seconds, or blank for the template default.', '时长必须是正数秒，或留空使用模板默认值。')
          : axisInvalid
            ? L('Every enabled axis needs at least one valid value.', '每个启用的变化轴至少需要一个有效值。')
            : total === undefined
              ? L('The run count could not be computed.', '无法计算运行数。')
              : overLimit
                ? L(`This plan creates ${total} runs; the browser limit is ${MAX_EXPERIMENT_RUNS}. Reduce axes or seeds, or use the CLI.`, `此计划将创建 ${total} 次运行；浏览器上限为 ${MAX_EXPERIMENT_RUNS}。请减少变化轴或种子，或使用命令行。`)
                : undefined;

  const plan = useMemo<ExperimentPlan | undefined>(() => {
    if (!template || seeds === undefined || jobs === undefined || horizon === undefined || total === undefined || overLimit || axisInvalid) return undefined;
    return { vary, seeds, jobs, ...(horizon === null ? {} : { horizon_s: horizon }) };
  }, [template, vary, seeds, jobs, horizon, total, overLimit, axisInvalid]);

  const axisProblemText = (problem: AxisProblem): string => {
    switch (problem.code) {
      case 'empty': return L('Enter at least one value.', '请至少输入一个值。');
      case 'too-many': return L(`Use at most ${MAX_AXIS_VALUES} values.`, `最多使用 ${MAX_AXIS_VALUES} 个值。`);
      case 'not-integer': return L(`"${problem.token ?? ''}" is not an integer.`, `“${problem.token ?? ''}”不是整数。`);
      case 'not-finite': return L(`"${problem.token ?? ''}" is not a finite number.`, `“${problem.token ?? ''}”不是有限数值。`);
      case 'out-of-range': return L(`"${problem.token ?? ''}" is outside this setting's bounds.`, `“${problem.token ?? ''}”超出此设置的范围。`);
    }
  };

  const setAxisEnabled = (knob: TemplateKnob, enabled: boolean) => {
    setAxes(previous => {
      const current = previous[knob.id] ?? emptyAxis();
      if (!enabled) return { ...previous, [knob.id]: { ...current, enabled: false } };
      if (knob.type === 'choice' || knob.type === 'boolean') {
        return { ...previous, [knob.id]: { enabled: true, values: axisOptions(knob), text: '' } };
      }
      const text = String(knob.default);
      const parsed = parseAxisValues(knob, text);
      return {
        ...previous,
        [knob.id]: {
          enabled: true,
          values: 'values' in parsed ? parsed.values : [],
          text,
          ...('problem' in parsed ? { problem: parsed.problem } : {}),
        },
      };
    });
  };

  const setChoice = (knob: TemplateKnob, option: KnobValue, checked: boolean) => {
    setAxes(previous => {
      const current = previous[knob.id] ?? emptyAxis();
      const options = axisOptions(knob);
      const selected = new Set(current.values.map(valueKey));
      if (checked) selected.add(valueKey(option)); else selected.delete(valueKey(option));
      return { ...previous, [knob.id]: { ...current, values: options.filter(item => selected.has(valueKey(item))) } };
    });
  };

  const setNumeric = (knob: TemplateKnob, text: string) => {
    const parsed = parseAxisValues(knob, text);
    setAxes(previous => ({
      ...previous,
      [knob.id]: {
        enabled: true,
        text,
        values: 'values' in parsed ? parsed.values : [],
        ...('problem' in parsed ? { problem: parsed.problem } : {}),
      },
    }));
  };

  const setHorizon = (text: string) => {
    setHorizonText(text);
    if (!text.trim()) return;
    setAxes(previous => {
      let changed = false;
      const next = { ...previous };
      for (const knob of template?.knobs ?? []) {
        const current = next[knob.id];
        if (knob.pointer === '/run/until_ns' && current?.enabled) {
          next[knob.id] = { ...current, enabled: false };
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  };

  const review = () => {
    if (!plan || planProblem) return;
    setSubmitError(undefined);
    setPhase('review');
  };

  const submit = async () => {
    if (!template || !plan || phase !== 'review' || submitting.current) return;
    submitting.current = true;
    setSubmitError(undefined);
    setPhase('submitting');
    let created: ExperimentReport;
    try {
      // One explicit POST. A lost response is reported as unknown below; it is
      // never retried automatically because the server may have accepted it.
      created = await client.create(template.id, plan);
    } catch (problem) {
      if (problem instanceof ApiError && problem.status >= 400 && problem.status < 500) {
        // A definite rejection created nothing; let the user fix the plan.
        setSubmitError(problem.detail);
        setPhase('draft');
      } else {
        // A network error, 5xx or undecodable 201 may still have created the
        // experiment. Keep the message honest and never retry automatically.
        setSubmitError(String(problem));
        setPhase('ambiguous');
      }
      submitting.current = false;
      return;
    }
    reportIdRef.current = created.id;
    setReport(created);
    setPhase('draft');
    submitting.current = false;
    onExperimentId(created.id);
  };

  const newExperiment = () => {
    setReport(undefined);
    setLoadError(undefined);
    setSubmitError(undefined);
    setPhase('draft');
    onExperimentId(undefined);
  };

  const openById = () => {
    const id = openId.trim();
    if (!id) return;
    setSubmitError(undefined);
    setPhase('draft');
    setReport(undefined);
    setLoadError(undefined);
    onExperimentId(id);
  };

  const openRun = (runId: string, status: string) => {
    if (!report) return;
    // Workbench's onOpen consumes id, status and studio_workspace. The
    // experiment report does not expose each run's until_ns, so no value is
    // asserted for it here.
    onOpen({
      id: runId,
      status,
      scenario: report.template_id,
      until_ns: report.horizon_s === null ? '' : String(Math.round(report.horizon_s * 1e9)),
      template_id: report.template_id,
      template_title: report.template_title,
      ...(report.template_title_zh ? { template_title_zh: report.template_title_zh } : {}),
    });
  };

  // The POST response is displayed even before the parent writes the URL; the
  // experimentId prop then takes over for refresh and polling.
  const shownId = experimentId ?? report?.id;
  const shownReport = report && report.id === shownId ? report : undefined;
  if (shownId) {
    return <section className="wb-experiment" data-testid="experiment-panel">
      <div className="wb-panel-head" style={{ padding: '0 0 8px' }}>
        <h3>{L('Experiment', '实验')}</h3>
        <code className="wb-tag info" data-testid="experiment-id">{shownId}</code>
      </div>
      {loadError && <div className="wb-issues" role="alert">
        {loadError}
        {shownReport && <span> {L('Showing the last successful report; Refresh retries the read.', '显示上次成功读取的报告；刷新会重试读取。')}</span>}
      </div>}
      {!shownReport && !loadError && <div role="status">{L('Loading the experiment…', '正在加载实验…')}</div>}
      {shownReport && <ExperimentResults report={shownReport} onOpenRun={openRun} onCompare={onCompare} />}
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button className="wb-btn small" onClick={newExperiment} data-testid="experiment-new">{L('New experiment', '新建实验')}</button>
        <button className="wb-btn small" onClick={() => setReloadKey(value => value + 1)}>{L('Refresh', '刷新')}</button>
      </div>
    </section>;
  }

  return <section className="wb-experiment" data-testid="experiment-panel">
    <div className="wb-panel-head" style={{ padding: '0 0 8px' }}><h3>{L('Experiments', '实验')}</h3></div>
    <p className="hint">
      {L('Run a template across typed settings and matched seeds. Each created cell is a real headless run; nothing is started until you confirm once.', '按不同设置取值与匹配种子运行模板。每个创建的单元都是真实的无人值守运行；在你确认一次之前不会启动任何内容。')}
    </p>
    {loadingTemplates && <div role="status">{L('Loading templates…', '正在加载模板…')}</div>}
    {templateError && <div className="wb-issues" role="alert">
      {templateError}
      <button className="wb-btn small" style={{ marginLeft: 8 }} onClick={() => { setTemplates(undefined); setTemplateId(undefined); setTemplateAttempt(value => value + 1); }}>{L('Retry', '重试')}</button>
    </div>}
    {templates && !templates.length && <div className="wb-empty">{L('No templates are available on this server.', '此服务器没有可用模板。')}</div>}

    {templates && templates.length > 0 && <div className="wb-field">
      <label htmlFor="experiment-template">{L('Template', '模板')}</label>
      <select id="experiment-template" data-testid="experiment-template" value={templateId ?? ''} onChange={event => { setTemplateId(event.target.value); setAxes({}); setHorizonText(''); }}>
        {templates.map(row => <option key={row.id} value={row.id}>{localized(row, 'title', locale) ?? row.title}</option>)}
      </select>
    </div>}

    {template && <div>
      <div className="wb-field">
        <label htmlFor="experiment-seeds">{L('Seeds', '种子数')}<small>{L(`Matched seeds 0…n-1 (1–${MAX_EXPERIMENT_SEEDS})`, `匹配种子 0…n-1（1–${MAX_EXPERIMENT_SEEDS}）`)}</small></label>
        <input id="experiment-seeds" data-testid="experiment-seeds" type="number" min={1} max={MAX_EXPERIMENT_SEEDS} step={1} value={seedsText} onChange={event => setSeedsText(event.target.value)} aria-invalid={seeds === undefined} />
      </div>
      <div className="wb-field">
        <label htmlFor="experiment-jobs">{L('Worker jobs', '工作进程')}<small>{L(`1–${MAX_EXPERIMENT_JOBS} processes`, `1–${MAX_EXPERIMENT_JOBS} 个进程`)}</small></label>
        <input id="experiment-jobs" data-testid="experiment-jobs" type="number" min={1} max={MAX_EXPERIMENT_JOBS} step={1} value={jobsText} onChange={event => setJobsText(event.target.value)} aria-invalid={jobs === undefined} />
      </div>
      <div className="wb-field">
        <label htmlFor="experiment-horizon">{L('Horizon (seconds, optional)', '时长（秒，可选）')}<small>{L(`Template duration: ${template.duration_s} s`, `模板时长：${template.duration_s} 秒`)}</small></label>
        <input id="experiment-horizon" data-testid="experiment-horizon" type="number" min={0} step="any" placeholder={String(template.duration_s)} value={horizonText} onChange={event => setHorizon(event.target.value)} aria-invalid={horizon === undefined} />
      </div>

      <fieldset style={{ border: 'none', padding: 0, margin: '8px 0', minWidth: 0 }}>
        <legend className="hint">{L('Axes (optional)', '变化轴（可选）')}</legend>
        {template.knobs.map(knob => {
          const draft = axes[knob.id] ?? emptyAxis();
          const blocked = blockedAxis(knob, hasHorizon);
          const label = localized(knob, 'label', locale) ?? knob.label;
          return <div key={knob.id} className="wb-experiment-axis" data-testid={`experiment-axis-${knob.id}`} style={{ padding: '6px 0', borderBottom: '1px dashed var(--wb-line)' }}>
            <div className="wb-card-meta" style={{ alignItems: 'center' }}>
              <label style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                <input type="checkbox" checked={draft.enabled} disabled={!!blocked} onChange={event => setAxisEnabled(knob, event.target.checked)} aria-label={L(`Vary ${label}`, `变化“${label}”`)} />
                <strong>{label}</strong>
              </label>
              <span className="wb-tag">{knob.type}</span>
              <code className="wb-mono">{knob.id}</code>
            </div>
            {blocked === 'seed' && <p className="hint">{L('Matched seeds set this knob; use the seeds field instead.', '匹配种子控制此设置；请改用种子字段。')}</p>}
            {blocked === 'horizon' && <p className="hint">{L('Clear the horizon override to vary this duration knob.', '清除时长覆盖后才能变化此时长设置。')}</p>}
            {draft.enabled && !blocked && <div style={{ marginTop: 4 }}>
              {(knob.type === 'choice' || knob.type === 'boolean') && <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {axisOptions(knob).map(option => {
                  const key = valueKey(option);
                  return <label key={key} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <input type="checkbox" checked={draft.values.some(value => valueKey(value) === key)} onChange={event => setChoice(knob, option, event.target.checked)} />
                    {knobOptionLabel(knob, option, locale)}
                  </label>;
                })}
              </div>}
              {knob.type !== 'choice' && knob.type !== 'boolean' && <div>
                <input
                  type="text"
                  data-testid={`experiment-values-${knob.id}`}
                  aria-label={L(`Values for ${label}`, `“${label}”的取值`)}
                  value={draft.text}
                  onChange={event => setNumeric(knob, event.target.value)}
                  style={{ width: '100%', height: 30, border: '1px solid var(--wb-line)', borderRadius: 7, padding: '0 8px' }}
                />
                <span className="hint">
                  {L('Comma-separated', '逗号分隔')}
                  {knob.min !== undefined || knob.max !== undefined
                    ? ` · ${L('allowed', '允许范围')} ${knob.min ?? '−∞'} … ${knob.max ?? '∞'}`
                    : ''}
                </span>
                {draft.problem && <span className="wb-field-error" role="alert">{axisProblemText(draft.problem)}</span>}
              </div>}
              {!draft.values.length && !draft.problem && <span className="wb-field-error" role="alert">{L('Choose at least one value.', '请至少选择一个值。')}</span>}
            </div>}
          </div>;
        })}
      </fieldset>

      <div data-testid="experiment-preview" aria-live="polite" style={{ padding: '8px 0', borderTop: '1px solid var(--wb-line)' }}>
        <strong>{L('Run preview', '运行数预览')}: </strong>
        <span>{total === undefined ? '—' : L(`${total} runs`, `${total} 次运行`)}</span>
        <div className="hint">
          {configurations === undefined || seeds === undefined
            ? L('Enter valid axes and seeds to preview the factorial product.', '输入有效的变化轴和种子后预览阶乘乘积。')
            : L(`${configurations} configurations × ${seeds} seeds`, `${configurations} 个配置 × ${seeds} 个种子`)}
          {!Object.keys(vary).length && L(' · no axis chosen, so one configuration is repeated with matched seeds', ' · 未选择变化轴，将以匹配种子重复一个配置')}
        </div>
        <div className="hint">{L(`Browser limit: ${MAX_EXPERIMENT_RUNS} runs per experiment (a UI guard, not a server limit).`, `浏览器上限：每个实验 ${MAX_EXPERIMENT_RUNS} 次运行（界面保护，并非服务器限制）。`)}</div>
        {overLimit && <div className="wb-field-error" role="alert">{L(`This plan exceeds the ${MAX_EXPERIMENT_RUNS}-run browser limit.`, `此计划超过 ${MAX_EXPERIMENT_RUNS} 次运行的浏览器上限。`)}</div>}
      </div>

      {phase === 'draft' && <>
        {submitError && <div className="wb-issues" role="alert">{L('The server rejected the experiment', '服务器拒绝了该实验')}: <span className="wb-mono">{submitError}</span></div>}
        {planProblem && <p className="hint" role="status">{planProblem}</p>}
        <button className="wb-btn small primary" disabled={!plan || !!planProblem} onClick={review} data-testid="experiment-review">{L('Review experiment', '检查实验')}</button>
      </>}

      {(phase === 'review' || phase === 'submitting') && <div className="wb-experiment-review" data-testid="experiment-review-panel" style={{ padding: 8, border: '1px solid var(--wb-line)', borderRadius: 9 }}>
        <h4 style={{ margin: '0 0 4px' }}>{L('Review once, then create once', '检查一次，然后创建一次')}</h4>
        <p className="hint">{L(`This will create ${total ?? 0} real headless runs (${configurations ?? 0} configurations × ${seeds ?? 0} seeds).`, `这将创建 ${total ?? 0} 次真实的无人值守运行（${configurations ?? 0} 个配置 × ${seeds ?? 0} 个种子）。`)}</p>
        <div className="wb-card-meta">
          {Object.entries(vary).map(([name, values]) => <span key={name} className="wb-tag">
            {localized(template.knobs.find(knob => knob.id === name), 'label', locale) ?? name}={values.join(', ')}
          </span>)}
          <span className="wb-tag">{L('jobs', '进程')} {jobs}</span>
          <span className="wb-tag">{L('horizon', '时长')} {horizon === null ? L('template default', '模板默认') : `${horizon} s`}</span>
        </div>
        {phase === 'submitting' && <div role="status">{L('Creating the experiment…', '正在创建实验…')}</div>}
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          <button className="wb-btn small" disabled={phase === 'submitting'} onClick={() => setPhase('draft')} data-testid="experiment-back">{L('Back', '返回')}</button>
          <button className="wb-btn small primary" disabled={phase === 'submitting' || !plan} onClick={() => void submit()} data-testid="experiment-confirm">
            {phase === 'submitting' ? L('Creating…', '创建中…') : L(`Create ${total ?? 0} runs`, `创建 ${total ?? 0} 次运行`)}
          </button>
        </div>
      </div>}

      {phase === 'ambiguous' && <div className="wb-issues" role="alert" data-testid="experiment-ambiguous">
        <strong>{L('submission status unknown; check Records', '提交状态未知；请检查“记录”')}</strong>
        <p>{L('The experiment may already have started. This panel will not retry the request automatically.', '实验可能已经开始。本面板不会自动重试该请求。')}</p>
        {submitError && <p className="wb-mono">{submitError}</p>}
        <button className="wb-btn small" onClick={() => { setPhase('draft'); setSubmitError(undefined); }}>{L('Start over', '重新开始')}</button>
      </div>}
    </div>}

    <div className="wb-field" style={{ marginTop: 8 }}>
      <label htmlFor="experiment-open-id">{L('Open an experiment id', '打开实验 ID')}<small>{L('GET /v1/experiments/{id}', 'GET /v1/experiments/{id}')}</small></label>
      <div style={{ display: 'flex', gap: 6 }}>
        <input id="experiment-open-id" data-testid="experiment-open-id" value={openId} onChange={event => setOpenId(event.target.value)} placeholder="exp-…" />
        <button className="wb-btn small" disabled={!openId.trim()} onClick={openById}>{L('Open', '打开')}</button>
      </div>
    </div>
  </section>;
}
