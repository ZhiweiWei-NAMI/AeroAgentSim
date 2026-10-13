import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { issueOf, questionOf, type ApplyRecord, type AssistantApi, type CompileIssue, type PlanVersion, type Story, type StoryItem } from './api';
import { Icon } from './icons';
import { InteractionDiagram } from './InteractionDiagram';
import { markerIds } from './overlay';
import { DiffList, PlanChanges } from './PlanChanges';
import { bi, clock } from './present';
import { RunStory } from './RunStory';
import { SceneCards } from './SceneCards';
import { AlternativesCompare } from './alternatives/AlternativesCompare';
import type { AssistantSession } from './useAssistant';
import { draftMessage } from './AssistantPanel';
import { Contained } from './Contained';
import { parameterName, PlanSummary } from './OperationSummary';
import { valueText } from './present';
import { RegionProposal } from './RegionProposal';

function sectionHref(panel?: string, records = false) {
  const query=new URLSearchParams(window.location.search);
  if(panel)query.set('panel',panel);
  if(records)query.set('records','1');else query.delete('records');
  return `${window.location.pathname}?${query}`;
}

export type Stage = 'proposed' | 'configured' | 'running';
type Tab = 'scene' | 'interactions' | 'changes' | 'story';

/** The plan, the configured scene and the run, in one sheet whose state is never ambiguous. */
type PlanBoardProps = {
  api: AssistantApi; assistant: AssistantSession; stage: Stage; waiting?: boolean; runId?: string;
  story?: Story; storyError?: string; startNs?: string; terminal: boolean;
  initializing: boolean; canInitialize: boolean; latestVersion?: number; onInitialize: (version: number) => void; onApplied: (record: ApplyRecord) => void;
  onHover: (ids: string[]) => void; onStep: (ids: string[]) => void; onContract: (type: string) => void; onSeek: (cut: number) => void;
  viewing?: number; onViewVersion: (version: number | undefined) => void; onClose: () => void;
};

export function PlanBoard(props: PlanBoardProps) {
  const {locale}=useI18n(),L=(en: string,zh: string)=>locale==='zh-CN'?zh:en;
  return <Contained resetKey={`${props.assistant.session?.id}-${props.assistant.plan?.version}-${props.viewing}`} fallback={retry=><aside className="as-board wb-panel wb-glass" data-testid="as-board-failed" role="alert">
    <p>{L('This plan could not be displayed. The conversation and project remain available.','无法显示此方案，对话和项目仍可使用。')}</p>
    <button className="wb-btn" onClick={retry}>{L('Try again','重试')}</button>
    <button className="wb-btn" disabled={props.assistant.busy} onClick={()=>void props.assistant.send(L('Fix the problems in this plan.','修复此方案的问题。'),{locale,...(props.runId?{run_id:props.runId}:{})}).catch(()=>undefined)}>{L('Ask the assistant to fix it','让助手修复')}</button>
    <button className="wb-btn ghost" onClick={props.onClose}>{L('Close','关闭')}</button>
  </aside>}><PlanBoardBody {...props}/></Contained>;
}

