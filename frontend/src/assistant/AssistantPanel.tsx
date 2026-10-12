import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import { Markdown } from '../workbench/Markdown';
import type { AssistantEvent, Connection, MessageContext } from './api';
import { ConnectModel } from './ConnectModel';
import { Icon } from './icons';
import { bi } from './present';
import { ModeSwitch, type EditMode, type Origin } from './ModeSwitch';
import { NeuralGlyph } from './motion/NeuralGlyph';
import type { AssistantSession } from './useAssistant';

type ToolEvent = Extract<AssistantEvent, { kind: 'tool' }>;
type Item =
  | { type: 'user'; key: string; text: string; context?: MessageContext; pending?: boolean }
  | { type: 'work'; key: string; tools: ToolEvent[]; open: boolean }
  | { type: 'assistant'; key: string; text: string; meta?: string }
  | { type: 'plan'; key: string; version: number; note: string; variant?: string | null; versions?: number[] }
  | { type: 'validated'; key: string; version: number; valid: boolean; errors?: string[] }
  | { type: 'applied'; key: string; version: number; planVersion: number; created: number; changed: number; removed: number }
  | { type: 'notice'; key: string; text: string }
  | { type: 'error'; key: string; text: string };

/** Fold the event log into what a reader follows: messages, one work log per turn, plan milestones. */
export function transcript(events: AssistantEvent[], pending: { text: string; context?: MessageContext } | undefined, busy: boolean, zh: boolean): Item[] {
  const items: Item[] = [];
  let work: Extract<Item, { type: 'work' }> | undefined, started: number | undefined;
  for (const event of events) {
    const key = `e${event.seq}`;
    if (event.kind === 'turn_started') { work = undefined; started = items.length; continue; }
    if (event.kind === 'turn_finished') {
      const answer = [...items].reverse().find(item => item.type === 'assistant') as Extract<Item, { type: 'assistant' }> | undefined;
      if (answer && event.elapsed_s !== undefined && items.indexOf(answer) >= (started ?? 0)) answer.meta = `${event.elapsed_s.toFixed(1)} s${event.model_calls ? zh ? ` · ${event.model_calls} 次模型调用` : ` · ${event.model_calls} model call${event.model_calls === 1 ? '' : 's'}` : ''}`;
      work = undefined; continue;
    }
    if (event.kind === 'tool') {
      if (!work) { work = { type: 'work', key, tools: [], open: false }; items.push(work); }
      const index = work.tools.findIndex(tool => tool.call_id === event.call_id);
      if (index >= 0) work.tools[index] = event; else work.tools.push(event);
      continue;
    }
    if (event.kind === 'user') items.push({ type: 'user', key, text: event.text, context: event.context });
    else if (event.kind === 'assistant') { items.push({ type: 'assistant', key, text: event.text }); work = undefined; }
    else if (event.kind === 'plan') items.push({ type: 'plan', key, version: event.version, note: event.note, variant: event.variant, versions: event.versions });
    else if (event.kind === 'plan_validated') { if (!event.valid) items.push({ type: 'validated', key, version: event.version, valid: event.valid, errors: event.errors }); }
    else if (event.kind === 'applied') items.push({ type: 'applied', key, version: event.version, planVersion: event.plan_version, created: event.report?.created?.length ?? 0, changed: event.report?.changed?.length ?? 0, removed: event.report?.removed?.length ?? 0 });
    else if (event.kind === 'notice') items.push({ type: 'notice', key, text: event.text });
    else if (event.kind === 'error') items.push({ type: 'error', key, text: event.message });
  }
  if (pending && !items.some(item => item.type === 'user' && item.text === pending.text && items.indexOf(item) >= items.length - 3)) items.push({ type: 'user', key: 'pending', text: pending.text, context: pending.context, pending: true });
  // The running turn's work log stays expanded so progress is visible.
  if (busy && work) work.open = true;
  return items;
}

