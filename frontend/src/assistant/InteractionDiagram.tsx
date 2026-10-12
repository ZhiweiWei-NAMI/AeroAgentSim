import { Fragment, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { Interactions, Lane, Step } from './api';
import { Icon } from './icons';
import { bi, clock } from './present';

type Block = { type: 'step'; step: Step; index: number } | { type: 'alt'; group: string; sections: Array<{ when: string; steps: Array<{ step: Step; index: number }> }> };

/** Consecutive steps of one branch group share an "alt" frame with one section per condition. */
export function blocks(steps: Step[]): Block[] {
  const out: Block[] = [];
  steps.forEach((step, index) => {
    const previous = out.at(-1);
    if (!step.branch) { out.push({ type: 'step', step, index }); return; }
    if (previous?.type === 'alt' && previous.group === step.branch.group) {
      const section = previous.sections.find(row => row.when === step.branch!.when);
      if (section) section.steps.push({ step, index }); else previous.sections.push({ when: step.branch.when, steps: [{ step, index }] });
    } else out.push({ type: 'alt', group: step.branch.group, sections: [{ when: step.branch.when, steps: [{ step, index }] }] });
  });
  return out;
}

const PLACES = '__places';

/** Lanes that steps use; many passive places share one lane so actors stay legible in a narrow board. */
export function laneLayout(interactions: Interactions) {
  const used = new Set(interactions.steps.flatMap(step => [step.from, ...(step.to ? [step.to] : [])]));
  const lanes = interactions.lanes.filter(lane => used.has(lane.id)), places = lanes.filter(lane => lane.kind === 'place');
  if (places.length <= 2) return { lanes, lane: (id: string) => id, places: new Map<string, Lane>() };
  const merged: Lane[] = [];
  for (const lane of lanes) {
    if (lane.kind !== 'place') merged.push(lane);
    else if (lane === places[0]) merged.push({ id: PLACES, label: `Places · ${places.length}`, label_zh: `地点 · ${places.length}`, kind: 'place' });
  }
  const byId = new Map(places.map(lane => [lane.id, lane]));
  return { lanes: merged, lane: (id: string) => byId.has(id) ? PLACES : id, places: byId };
}

const WHEN: Record<string, [string, string]> = {
  accepted: ['if a courier accepts', '若配送员接受'], declined: ['if nobody accepts', '若无人接受'], accept: ['if it accepts', '若接受'], decline: ['if it declines', '若拒绝'],
  noticed: ['if noticed', '若被发现'], not_noticed: ['if nobody notices', '若无人发现'], low_battery: ['if battery is low', '若电量不足'],
};

export function InteractionDiagram({ interactions, onStep, compact = false }: { interactions: Interactions; onStep?: (lanes: string[], step?: Step) => void; compact?: boolean }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const layout = useMemo(() => laneLayout(interactions), [interactions]), lanes = layout.lanes;
  const column = useMemo(() => new Map(lanes.map((lane, index) => [lane.id, index])), [lanes]);
  const grouped = useMemo(() => blocks(interactions.steps), [interactions.steps]);
  const [current, setCurrent] = useState<number>(), [playing, setPlaying] = useState(false);
  const total = interactions.steps.length;
  useEffect(() => { setCurrent(undefined); setPlaying(false); }, [interactions]);
  useEffect(() => {
    if (!playing) return;
    const timer = setTimeout(() => {
      const next = current === undefined ? 0 : current + 1;
      if (next >= total) { setPlaying(false); setCurrent(undefined); onStep?.([]); return; }
      setCurrent(next);
    }, current === undefined ? 0 : 1500);
    return () => clearTimeout(timer);
  }, [playing, current, total]);
  useEffect(() => {
    const step = current === undefined ? undefined : interactions.steps[current];
    onStep?.(step ? [step.from, ...(step.to ? [step.to] : [])] : [], step);
  }, [current]);

  if (!lanes.length || !total) return <p className="as-quiet">{L('No interactions to show yet.', '暂无可展示的交互。')}</p>;
  const laneLabel = (lane: Lane) => (zh ? lane.label_zh : undefined) ?? lane.label;
  const row = ({ step, index }: { step: Step; index: number }) => {
    const from = column.get(layout.lane(step.from)) ?? 0, to = step.to ? column.get(layout.lane(step.to)) ?? from : from;
    const place = layout.places.get(step.to ?? '') ?? layout.places.get(step.from);
    const self = from === to, left = Math.min(from, to), span = Math.abs(to - from) + 1;
    const state = current === undefined ? '' : index === current ? ' is-current' : index < current ? ' is-past' : ' is-future';
    const kind = step.layer === 'setup' ? 'setup' : step.decided_by === 'agent' ? 'agent' : 'prescribed';
    return <li key={step.id} className={`as-seq-row is-${kind}${state}`} data-testid="as-seq-step"
      onClick={() => { setPlaying(false); setCurrent(index === current ? undefined : index); }}
      style={{ '--from': left, '--span': span } as CSSProperties}>
      <span className="as-seq-time wb-mono">{step.at_s === null || step.at_s === undefined ? '' : clock(step.at_s)}</span>
      <span className="as-seq-text">
        <i className="as-seq-badge" title={kind === 'setup' ? L('Setup: a background condition', '设定：背景条件') : kind === 'agent' ? L('Decided by the agent', '由智能体决定') : L('Prescribed rule', '规定的规则')}>{kind === 'setup' ? '◇' : <Icon name={kind === 'agent' ? 'spark' : 'rule'} size={11} />}</i>
        {(zh ? step.text_zh : undefined) ?? step.text}
        {place && <small className="as-seq-place"><Icon name="customer" size={9} />{laneLabel(place)}</small>}
      </span>
      <span className="as-seq-track" aria-hidden>
        {self ? <span className="as-seq-self" /> : <span className={`as-seq-arrow${to < from ? ' is-back' : ''}`}><b /></span>}
      </span>
    </li>;
  };
  return <div className={`as-seq${compact ? ' is-compact' : ''}`} style={{ '--lanes': lanes.length } as CSSProperties} data-testid="as-diagram">
    <div className="as-seq-controls">
      <button className="wb-btn small" onClick={() => { if (playing) setPlaying(false); else { setCurrent(undefined); setPlaying(true); } }} data-testid="as-seq-play"><Icon name={playing ? 'pause' : 'play'} size={11} />{playing ? L('Pause', '暂停') : L('Play the sequence', '播放流程')}</button>
      <span className="as-quiet">{current === undefined ? L(`${total} steps`, `${total} 步`) : `${current + 1} / ${total}`}</span>
      <span className="as-seq-legend"><span><i>◇</i>{L('setup', '设定')}</span><span><Icon name="rule" size={10} />{L('prescribed', '规定')}</span><span><Icon name="spark" size={10} />{L('agent decides', '智能体决定')}</span></span>
    </div>
    <div className="as-seq-scroll"><div className="as-seq-grid">
      <div className="as-seq-heads">{lanes.map(lane => <span key={lane.id} className={`as-seq-lane is-${lane.kind}`} title={laneLabel(lane)}><Icon name={lane.kind === 'person' ? 'incident' : lane.kind === 'place' ? 'customer' : lane.kind === 'system' ? 'layers' : 'drone'} size={12} /><em>{laneLabel(lane)}</em></span>)}</div>
      <div className="as-seq-lifelines" aria-hidden>{lanes.map(lane => <i key={lane.id} />)}</div>
      <ol className="as-seq-rows">
        {grouped.map(block => block.type === 'step' ? row(block) : <li key={`alt-${block.group}-${block.sections[0].steps[0].index}`} className="as-seq-alt">
          {block.sections.map((section, index) => <Fragment key={section.when}>
            <div className={`as-seq-alt-tag${index ? ' is-else' : ''}`}>{WHEN[section.when] ? L(...WHEN[section.when]) : bi(section.when, zh).replace(/_/g, ' ')}</div>
            <ol>{section.steps.map(row)}</ol>
          </Fragment>)}
        </li>)}
      </ol>
    </div></div>
  </div>;
}