function PlanBoardBody({ api, assistant, stage, waiting = false, runId, story, storyError, startNs, terminal, initializing, canInitialize, latestVersion, onInitialize, onApplied, onHover, onStep, onContract, onSeek, viewing, onViewVersion, onClose }: PlanBoardProps) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const { session, plan: latest, busy, applied } = assistant;
  const [older, setOlder] = useState<PlanVersion>();
  const [versionError,setVersionError]=useState<string>();
  const [answered, setAnswered] = useState<string[]>([]);
  useEffect(() => { try { setAnswered(JSON.parse(sessionStorage.getItem(`aas-answered:${session?.id}`) ?? '[]')); } catch { setAnswered([]); } }, [session?.id]);
  const [tab, setTab] = useState<Tab>(stage === 'running' ? 'story' : 'scene');
  const [compareAlt, setCompareAlt] = useState(false);
  const [applying, setApplying] = useState(false), [applyError, setApplyError] = useState<string>();
  const scroller = useRef<HTMLDivElement>(null), tabsRef = useRef<HTMLElement>(null), openedAt = useRef(stage);
  // Choosing a tab brings its content up under the pinned tab bar.
  const choose = (next: Tab) => { setTab(next); const box = scroller.current, bar = tabsRef.current; if (box && bar) requestAnimationFrame(() => box.scrollTo({ top: box.scrollTop + (bar.nextElementSibling ?? bar).getBoundingClientRect().top - box.getBoundingClientRect().top - (bar.nextElementSibling ? bar.offsetHeight : 0), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })); };
  useEffect(() => { if (stage === 'running') setTab('story'); else if (tab === 'story') setTab('scene'); }, [stage]);
  useEffect(() => {
    setVersionError(undefined);setOlder(undefined);
    if (viewing === undefined || viewing === latest?.version || !session) return;
    const abort = new AbortController();
    void api.plan(session.id, viewing, abort.signal).then(value=>{if(!abort.signal.aborted)setOlder(value);}).catch(problem=>{if(!abort.signal.aborted)setVersionError(String(problem));});
    return () => abort.abort();
  }, [api, session?.id, viewing, latest?.version]);
  const view = (version: number | undefined) => onViewVersion(version === latest?.version ? undefined : version);

  const plan = older ?? latest;
  if (!session || !plan) return null;
  const isOld = !!older && older.version !== latest?.version;
  // Which plan version actually changed the project — a shown plan that was
  // already applied is labelled "applied", never presented as an open draft.
  const appliedForPlan = session.applied.filter(row => row.plan_version === plan.version).at(-1);
  const supersededBy = appliedForPlan ? session.applied.filter(row => row.plan_version !== plan.version && row.applied_at >= appliedForPlan.applied_at).at(-1) : undefined;
  const alternatives = session.alternatives ?? [];
  const isAlternative = alternatives.includes(plan.version) && plan.version !== latest?.version;
  const scene = plan.scene;
  const hover = (id: string | undefined) => onHover(id ? markerIds(scene, id) : []);
  const loadingVersion=viewing!==undefined && viewing!==latest?.version && older?.version!==viewing;
  const valid = plan.validation.state === 'valid' && plan.compile.ok && !loadingVersion && !versionError, applyDone = !!appliedForPlan;
  const confirm = async () => {
    setApplying(true); setApplyError(undefined);
    try { onApplied(await assistant.apply(plan.version)); } catch (problem) { setApplyError(problem instanceof Error ? problem.message : String(problem)); } finally { setApplying(false); }
  };
  const ask = (text: string) => void assistant.send(text,{locale,...(runId?{run_id:runId}:{})}).catch(() => undefined);
  // A question the user answered here stays answered even if a revision repeats it.
  const answer = (question: string, text: string) => { setAnswered(rows => { const next = [...rows, question]; try { sessionStorage.setItem(`aas-answered:${session.id}`, JSON.stringify(next)); } catch { /* storage unavailable */ } return next; }); ask(text); };
  const questions = plan.open_questions.map(questionOf).filter(row => !answered.includes(row.question));
  const askWhy = (item: StoryItem) => ask(L(`Why didn't this happen: "${item.text}"?`, `为什么这一步没有发生：“${item.text_zh ?? item.text}”？`));
  // A plan that is already applied is shown as applied (or superseded), never as a
  // fresh proposal — returning from Form or a refresh must not resurrect it.
  const appliedShown = !isOld && !!appliedForPlan;
  // While a run is live the board stays in the running stage; otherwise an applied
  // plan is labelled applied/superseded, never resurrected as an open draft.
  const shownStage: Stage = appliedShown ? (stage === 'running' ? 'running' : 'configured') : isOld ? 'proposed' : stage;
  const rows = session.plan_versions;
  const initializeVersion=latestVersion ?? assistant.draftState?.latestVersion;
  const initializeAllowed=canInitialize && initializeVersion !== undefined && !assistant.draftState?.dirty && !loadingVersion && !versionError;
  const initialize = () => { if (initializeAllowed && !initializing) onInitialize(initializeVersion!); };

  return <aside className={`as-board wb-panel wb-glass is-${shownStage}${isOld ? ' is-old' : ''}`} data-testid="as-plan-board" data-stage={shownStage} aria-label={L('Plan board', '方案面板')}>
    <i className="as-board-scan" key={`scan-${plan.version}`} aria-hidden />
    {/* The stage pulse marks a transition, so it never plays for the stage the board opened in. */}
    {shownStage !== openedAt.current && <i className="as-board-charge" key={`charge-${shownStage}`} aria-hidden />}
    <header className="as-board-head">
      <ol className="as-rail" aria-label={L('Where you are', '当前阶段')}>
        {(['proposed', 'configured', 'running'] as Stage[]).map((step, index, all) => {
          const at = all.indexOf(shownStage);
          // An initialized run waits at t = 0 until Start; it is not running yet.
          const ready = step === 'running' && waiting && index === at;
          return <li key={step} className={index === at ? `is-on${ready ? ' is-waiting' : ''}` : index < at ? 'is-past' : ''} aria-current={index === at ? 'step' : undefined}>
            <i />{step === 'proposed' ? L('Proposed', '提议') : step === 'configured' ? L('Configured', '已配置') : ready ? L('Ready · t = 0', '就绪 · t = 0') : L('Running', '运行')}
          </li>;
        })}
      </ol>
      <button className="wb-x" onClick={onClose} aria-label={L('Close plan board', '关闭方案面板')}>✕</button>
    </header>

    <div className="as-board-scroll" ref={scroller}>
    <div className="as-board-title as-rise">
      <span className={`as-badge is-${shownStage}`} data-testid="as-plan-state">
        {shownStage === 'proposed' ? <>{isOld ? L(`Earlier version · v${plan.version}`, `早期版本 · v${plan.version}`) : L(`Proposed plan · v${plan.version}`, `提议方案 · v${plan.version}`)}<em>{L('not applied', '未应用')}</em></>
          : shownStage === 'configured' ? <>{appliedShown
              ? <>{supersededBy ? L(`Superseded · v${plan.version}`, `已被取代 · v${plan.version}`) : L(`Applied as version ${appliedForPlan?.version ?? ''}`, `已应用为版本 ${appliedForPlan?.version ?? ''}`)}</>
              : <>{L(`Configured · version ${applied?.version ?? ''}`, `已配置 · 版本 ${applied?.version ?? ''}`)}</>}
            <em>{supersededBy ? L(`superseded by version ${supersededBy.version}`, `已被版本 ${supersededBy.version} 取代`) : L('not running', '未运行')}</em></>
          : <>{terminal ? L('Run finished', '运行结束') : waiting ? L('Initialized · press Start', '已初始化 · 请按启动') : L('Running', '运行中')}<em className="wb-mono">{runId}</em></>}
      </span>
      <h2>{bi(plan.title,zh)}</h2>
      <PlanSummary plan={plan}/>
      {versionError?<p className="wb-issues" role="alert">{L(`Could not load plan v${viewing}.`,`无法加载方案 v${viewing}。`)} {bi(versionError,zh)}</p>
        :loadingVersion?<p className="hint" role="status">{L(`Loading plan v${viewing}…`,`正在加载方案 v${viewing}…`)}</p>:null}
      {appliedShown && !isOld && <DraftNotice state={assistant.draftState}/>}
      {rows.length > 1 && <div className="as-versions" role="tablist" aria-label={L('Plan versions', '方案版本')}>
        {rows.map(row => <button key={row.version} role="tab" aria-selected={row.version === plan.version} title={bi(row.note, zh)} onClick={() => view(row.version)} data-testid={`as-version-${row.version}`}
          className={`${row.version === latest?.version ? 'is-latest' : ''} is-${row.validation_state}${alternatives.includes(row.version) ? ' is-alt' : ''}`}>
          {row.variant ? <b>{bi(row.variant,zh)}</b> : null}v{row.version}
        </button>)}
        {alternatives.length >= 2 && <button className={`wb-btn small ghost as-alt-toggle${compareAlt ? ' is-on' : ''}`} aria-pressed={compareAlt} data-testid="as-alt-compare" onClick={() => setCompareAlt(value => !value)}>
          {L('Compare side by side', '并排对比')}
        </button>}
      </div>}
      {isOld && <div className="as-old-note">
        <span>{bi(plan.note, zh) || L('An earlier version, shown for comparison.', '早期版本，仅供对比。')}</span>
        {isAlternative ? <button className="wb-btn small primary" onClick={() => void assistant.choose(plan.version)} data-testid="as-choose">{L('Choose this option', '选择此方案')}</button>
          : <button className="wb-btn small" onClick={() => ask(L(`Go back to version ${plan.version}, keeping everything else the same.`, `回到版本 ${plan.version}，其余保持不变。`))} disabled={busy}>{L('Go back to this version', '回到此版本')}</button>}
        <button className="wb-btn small ghost" onClick={() => view(undefined)}>{L('Show latest', '查看最新')}</button>
      </div>}
      {/* Read-only side-by-side review of the proposed alternatives (AST-015).
          Selecting one only opens it for review; applying stays on the explicit confirm path. */}
      {!isOld && compareAlt && alternatives.length >= 2 && session &&
        <AlternativesCompare api={api} sessionId={session.id} versions={alternatives} current={viewing ?? latest?.version}
          onSelect={version => view(version === latest?.version ? undefined : version)} onClose={() => setCompareAlt(false)} />}
    </div>

    {!isOld && (shownStage === 'proposed' || !!assistant.draftState?.changes?.length) && (questions.length > 0 || plan.assumptions.length > 0) && <div className="as-asks as-rise">
      {(shownStage==='proposed'?questions:[]).map(({ question, options }) => <div key={question} className="as-question" data-testid="as-question">
        <span className="as-question-mark">?</span>
        <div><p>{bi(question,zh)}</p>
          <div className="as-question-options">
            {options.map(option => <button key={option} className="wb-btn small" disabled={busy} onClick={() => answer(question, L(`For "${bi(question,zh)}": ${bi(option,zh)}`, `关于“${bi(question,zh)}”：${bi(option,zh)}`))}>{bi(option,zh)}</button>)}
            <button className="wb-btn small ghost" disabled={busy} onClick={() => answer(question, L(`Use a sensible default for: ${question}`, `这个问题用合理的默认值：${question}`))}>{L('Use a sensible default', '用合理默认值')}</button>
          </div>
        </div>
      </div>)}
      {plan.assumptions.length > 0 && <details className="as-assumptions" open={shownStage==='proposed' && (plan.version === 1 || questions.length === 0)}>
        <summary>{shownStage==='proposed'?L(`Assumptions (${plan.assumptions.length})`, `假设（${plan.assumptions.length}）`):L('Assumptions in this plan','此方案的假设')}<small>{shownStage==='proposed'?L('filled in for you — change any of them', '已为你补全，可随时修改'):L('The current draft differs below.','当前草稿已改变。')}</small></summary>
        <ul>{plan.assumptions.map(text => <li key={text}>
          <span>{bi(text,zh)}</span>
          <button className="as-link" onClick={() => draftMessage(L(`Instead of "${text}", `, `不要“${text}”，改为`))}>{L('Change', '修改')}</button>
        </li>)}</ul>
      </details>}
    </div>}

    {shownStage === 'configured' && !isOld && appliedShown && appliedForPlan && <ApplySummary record={appliedForPlan} onHover={hover} supersededBy={supersededBy?.version} />}

    <nav className="as-tabs" role="tablist" ref={tabsRef}>
      {stage === 'running' && !isOld && <button role="tab" aria-selected={tab === 'story'} onClick={() => choose('story')} data-testid="as-tab-story">{L('Story', '经过')}</button>}
      <button role="tab" aria-selected={tab === 'scene'} onClick={() => choose('scene')} data-testid="as-tab-scene">{L('Scene', '场景')}</button>
      <button role="tab" aria-selected={tab === 'interactions'} onClick={() => choose('interactions')} disabled={!plan.interactions?.steps?.length} data-testid="as-tab-interactions">{L('Interactions', '交互')}{plan.interactions?.steps?.length ? <span>{plan.interactions.steps.length}</span> : null}</button>
      <button role="tab" aria-selected={tab === 'changes'} onClick={() => choose('changes')} data-testid="as-tab-changes">{L('Changes', '变更')}{plan.diff_previous ? <span>{plan.diff_previous.added.length + plan.diff_previous.changed.length + plan.diff_previous.removed.length}</span> : null}</button>
    </nav>

    <div className="as-board-body" key={`${plan.version}-${tab}`}>
      {isOld && (!plan.compile.ok || plan.validation.state==='invalid') && <Validation plan={plan} busy={busy} onFix={issue=>ask(issue
        ? L(`Fix plan version ${plan.version}, operation ${issue.op_id ?? ''}, field ${issue.field ?? ''}: ${bi(issue.message,zh)}`,`修复方案版本 ${plan.version}，操作 ${issue.op_id ?? ''}，字段 ${issue.field ?? ''}：${bi(issue.message,zh)}`)
        : L(`Fix the problems in plan version ${plan.version}.`,`修复方案版本 ${plan.version} 中的问题。`))}/>}
      {tab==='scene' && shownStage==='proposed' && <RegionProposal plan={plan} preview={assistant.planPreview?.version===plan.version?assistant.planPreview:undefined}/>}
      {tab === 'scene' && (scene ? <SceneCards scene={scene} diff={plan.diff_previous ?? plan.diff_base} onHover={hover} onContract={onContract} busy={busy || isOld} onMode={ask} /> : <p className="as-quiet">{L('The plan could not be compiled into a scene yet.', '方案尚未编译成场景。')}</p>)}
      {tab === 'interactions' && plan.interactions && <InteractionDiagram interactions={plan.interactions} onStep={lanes => onStep(lanes.flatMap(id => markerIds(scene, id)))} />}
      {tab === 'changes' && <PlanChanges plan={plan} onHover={hover} />}
      {tab === 'story' && <RunStory story={story} error={storyError} startNs={startNs} terminal={terminal} expectations={plan.expectations} planVersion={plan.version} onSeek={onSeek} onAsk={askWhy} />}
      {stage !== 'proposed' && !isOld && tab === 'scene' && plan.expectations?.length ? <Watch plan={plan} /> : null}
    </div>
    </div>

    <footer className="as-board-foot">
      {isOld ? <p className="as-quiet">{L('Viewing an earlier version. Only the latest version can be confirmed.', '正在查看早期版本。只能确认最新版本。')}</p>
        : appliedShown ? <>
          {shownStage === 'configured' ? <div className="as-foot-actions">
            <span className="as-quiet">{supersededBy ? L(`A later version (${supersededBy.version}) is what the project runs now.`, `项目现在使用的是更新的版本（${supersededBy.version}）。`) : L('Already applied to the project. Nothing more to confirm.', '已应用到项目。无需再次确认。')}</span>
            <button className="wb-btn" disabled={busy} onClick={()=>ask(L('What should I watch for when I run it?','运行时我应该关注什么？'))} data-testid="as-what-to-watch"><Icon name="eye" size={13}/>{L('What will I see?','我会看到什么？')}</button>
            {!supersededBy && <button className="wb-btn primary" disabled={!initializeAllowed || initializing} onClick={initialize} data-testid="as-initialize"><Icon name="play" size={12} />{initializing ? L('Initializing…', '初始化中…') : L(`Initialize version ${initializeVersion ?? ''}`, `初始化版本 ${initializeVersion ?? ''}`)}</button>}
            {!supersededBy && !initializeAllowed && <span className="hint">{assistant.draftState?.dirty?L('Validate and save the changed draft as a version before initializing.','请先校验修改后的草稿并保存版本，再初始化。'):L('Save a valid version before initializing.','请先保存有效版本，再初始化。')} <a className="wb-btn small ghost" href={sectionHref('controls')}>{L('Open Configure','打开配置')}</a></span>}
          </div> : <div className="as-foot-actions">
            <span className="as-quiet">{terminal ? L('Ask about anything that surprised you.', '对结果有疑问可以直接问。') : L('The story updates as events are recorded.', '记录到事件后经过会更新。')}</span>
            <button className="wb-btn" disabled={busy||!runId} onClick={() => ask(terminal ? L('Summarise what happened in this run compared with the plan.', '对照方案总结这次运行发生了什么。') : L('What is happening right now?', '现在发生了什么？'))}>{terminal ? L('Summarise the run', '总结运行') : L('What is happening?', '现在怎样了？')}</button>
            {!runId&&<span className="hint">{L('Select a run in Records to ask about its results.','请在记录中选择一次运行，再询问其结果。')} <a className="wb-btn small ghost" href={sectionHref(undefined,true)}>{L('Open Records','打开记录')}</a></span>}
          </div>}
        </>
        : stage === 'proposed' ? <>
          <Validation plan={plan} busy={busy}
            onFix={operation => ask(operation
              ? L(`Revise operation ${operation.op_id ?? ''}: fix the problem with ${operation.field ?? 'its parameters'} — ${bi(operation.message,zh)}`.replace(/\s+/g, ' ').trim(), `修改操作 ${operation.op_id ?? ''}：解决 ${operation.field ?? '其参数'} 的问题 —— ${bi(operation.message,zh)}`.replace(/\s+/g, ' ').trim())
              : L('Fix the problems the simulator reported.', '修复仿真器报告的问题。'))} />
          {applyError && <p className="as-note bad" role="alert">{bi(applyError,zh)}</p>}
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
          <button className="wb-btn primary" disabled={!initializeAllowed || initializing} onClick={initialize} data-testid="as-initialize"><Icon name="play" size={12} />{initializing ? L('Initializing…', '初始化中…') : L(`Initialize version ${initializeVersion ?? ''}`, `初始化版本 ${initializeVersion ?? ''}`)}</button>
        </div>
        : <div className="as-foot-actions">
          <span className="as-quiet">{terminal ? L('Ask about anything that surprised you.', '对结果有疑问可以直接问。') : L('The story updates as events are recorded.', '记录到事件后经过会更新。')}</span>
          <button className="wb-btn" disabled={busy||!runId} onClick={() => ask(terminal ? L('Summarise what happened in this run compared with the plan.', '对照方案总结这次运行发生了什么。') : L('What is happening right now?', '现在发生了什么？'))}>{terminal ? L('Summarise the run', '总结运行') : L('What is happening?', '现在怎样了？')}</button>
          {!runId&&<span className="hint">{L('Select a run in Records to ask about its results.','请在记录中选择一次运行，再询问其结果。')} <a className="wb-btn small ghost" href={sectionHref(undefined,true)}>{L('Open Records','打开记录')}</a></span>}
        </div>}
    </footer>
  </aside>;
}