const STARTERS = {
  project: [
    ['Explain this scene in plain words', '用通俗的话介绍这个场景'],
    ['A drone that notices a fallen person should hand its parcel to a nearby courier and photograph the person', '发现有人跌倒的无人机把包裹交给附近的配送员，然后去拍照'],
    ['Make it busier: twice the orders, keep everything else the same', '让它更繁忙：订单翻倍，其他保持不变'],
    ['Show me two options for handling a sudden medical request', '给我两个应对突发医疗请求的方案'],
  ],
  none: [
    ['I want to test a few drones', '我想测试几架无人机'],
    ['Can we create a scenario that mixes food delivery and logistics?', '能否创建一个外卖配送与物流混合的场景？'],
    ['A logistics drone notices a person has fallen, asks a food-delivery courier to take over its parcel, then photographs the person', '物流无人机发现有人跌倒，请外卖配送员接手包裹，然后去给跌倒的人拍照'],
    ['A cooperative power-line inspection with two drones', '两架无人机协同巡检输电线路'],
  ],
};

export function AssistantPanel({ assistant, connection, connectionError, connectionSaving, onConnect, onTest, project, selected, runId, onClearSelected, onClose, onSwitch, switching, morph, onShowPlan, onShowChanges, suggestionsExtra }: {
  assistant: AssistantSession; connection?: Connection; connectionError?: string; connectionSaving: boolean;
  onConnect: (body: { base_url: string; model: string; api_key?: string; clear_key?: boolean }) => Promise<unknown>; onTest: () => Promise<void>;
  project?: { id: string; name: string }; selected?: { id: string; label: string }; runId?: string; onClearSelected?: () => void;
  onClose: () => void; onSwitch?: (mode: EditMode, origin: Origin) => void; switching?: boolean; morph?: 'in' | 'out';
  onShowPlan: (version: number) => void; onShowChanges: () => void; suggestionsExtra?: string[];
}) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const { events, busy, pending, error, plan } = assistant;
  // An unsent message survives Form ↔ Conversation switches and reloads of the same project.
  const draftKey = `aas-composer:${project?.id ?? 'new'}`;
  const [text, setText] = useState(() => { try { return sessionStorage.getItem(draftKey) ?? ''; } catch { return ''; } }), [showConnect, setShowConnect] = useState(false);
  useEffect(() => { try { if (text) sessionStorage.setItem(draftKey, text); else sessionStorage.removeItem(draftKey); } catch { /* storage unavailable */ } }, [draftKey, text]);
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const items = useMemo(() => transcript(events, pending, busy, zh), [events, pending, busy, zh]);
  // Messages already there when the conversation opened appear at once; new ones are revealed.
  const history = useRef<Set<string>>();
  if (!history.current && assistant.session) history.current = new Set(items.map(item => item.key));
  const lastTool = [...events].reverse().find(event => event.kind === 'tool');
  const scroller = useRef<HTMLDivElement>(null), pinned = useRef(true), input = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => { const node = scroller.current; if (node && pinned.current) node.scrollTop = node.scrollHeight; }, [items, busy]);
  useLayoutEffect(() => { const node = input.current; if (!node) return; node.style.height = 'auto'; node.style.height = `${Math.min(160, node.scrollHeight)}px`; }, [text]);
  useEffect(() => { const handler = (event: Event) => { const detail = (event as CustomEvent<string>).detail; if (typeof detail === 'string') { setText(detail); requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(detail.length, detail.length); }); } }; addEventListener('aas-assistant-draft', handler); return () => removeEventListener('aas-assistant-draft', handler); }, []);
  const needsConnection = !connection || !connection.configured || connection.status === 'error';
  const context: MessageContext = { locale, ...(selected ? { selected_entity: selected.id } : {}), ...(runId ? { run_id: runId } : {}) };
  const submit = (value = text) => {
    if (!value.trim() || busy) return;
    pinned.current = true;
    void assistant.send(value, context).catch(() => undefined);
    if (value === text) setText('');
  };
  const lastAnswer = [...events].reverse().find(event => event.kind === 'assistant') as Extract<AssistantEvent, { kind: 'assistant' }> | undefined;
  const lastUser = [...events].reverse().find(event => event.kind === 'user');
  const freshAnswer = lastAnswer && (!lastUser || lastAnswer.seq > lastUser.seq);
  const suggestions = [...new Set([...(freshAnswer ? lastAnswer?.suggestions ?? [] : []), ...(suggestionsExtra ?? [])])].slice(0, 5);
  const running = items.flatMap(item => item.type === 'work' ? item.tools : []).filter(tool => tool.status === 'running').at(-1);
  const toggle = (key: string) => setOpened(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; });

  const render = (item: Item): ReactNode => {
    switch (item.type) {
      case 'user': return <div className={`as-msg as-user${item.pending ? ' is-pending' : ''}`}><div className="as-bubble">{item.text}</div>{item.context?.selected_entity && <small className="as-about">{L('about', '关于')} {item.context.selected_entity}</small>}</div>;
      case 'assistant': return <div className={`as-msg as-bot${history.current?.has(item.key) ? '' : ' as-reveal'}`}><span className="as-avatar"><NeuralGlyph state="idle" size={18} /></span><div className="as-answer"><Markdown source={item.text} />{item.meta && <small className="as-meta">{item.meta}</small>}</div></div>;
      case 'work': {
        const open = item.open || opened.has(item.key), failed = item.tools.filter(tool => tool.status === 'error').length, done = item.tools.filter(tool => tool.status === 'ok').length;
        return <div className={`as-work${open ? ' is-open' : ''}`}>
          <button className="as-work-head" onClick={() => toggle(item.key)} aria-expanded={open}>
            <span className="as-work-dots">{item.tools.slice(-8).map(tool => <i key={tool.call_id} className={tool.status} />)}</span>
            <span>{item.tools.some(tool => tool.status === 'running') ? L('Working', '处理中') : L(`Used ${item.tools.length} platform tool${item.tools.length === 1 ? '' : 's'}`, `调用了 ${item.tools.length} 个平台工具`)}{failed ? L(` · ${failed} needed a retry`, ` · ${failed} 次需重试`) : ''}</span>
            <span className="as-chev" aria-hidden>{open ? '▾' : '▸'}</span>
          </button>
          {open && <ol>{item.tools.map(tool => <li key={tool.call_id} className={tool.status}><span className="as-step-icon">{tool.status === 'running' ? <i className="as-spin" /> : <Icon name={tool.status === 'ok' ? 'check' : 'cross'} size={12} />}</span><span><b>{(zh ? tool.label_zh : undefined) ?? tool.label}</b>{tool.detail && <small>{bi(tool.detail, zh)}</small>}</span></li>)}</ol>}
          {!open && done > 0 && <span className="sr-only">{done}</span>}
        </div>;
      }
      case 'plan': return <button className="as-plan-chip" onClick={() => onShowPlan(item.version)} data-testid="as-plan-chip">
        <span className="as-plan-v">v{item.version}{item.variant ? ` · ${item.variant}` : ''}</span>
        <span className="as-plan-note">{item.versions && item.versions.length > 1 ? L(`${item.versions.length} options to compare`, `${item.versions.length} 个方案可对比`) : bi(item.note, zh) || L('Plan updated', '方案已更新')}</span>
        <span className="as-plan-go">{L('View', '查看')} →</span>
      </button>;
      case 'validated': return <div className="as-note bad" role="status"><Icon name="cross" size={13} /><span>{L(`Plan v${item.version} does not pass the simulator's checks yet`, `方案 v${item.version} 尚未通过仿真器校验`)}{item.errors?.[0] ? ` — ${item.errors[0]}` : ''}</span></div>;
      case 'applied': return <div className="as-applied" data-testid="as-applied-card"><span className="as-applied-icon"><Icon name="check" size={14} /></span><div><b>{L(`Applied plan v${item.planVersion} as project version ${item.version}`, `方案 v${item.planVersion} 已应用为项目版本 ${item.version}`)}</b><small className="wb-mono">{L(`${item.created} created · ${item.changed} changed · ${item.removed} removed`, `新增 ${item.created} · 修改 ${item.changed} · 移除 ${item.removed}`)}</small></div><button className="wb-btn small" onClick={onShowChanges}>{L('What changed', '查看改动')}</button></div>;
      case 'notice': return <div className="as-note"><Icon name="dot" size={12} /><span>{bi(item.text, zh)}</span></div>;
      case 'error': return <div className="as-note bad" role="alert"><Icon name="cross" size={13} /><span>{bi(item.text, zh)}</span></div>;
    }
  };

  const glyph = needsConnection ? 'off' : error ? 'error' : busy ? 'thinking' : 'idle';
  return <aside className="wb-panel wb-right as-panel wb-glass" data-testid="wb-assistant" data-morph={morph} aria-label={L('Assistant', '助手')}>
    <header className="as-head as-rise">
      <span className="as-head-mark"><NeuralGlyph state={glyph} pulseKey={lastTool?.seq} size={24} title={L('Assistant', '助手')} /></span>
      <div className="as-head-title"><h3>{L('Assistant', '助手')}</h3>
        <button className="as-model" onClick={() => setShowConnect(value => !value)} title={connection?.base_url ?? ''} data-testid="as-model-chip"><span className={`as-led ${connection?.configured ? connection.status : 'none'}`} />{connection?.configured ? connection.model : L('No model connected', '未连接模型')}</button></div>
      {onSwitch && <ModeSwitch mode="chat" onChange={onSwitch} switching={switching} />}
      <button className="wb-x" onClick={onClose} aria-label={L('Close assistant', '关闭助手')}>✕</button>
    </header>
    {(showConnect || needsConnection && !events.length) ? <div className="as-body as-body-center as-rise"><ConnectModel connection={connection} error={connectionError} saving={connectionSaving} onSave={onConnect} onTest={onTest} onDone={() => setShowConnect(false)} compact={showConnect && !needsConnection} /></div> : <>
      <div className="as-body as-rise" ref={scroller} onScroll={event => { const node = event.currentTarget; pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40; }} data-testid="as-transcript" aria-live="polite">
        {!items.length && <div className="as-empty">
          <img className="as-empty-art" src={`${import.meta.env.BASE_URL}assistant/empty-city.webp`} alt="" width={180} height={180} />
          <h2>{project ? L('What should change in this scene?', '这个场景要做哪些改变？') : L('Describe the situation you want to study', '描述你想研究的情景')}</h2>
          <p>{project ? L(`I'll work from “${project.name}” and change only what you describe. You'll see a plan before anything is applied.`, `我会以“${project.name}”为基础，只改动你描述的部分。应用之前，你会先看到方案。`) : L('Say it in your own words — a rough idea is enough. I will suggest a concrete starting point, show you the plan and its assumptions, and apply nothing until you confirm.', '用你自己的话说就行，粗略的想法也可以。我会给出具体的起点，展示方案及其假设；在你确认之前不会应用任何改动。')}</p>
          <div className="as-starters">{(project ? STARTERS.project : STARTERS.none).map(([en, cn]) => <button key={en} onClick={() => submit(L(en, cn))} disabled={busy || needsConnection}>{L(en, cn)}</button>)}</div>
        </div>}
        {items.map(item => <Fragment key={item.key}>{render(item)}</Fragment>)}
        {busy && <div className="as-thinking" role="status" data-testid="as-thinking"><span className="as-dots"><i /><i /><i /></span><span>{running ? (zh ? running.label_zh : undefined) ?? running.label : L('Thinking…', '思考中…')}</span><button className="wb-btn small ghost" onClick={() => void assistant.cancel()}><Icon name="stop" size={11} />{L('Stop', '停止')}</button></div>}
        {error && <div className="as-note bad" role="alert"><Icon name="cross" size={13} /><span>{bi(error, zh)}</span></div>}
      </div>
      <footer className="as-foot as-rise">
        {!busy && suggestions.length > 0 && <div className="as-suggestions" data-testid="as-suggestions">{suggestions.map(value => <button key={value} onClick={() => submit(value)}>{value}</button>)}</div>}
        {selected && <div className="as-context"><Icon name="eye" size={12} />{L('Asking about', '正在询问')} <b>{selected.label}</b>{onClearSelected && <button className="wb-x" onClick={onClearSelected} aria-label={L('Stop asking about the selection', '不再针对所选对象')}>✕</button>}</div>}
        <form className={`as-compose${busy ? ' is-busy' : ''}`} onSubmit={event => { event.preventDefault(); submit(); }}>
          <textarea ref={input} rows={1} value={text} disabled={needsConnection} onChange={event => setText(event.target.value)} data-testid="as-input"
            placeholder={plan ? L('Ask, or describe a change…', '提问，或描述要做的修改…') : L('Describe what you want to study…', '描述你想研究的内容…')}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); } }} />
          <button type="submit" className="as-send" disabled={busy || !text.trim() || needsConnection} aria-label={L('Send', '发送')} data-testid="as-send"><Icon name="send" size={15} /></button>
        </form>
        <small className="as-fine">{L('Plans are proposals. Your scene changes only when you press Confirm.', '方案只是提议。只有按下“确认”后，场景才会改变。')}</small>
      </footer>
    </>}
  </aside>;
}

/** Put text in the composer from anywhere (assumption "Change…", "Ask why"). */
export function draftMessage(text: string) { dispatchEvent(new CustomEvent('aas-assistant-draft', { detail: text })); }
