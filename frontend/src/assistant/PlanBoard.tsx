import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { questionOf, type ApplyRecord, type AssistantApi, type PlanVersion, type Story, type StoryItem } from './api';
import { Icon } from './icons';
import { InteractionDiagram } from './InteractionDiagram';
import { markerIds } from './overlay';
import { DiffList, PlanChanges } from './PlanChanges';
import { bi, clock } from './present';
import { RunStory } from './RunStory';
import { SceneCards } from './SceneCards';
import type { AssistantSession } from './useAssistant';
import { draftMessage } from './AssistantPanel';

export type Stage = 'proposed' | 'configured' | 'running';
type Tab = 'scene' | 'interactions' | 'changes' | 'story';

/** The plan, the configured scene and the run, in one sheet whose state is never ambiguous. */
export function PlanBoard({ api, assistant, stage, runId, story, storyError, startNs, terminal, initializing, canInitialize, onInitialize, onApplied, onHover, onStep, onContract, onSeek, viewing, onViewVersion, onClose }: {
  api: AssistantApi; assistant: AssistantSession; stage: Stage; runId?: string;
  story?: Story; storyError?: string; startNs?: string; terminal: boolean;
  initializing: boolean; canInitialize: boolean; onInitialize: () => void; onApplied: (record: ApplyRecord) => void;
  onHover: (ids: string[]) => void; onStep: (ids: string[]) => void; onContract: (type: string) => void; onSeek: (cut: number) => void;
  viewing?: number; onViewVersion: (version: number | undefined) => void; onClose: () => void;
}) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const { session, plan: latest, busy, applied } = assistant;
  const [older, setOlder] = useState<PlanVersion>();
  const [tab, setTab] = useState<Tab>(stage === 'running' ? 'story' : 'scene');
  const [applying, setApplying] = useState(false), [applyError, setApplyError] = useState<string>();
  const scroller = useRef<HTMLDivElement>(null), tabsRef = useRef<HTMLElement>(null);
  // Switching tabs while the tab bar is pinned starts the new tab at its top.
  useEffect(() => { const box = scroller.current, bar = tabsRef.current; if (box && bar && box.scrollTop > bar.offsetTop) box.scrollTop = bar.offsetTop; }, [tab]);
  useEffect(() => { if (stage === 'running') setTab('story'); else if (tab === 'story') setTab('scene'); }, [stage]);
  useEffect(() => {
    if (viewing === undefined || viewing === latest?.version || !session) { setOlder(undefined); return; }
    const abort = new AbortController();
    void api.plan(session.id, viewing, abort.signal).then(setOlder).catch(() => undefined);
    return () => abort.abort();
  }, [api, session?.id, viewing, latest?.version]);
  const view = (version: number | undefined) => onViewVersion(version === latest?.version ? undefined : version);

  const plan = older ?? latest;
  if (!session || !plan) return null;
  const isOld = !!older && older.version !== latest?.version;
  const alternatives = session.alternatives ?? [];
  const isAlternative = alternatives.includes(plan.version) && plan.version !== latest?.version;
  const scene = plan.scene;
  const hover = (id: string | undefined) => onHover(id ? markerIds(scene, id) : []);
  const valid = plan.validation.state === 'valid' && plan.compile.ok, applyDone = !!applied && applied.plan_version === plan.version;
  const confirm = async () => {
    setApplying(true); setApplyError(undefined);
    try { onApplied(await assistant.apply(plan.version)); } catch (problem) { setApplyError(problem instanceof Error ? problem.message : String(problem)); } finally { setApplying(false); }
  };
  const ask = (text: string) => void assistant.send(text).catch(() => undefined);
  const askWhy = (item: StoryItem) => void assistant.send(L(`Why didn't this happen: "${item.text}"?`, `为什么这一步没有发生：“${item.text_zh ?? item.text}”？`), { run_id: runId, locale }).catch(() => undefined);
  const shownStage: Stage = isOld ? 'proposed' : stage;
  const rows = session.plan_versions;

  return <aside className={`as-board wb-panel wb-glass is-${shownStage}${isOld ? ' is-old' : ''}`} data-testid="as-plan-board" data-stage={shownStage} aria-label={L('Plan board', '方案面板')}>
    <header className="as-board-head">
      <ol className="as-rail" aria-label={L('Where you are', '当前阶段')}>
        {(['proposed', 'configured', 'running'] as Stage[]).map((step, index, all) => {
          const at = all.indexOf(shownStage);
          return <li key={step} className={index === at ? 'is-on' : index < at ? 'is-past' : ''} aria-current={index === at ? 'step' : undefined}>
            <i />{step === 'proposed' ? L('Proposed', '提议') : step === 'configured' ? L('Configured', '已配置') : L('Running', '运行')}
          </li>;
        })}
      </ol>
      <button className="wb-x" onClick={onClose} aria-label={L('Close plan board', '关闭方案面板')}>✕</button>
    </header>

    <div className="as-board-scroll" ref={scroller}>
    <div className="as-board-title as-rise">
      <span className={`as-badge is-${shownStage}`} data-testid="as-plan-state">
        {shownStage === 'proposed' ? <>{isOld ? L(`Earlier version · v${plan.version}`, `早期版本 · v${plan.version}`) : L(`Proposed plan · v${plan.version}`, `提议方案 · v${plan.version}`)}<em>{L('not applied', '未应用')}</em></>
          : shownStage === 'configured' ? <>{L(`Configured · version ${applied?.version ?? ''}`, `已配置 · 版本 ${applied?.version ?? ''}`)}<em>{L('not running', '未运行')}</em></>
          : <>{terminal ? L('Run finished', '运行结束') : L('Running', '运行中')}<em className="wb-mono">{runId}</em></>}
      </span>
      <h2>{plan.title}</h2>
      {plan.summary && <p>{plan.summary}</p>}
      {rows.length > 1 && <div className="as-versions" role="tablist" aria-label={L('Plan versions', '方案版本')}>
        {rows.map(row => <button key={row.version} role="tab" aria-selected={row.version === plan.version} title={row.note} onClick={() => view(row.version)} data-testid={`as-version-${row.version}`}
          className={`${row.version === latest?.version ? 'is-latest' : ''} is-${row.validation_state}${alternatives.includes(row.version) ? ' is-alt' : ''}`}>
          {row.variant ? <b>{row.variant}</b> : null}v{row.version}
        </button>)}
      </div>}
      {isOld && <div className="as-old-note">
        <span>{plan.note || L('An earlier version, shown for comparison.', '早期版本，仅供对比。')}</span>
        {isAlternative ? <button className="wb-btn small primary" onClick={() => void assistant.choose(plan.version)} data-testid="as-choose">{L('Choose this option', '选择此方案')}</button>
          : <button className="wb-btn small" onClick={() => ask(L(`Go back to version ${plan.version}, keeping everything else the same.`, `回到版本 ${plan.version}，其余保持不变。`))} disabled={busy}>{L('Go back to this version', '回到此版本')}</button>}
        <button className="wb-btn small ghost" onClick={() => view(undefined)}>{L('Show latest', '查看最新')}</button>
      </div>}
    </div>

    {!isOld && stage === 'proposed' && (plan.open_questions.length > 0 || plan.assumptions.length > 0) && <div className="as-asks as-rise">
      {plan.open_questions.map(questionOf).map(({ question, options }) => <div key={question} className="as-question" data-testid="as-question">
        <span className="as-question-mark">?</span>
        <div><p>{question}</p>
          <div className="as-question-options">
            {options.map(option => <button key={option} className="wb-btn small" disabled={busy} onClick={() => ask(option)}>{option}</button>)}
            <button className="wb-btn small ghost" disabled={busy} onClick={() => ask(L(`Use a sensible default for: ${question}`, `这个问题用合理的默认值：${question}`))}>{L('Use a sensible default', '用合理默认值')}</button>
          </div>
        </div>
      </div>)}
      {plan.assumptions.length > 0 && <details className="as-assumptions" open={plan.version === 1 || plan.open_questions.length === 0}>
        <summary>{L(`Assumptions (${plan.assumptions.length})`, `假设（${plan.assumptions.length}）`)}<small>{L('filled in for you — change any of them', '已为你补全，可随时修改')}</small></summary>
        <ul>{plan.assumptions.map(text => <li key={text}>
          <span>{text}</span>
          <button className="as-link" onClick={() => draftMessage(L(`Instead of "${text}", `, `不要“${text}”，改为`))}>{L('Change', '修改')}</button>
        </li>)}</ul>
      </details>}
    </div>}

    {stage === 'configured' && !isOld && applied && <ApplySummary record={applied} onHover={hover} />}

    <nav className="as-tabs" role="tablist" ref={tabsRef}>
      {stage === 'running' && !isOld && <button role="tab" aria-selected={tab === 'story'} onClick={() => setTab('story')} data-testid="as-tab-story">{L('Story', '经过')}</button>}
      <button role="tab" aria-selected={tab === 'scene'} onClick={() => setTab('scene')} data-testid="as-tab-scene">{L('Scene', '场景')}</button>
      <button role="tab" aria-selected={tab === 'interactions'} onClick={() => setTab('interactions')} disabled={!plan.interactions?.steps.length} data-testid="as-tab-interactions">{L('Interactions', '交互')}{plan.interactions?.steps.length ? <span>{plan.interactions.steps.length}</span> : null}</button>
      <button role="tab" aria-selected={tab === 'changes'} onClick={() => setTab('changes')} data-testid="as-tab-changes">{L('Changes', '变更')}{plan.diff_previous ? <span>{plan.diff_previous.added.length + plan.diff_previous.changed.length + plan.diff_previous.removed.length}</span> : null}</button>
    </nav>

    <div className="as-board-body" key={`${plan.version}-${tab}`}>
      {tab === 'scene' && (scene ? <SceneCards scene={scene} diff={plan.diff_previous ?? plan.diff_base} onHover={hover} onContract={onContract} busy={busy || isOld} onMode={ask} /> : <p className="as-quiet">{L('The plan could not be compiled into a scene yet.', '方案尚未编译成场景。')}</p>)}
      {tab === 'interactions' && plan.interactions && <InteractionDiagram interactions={plan.interactions} onStep={lanes => onStep(lanes.flatMap(id => markerIds(scene, id)))} />}
      {tab === 'changes' && <PlanChanges plan={plan} onHover={hover} />}
      {tab === 'story' && <RunStory story={story} error={storyError} startNs={startNs} terminal={terminal} expectations={plan.expectations} onSeek={onSeek} onAsk={askWhy} />}
      {stage !== 'proposed' && !isOld && tab === 'scene' && plan.expectations?.length ? <Watch plan={plan} /> : null}
    </div>
    </div>

    <footer className="as-board-foot">
      {isOld ? <p className="as-quiet">{L('Viewing an earlier version. Only the latest version can be confirmed.', '正在查看早期版本。只能确认最新版本。')}</p>
        : stage === 'proposed' ? <>
          <Validation plan={plan} onFix={() => ask(L('Fix the problems the simulator reported.', '修复仿真器报告的问题。'))} busy={busy} />
          {applyError && <p className="as-note bad" role="alert">{applyError}</p>}
          <div className="as-foot-actions">
            <span className="as-quiet">{applyDone ? L('Already applied.', '已应用。') : L('Your scene changes only when you confirm.', '确认后场景才会改变。')}</span>
            <button className="wb-btn primary as-confirm" disabled={!valid || busy || applying || applyDone} onClick={() => void confirm()} data-testid="as-confirm"
              title={!valid ? L('Available once the simulator has checked this version', '仿真器校验通过后可用') : busy ? L('Wait for the assistant to finish', '请等待助手完成') : undefined}>
              {applying ? <><span className="as-spin" />{L('Applying…', '正在应用…')}</> : <><Icon name="check" size={13} />{L('Confirm and apply', '确认并应用')}</>}
            </button>
          </div>
        </>
        : stage === 'configured' ? <div className="as-foot-actions">
          <button className="wb-btn" disabled={busy} onClick={() => ask(L('What should I watch for when I run it?', '运行时我应该关注什么？'))} data-testid="as-what-to-watch"><Icon name="eye" size={13} />{L('What will I see?', '我会看到什么？')}</button>
          <button className="wb-btn primary" disabled={!canInitialize || initializing} onClick={onInitialize} data-testid="as-initialize"><Icon name="play" size={12} />{initializing ? L('Initializing…', '初始化中…') : L(`Initialize version ${applied?.version ?? ''}`, `初始化版本 ${applied?.version ?? ''}`)}</button>
        </div>
        : <div className="as-foot-actions">
          <span className="as-quiet">{terminal ? L('Ask about anything that surprised you.', '对结果有疑问可以直接问。') : L('The story updates as events are recorded.', '记录到事件后经过会更新。')}</span>
          <button className="wb-btn" disabled={busy} onClick={() => ask(terminal ? L('Summarise what happened in this run compared with the plan.', '对照方案总结这次运行发生了什么。') : L('What is happening right now?', '现在发生了什么？'))}>{terminal ? L('Summarise the run', '总结运行') : L('What is happening?', '现在怎样了？')}</button>
        </div>}
    </footer>
  </aside>;
}