function Validation({ plan, onFix, busy }: { plan: PlanVersion; onFix: (operation?: CompileIssue) => void; busy: boolean }) {
  const { locale } = useI18n(), L = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const seen=new Set<string>();
  const all=[...plan.compile.errors,...(plan.validation.errors??[])].map(issueOf).filter(issue=>{
    const key=JSON.stringify([issue.op_id,issue.field,issue.message]);if(seen.has(key))return false;seen.add(key);return true;
  });
  const state = !plan.compile.ok ? 'invalid' : plan.validation.state, zh = locale === 'zh-CN';
  const notes = plan.compile.warnings.map(row => typeof row === 'string' ? row : row.message);
  return <>{state !== 'invalid' && notes.length > 0 && <ul className="as-compile-notes" data-testid="as-compile-notes">{notes.slice(0, 3).map(note => <li key={note}><Icon name="dot" size={10} />{bi(note, zh)}</li>)}</ul>}
  <div className={`as-validation is-${state}`} data-testid="as-validation" data-state={state} role="status">
    {state === 'pending' ? <><span className="as-spin" />{L('Checking with the simulator…', '正在用仿真器校验…')}</>
      : state === 'valid' ? <><Icon name="check" size={12} />{L('Checked: the simulator loads this scene', '已校验：仿真器可以加载此场景')}</>
      : <details open={all.length > 0}>
        <summary><Icon name="cross" size={12} />{L('The simulator rejected this version', '仿真器拒绝了此版本')}</summary>
        <ul className="as-op-errors" data-testid="as-op-errors">{all.slice(0, 6).map((error, index) => <li key={`${error.op_id ?? ''}-${index}`}>
          <span className="wb-mono">{error.op_id?`${error.op_id}: `:''}{bi(error.message,zh)}</span>
          {error.field && <small className="as-op-error-field">{L('field', '字段')} · <code>{error.field}</code></small>}
          <button className="wb-btn small ghost" disabled={busy} onClick={() => onFix(error.op_id||error.field ? error : undefined)} data-testid="as-revise">
            {L('Revise', '修改')}
          </button>
        </li>)}</ul>
        {all.length > 6 && <small className="as-quiet">{L(`${all.length - 6} more problems`, `还有 ${all.length - 6} 个问题`)}</small>}
        <button className="wb-btn small" disabled={busy} onClick={() => onFix()}>{L('Ask the assistant to fix it', '让助手修复')}</button>
      </details>}
  </div></>;
}