function Validation({ plan, onFix, busy }: { plan: PlanVersion; onFix: () => void; busy: boolean }) {
  const { locale } = useI18n(), L = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const errors = [...plan.compile.errors.map(error => `${error.op_id ? `${error.op_id}: ` : ''}${error.message}`), ...(plan.validation.errors ?? [])];
  const state = !plan.compile.ok ? 'invalid' : plan.validation.state;
  return <div className={`as-validation is-${state}`} data-testid="as-validation" data-state={state} role="status">
    {state === 'pending' ? <><span className="as-spin" />{L('Checking with the simulator…', '正在用仿真器校验…')}</>
      : state === 'valid' ? <><Icon name="check" size={12} />{L('Checked: the simulator loads this scene', '已校验：仿真器可以加载此场景')}{plan.compile.warnings.length ? L(` · ${plan.compile.warnings.length} note${plan.compile.warnings.length === 1 ? '' : 's'}`, ` · ${plan.compile.warnings.length} 条提示`) : ''}</>
      : <details><summary><Icon name="cross" size={12} />{L('The simulator rejected this version', '仿真器拒绝了此版本')}</summary>
        <ul>{errors.slice(0, 6).map(error => <li key={error} className="wb-mono">{bi(error, locale === 'zh-CN')}</li>)}</ul>
        <button className="wb-btn small" disabled={busy} onClick={onFix}>{L('Ask the assistant to fix it', '让助手修复')}</button>
      </details>}
  </div>;
}

function ApplySummary({ record, onHover }: { record: ApplyRecord; onHover: (id: string | undefined) => void }) {
  const { locale } = useI18n(), L = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const { created, changed, removed } = record.report;
  return <section className="as-applied-card" data-testid="as-apply-report">
    <div className="as-applied-head"><span className="as-applied-icon"><Icon name="check" size={13} /></span>
      <div><strong>{L(`Applied as version ${record.version}`, `已应用为版本 ${record.version}`)}</strong>
        <small className="wb-mono">{L(`${created.length} created · ${changed.length} changed · ${removed.length} removed`, `新增 ${created.length} · 修改 ${changed.length} · 移除 ${removed.length}`)}</small></div></div>
    <DiffList diff={{ added: created, changed, removed }} onHover={onHover} empty={L('Nothing in the scene changed.', '场景没有变化。')} />
  </section>;
}

function Watch({ plan }: { plan: PlanVersion }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  return <section className="as-section as-watch" data-testid="as-watch">
    <h4><span>{L('What to watch when it runs', '运行时关注')}</span><i /><em className="wb-mono">{String(plan.expectations!.length).padStart(2, '0')}</em></h4>
    <ol>{plan.expectations!.map((row, index) => <li key={row.id} style={{ '--i': index } as CSSProperties} className={`is-${row.layer}`}>
      <time className="wb-mono">{row.approx_s !== undefined && row.approx_s !== null ? `≈${clock(row.approx_s)}` : '—'}</time>
      <div><span>{(zh ? row.text_zh : undefined) ?? row.text}</span>
        {row.precheck && <small className={row.precheck.ok ? 'is-ok' : 'is-warn'}><Icon name={row.precheck.ok ? 'check' : 'incident'} size={10} />{(zh ? row.precheck.text_zh : undefined) ?? row.precheck.text}</small>}</div>
    </li>)}</ol>
  </section>;
}