function DraftNotice({ state }: { state: AssistantSession['draftState'] }) {
  const {locale}=useI18n(),L=(en: string,zh: string)=>locale==='zh-CN'?zh:en;
  if(!state)return null;
  if(state.error)return <p className="wb-issues" role="alert">{L('Could not compare the draft with this plan.','无法将草稿与此方案对比。')} {bi(state.error,locale==='zh-CN')}</p>;
  if(!state.changes)return <p className="hint">{L('Comparing the current draft with this plan…','正在对比当前草稿与此方案…')}</p>;
  if(!state.changes.length)return null;
  const value=(item: unknown)=>item===undefined?L('Not present','不存在'):valueText(item);
  return <section className="as-draft-notice" role="note" data-testid="as-draft-changed">
    <strong>{L('Draft changed since this plan','此方案之后草稿已改变')}</strong>
    <p className="hint">{L('The scene cards and assumptions below describe this plan. The current draft has these changes:','下方场景卡片和假设描述的是此方案，当前草稿有以下变更：')}</p>
    <ul>{state.changes.map((change,index)=><li key={`${change.path.join('.')}-${index}`}>
      <span>{change.path.map((part,index)=>index>0 && ['entities','generators','engines'].includes(change.path[index-1])?part:parameterName(part,L)).join(' › ')}</span>
      <span className="wb-mono">{value(change.before)} → {value(change.after)}</span>
    </li>)}</ul>
  </section>;
}

function ApplySummary({ record, onHover, supersededBy }: { record: ApplyRecord; onHover: (id: string | undefined) => void; supersededBy?: number }) {
  const { locale } = useI18n(), L = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const { created, changed, removed } = record.report;
  return <section className="as-applied-card" data-testid="as-apply-report">
    <div className="as-applied-head"><span className="as-applied-icon"><Icon name="check" size={13} /></span>
      <div><strong>{L(`Applied as version ${record.version}`, `已应用为版本 ${record.version}`)}</strong>
        <small className="wb-mono">{L(`${created.length} created · ${changed.length} changed · ${removed.length} removed`, `新增 ${created.length} · 修改 ${changed.length} · 移除 ${removed.length}`)}</small></div></div>
    {supersededBy !== undefined && <p className="as-note" role="note">{L(`This version is no longer the latest: the project now uses version ${supersededBy}.`, `此版本已不是最新：项目现在使用版本 ${supersededBy}。`)}</p>}
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
