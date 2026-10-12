import type { ReactNode } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { Contract, Diff, Fleet, Policy, SceneSummary } from './api';
import { Icon } from './icons';
import { clock, constraintTexts, kindNoun, optionText, pick, placeRole, releaseText, valueText, type Lang } from './present';

export type Mark = 'new' | 'changed' | undefined;
export function marks(diff: Diff | undefined) {
  const added = new Set([...(diff?.added ?? []).map(row => row.id), ...(diff?.entities?.added ?? [])]);
  const changed = new Set([...(diff?.changed ?? []).map(row => row.id), ...(diff?.entities?.changed ?? [])]);
  return (id: string, members: string[] = []): Mark => added.has(id) || members.some(member => added.has(member)) ? 'new' : changed.has(id) || members.some(member => changed.has(member)) ? 'changed' : undefined;
}

function Badge({ mark, L }: { mark: Mark; L: Lang }) {
  return mark ? <span className={`as-mark is-${mark}`}>{mark === 'new' ? L('new', '新增') : L('changed', '修改')}</span> : null;
}

function ContractChip({ contract, onContract }: { contract?: Contract; onContract?: (type: string) => void }) {
  if (!contract?.type) return null;
  const chain = [...(contract.ancestry ?? []).slice(-2), contract.type].filter((item, index, list) => list.indexOf(item) === index);
  return <button className="as-contract" title={[...(contract.ancestry ?? []), contract.type].join(' › ')} onClick={event => { event.stopPropagation(); onContract?.(contract.type!); }} data-testid="as-contract">
    <Icon name="layers" size={10} />{chain.map((item, index) => <span key={item}>{index ? <i>›</i> : null}{item}</span>)}
  </button>;
}

/** One titled group of rows, HUD style: a mono caption, a hairline and a count. */
export function Section({ title, count, children, layer, testid }: { title: string; count?: number; children: ReactNode; layer?: 'setup' | 'behaviour'; testid?: string }) {
  return <section className={`as-section${layer ? ` is-${layer}` : ''}`} data-testid={testid}>
    <h4><span>{title}</span><i />{count !== undefined && <em className="wb-mono">{String(count).padStart(2, '0')}</em>}</h4>
    {children}
  </section>;
}

function Row({ icon, title, meta, mark, onHover, children, testid }: { icon: string; title: ReactNode; meta?: ReactNode; mark?: Mark; onHover?: (on: boolean) => void; children?: ReactNode; testid?: string }) {
  return <div className={`as-card${mark ? ` is-${mark}` : ''}`} data-anchor onMouseEnter={() => onHover?.(true)} onMouseLeave={() => onHover?.(false)} data-testid={testid}>
    <span className="as-card-icon"><Icon name={icon} size={15} /></span>
    <div className="as-card-main"><div className="as-card-title">{title}</div>{meta && <div className="as-card-meta">{meta}</div>}{children}</div>
  </div>;
}

function fleetLine(fleet: Fleet, scene: SceneSummary, L: Lang, zh: boolean) {
  const start = fleet.start?.place ? scene.places?.find(place => place.id === fleet.start!.place) : undefined;
  return [
    fleet.task_kinds?.length ? fleet.task_kinds.map(kind => kindNoun(kind, 2, L)).join(' · ') : undefined,
    start ? L(`starts at ${pick(start, zh)}`, `从${pick(start, zh)}出发`) : fleet.start?.label,
    typeof fleet.battery_pct === 'number' ? L(`${fleet.battery_pct}% battery`, `电量 ${fleet.battery_pct}%`) : undefined,
    typeof fleet.payload_capacity_kg === 'number' ? `${fleet.payload_capacity_kg} kg` : undefined,
  ].filter(Boolean).join(' · ');
}

/** A behaviour: who decides, what is allowed, what is always enforced. */
function Responsibility({ who, when, policy, kind, onMode, busy, L, testid }: { who: string; when: string; policy: Policy; kind: 'responder' | 'helper'; onMode?: (mode: 'prescribed' | 'agent') => void; busy?: boolean; L: Lang; testid: string }) {
  const agent = policy.mode === 'agent', constraints = constraintTexts(policy, L);
  const options = kind === 'helper' ? ['accept', 'decline'] : policy.order ?? [];
  return <div className={`as-duty is-${agent ? 'agent' : 'prescribed'}`} data-anchor data-testid={testid}>
    <div className="as-duty-head">
      <div><strong>{who}</strong><small>{when}</small></div>
      <div className="as-duty-mode" role="radiogroup" aria-label={L('Who decides', '由谁决定')}>
        <button role="radio" aria-checked={!agent} disabled={busy || !agent} onClick={() => onMode?.('prescribed')} data-testid={`${testid}-prescribed`}><Icon name="rule" size={11} />{L('Prescribed', '规定')}</button>
        <button role="radio" aria-checked={agent} disabled={busy || agent} onClick={() => onMode?.('agent')} data-testid={`${testid}-agent`}><Icon name="spark" size={11} />{L('Agent decides', '智能体决定')}</button>
      </div>
    </div>
    {agent ? <div className="as-duty-body">
      {policy.goal && <p className="as-goal">“{policy.goal}”</p>}
      {options.length > 0 && <p>{L('Chooses one of:', '可选择：')} {options.map(option => <span key={option} className="as-option">{optionText(option, L)}</span>)}</p>}
    </div> : <div className="as-duty-body">
      {kind === 'helper' ? <p>{L('Answers by rule.', '按规则回复。')}</p> : options.length > 0 && <ol className="as-order">{options.map(option => <li key={option}>{optionText(option, L)}</li>)}</ol>}
    </div>}
    {constraints.length > 0 && <ul className="as-constraints" title={L('Always enforced by the simulator, whoever decides', '无论由谁决定，仿真器始终强制执行')}>{constraints.map(text => <li key={text}><Icon name="rule" size={10} />{text}</li>)}</ul>}
  </div>;
}

export function SceneCards({ scene, diff, onHover, onContract, onMode, busy }: {
  scene: SceneSummary; diff?: Diff; onHover?: (id: string | undefined) => void; onContract?: (type: string) => void;
  onMode?: (text: string) => void; busy?: boolean;
}) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L: Lang = (en, cn) => zh ? cn : en;
  const mark = marks(diff), hover = (id: string) => (on: boolean) => onHover?.(on ? id : undefined);
  const places = scene.places ?? [], fleets = scene.fleets ?? [], workloads = scene.workloads ?? [], events = scene.events ?? [];
  const coop = scene.cooperation ?? undefined, wind = scene.environment?.wind;
  const name = (id: string) => { const place = places.find(row => row.id === id); return place ? pick(place, zh) : id; };
  const fleetName = (vehicles: string[] | undefined) => {
    const names = [...new Set((vehicles ?? []).map(vehicle => { const fleet = fleets.find(row => row.id === vehicle || row.members.includes(vehicle)); return fleet ? pick(fleet, zh) : vehicle; }))];
    return names.join(L(' and ', '和')) || L('Any UAV', '任意无人机');
  };
  const incidents = coop?.incidents ?? [];
  const duties = coop ? [
    coop.responder && { kind: 'responder' as const, who: fleetName(coop.watchers?.vehicles), policy: coop.responder,
      when: L(`when it notices an incident within ${coop.watchers?.notice_radius_m ?? '—'} m`, `在 ${coop.watchers?.notice_radius_m ?? '—'} 米内发现事件时`) },
    coop.helpers && { kind: 'helper' as const, who: fleetName(coop.helpers.vehicles), policy: coop.helpers,
      when: L(`when asked to take over a delivery within ${coop.helpers.ask_radius_m ?? '—'} m`, `在 ${coop.helpers.ask_radius_m ?? '—'} 米内被请求接手配送时`) },
  ].filter(Boolean) as Array<{ kind: 'responder' | 'helper'; who: string; when: string; policy: Policy }> : [];
  const modeText = (duty: (typeof duties)[number], mode: 'prescribed' | 'agent') => duty.kind === 'responder'
    ? mode === 'agent' ? L(`Let the ${duty.who} decide what to do when it notices an incident.`, `发现事件时，让${duty.who}自己决定怎么做。`) : L(`Make the ${duty.who} follow fixed rules when it notices an incident.`, `发现事件时，让${duty.who}按固定规则行动。`)
    : mode === 'agent' ? L(`Let the ${duty.who} decide whether to accept a handoff.`, `让${duty.who}自己决定是否接手。`) : L(`Make the ${duty.who} answer handoff requests by a fixed rule.`, `让${duty.who}按固定规则回应接手请求。`);
  const algorithms = Object.entries(scene.algorithms ?? {});

  return <div className="as-cards" data-testid="as-scene">
    <div className="as-layer-head is-setup"><span className="as-layer-glyph">◇</span><div><strong>{L('Setup', '设定')}</strong><small>{L('What the world is: places, fleets, orders and what happens around them.', '世界本身：地点、机队、订单，以及周围会发生的事。')}</small></div></div>

    <Section title={L('Environment', '环境')} layer="setup">
      <div className="as-env">
        {scene.city && <span><Icon name="globe" size={12} />{scene.city}</span>}
        {scene.duration_s !== undefined && <span><Icon name="clock" size={12} />{clock(scene.duration_s)}</span>}
        {wind && <span><Icon name="wind" size={12} />{L(`${valueText(wind.speed_mps ?? 0)} m/s wind`, `风速 ${valueText(wind.speed_mps ?? 0)} 米/秒`)}</span>}
        {wind?.gusts?.map((gust, index) => <span key={index} className={mark('wind') ? 'is-new' : ''}><Icon name="wind" size={12} />{L(`gust ${gust.speed_mps} m/s at ${clock(gust.start_s)}–${clock(gust.end_s)}`, `${clock(gust.start_s)}–${clock(gust.end_s)} 阵风 ${gust.speed_mps} 米/秒`)}</span>)}
        {scene.run_mode && <span><Icon name="play" size={12} />{scene.run_mode === 'interactive' ? L('interactive', '交互式') : L('scheduled', '按计划')}</span>}
      </div>
    </Section>

    {fleets.length > 0 && <Section title={L('Participants', '参与者')} count={fleets.reduce((sum, fleet) => sum + fleet.count, 0)} layer="setup" testid="as-fleets">
      {fleets.map(fleet => <Row key={fleet.id} icon="drone" mark={mark(fleet.id, fleet.members)} onHover={hover(fleet.id)} testid={`as-fleet-${fleet.id}`}
        title={<>{fleet.count > 1 && <b className="as-count">{fleet.count}×</b>}{pick(fleet, zh)}<Badge mark={mark(fleet.id, fleet.members)} L={L} /></>}
        meta={fleetLine(fleet, scene, L, zh)}><ContractChip contract={fleet.contract} onContract={onContract} /></Row>)}
    </Section>}

    {places.length > 0 && <Section title={L('Places', '地点')} count={places.length} layer="setup" testid="as-places">
      <div className="as-place-grid">{places.map(place => { const m = mark(place.id); return <button key={place.id} className={`as-place${m ? ` is-${m}` : ''}`} data-anchor onMouseEnter={() => onHover?.(place.id)} onMouseLeave={() => onHover?.(undefined)} onFocus={() => onHover?.(place.id)} onBlur={() => onHover?.(undefined)} title={place.contract?.type}>
        <Icon name={place.role} size={13} /><span>{pick(place, zh)}<small>{placeRole(place.role, L)}</small></span>{m && <i className={`as-dot is-${m}`} />}
      </button>; })}</div>
    </Section>}

    {workloads.length > 0 && <Section title={L('Tasks', '任务')} count={workloads.reduce((sum, row) => sum + row.count, 0)} layer="setup" testid="as-tasks">
      {workloads.map(workload => <Row key={workload.id} icon={workload.kind} mark={mark(workload.id, workload.tasks)} onHover={hover(workload.id)}
        title={<><b className="as-count">{workload.count}</b>{kindNoun(workload.kind, workload.count, L)}{workload.protected && <span className="as-mark is-protected">{L('protected', '受保护')}</span>}<Badge mark={mark(workload.id, workload.tasks)} L={L} /></>}
        meta={<>{workload.from.slice(0, 2).map(name).join(', ')}{workload.from.length > 2 ? ` +${workload.from.length - 2}` : ''} <span className="as-arrow">→</span> {workload.to.slice(0, 2).map(name).join(', ')}{workload.to.length > 2 ? ` +${workload.to.length - 2}` : ''}{workload.release ? ` · ${releaseText(workload.release, L)}` : ''}</>} />)}
    </Section>}

    {(events.length > 0 || incidents.length > 0) && <Section title={L('Background events', '背景事件')} count={events.length + incidents.filter(incident => !events.some(event => event.id === incident.id)).length} layer="setup" testid="as-events">
      <ol className="as-timeline">
        {/* Cooperation incidents are also scene events; list each once. */}
        {[...incidents.filter(incident => !events.some(event => event.id === incident.id)).map(incident => ({ id: incident.id, at: incident.at_s, text: pick(incident, zh) || incident.kind.replace(/_/g, ' '), icon: 'incident', incident: true })),
          ...events.map(event => { const incident = incidents.some(row => row.id === event.id); return { id: event.id, at: event.at_s, text: pick(event, zh), icon: incident ? 'incident' : event.kind.includes('wind') || event.kind.includes('gust') ? 'wind' : 'clock', incident }; })]
          .sort((a, b) => (a.at ?? 0) - (b.at ?? 0)).map(row => { const m = mark(row.id); return <li key={row.id} className={`${row.incident ? 'is-incident' : ''}${m ? ` is-${m}` : ''}`} data-anchor onMouseEnter={() => onHover?.(row.id)} onMouseLeave={() => onHover?.(undefined)}>
            <time className="wb-mono">{clock(row.at)}</time><Icon name={row.icon} size={12} /><span>{row.text}</span><Badge mark={m} L={L} />
          </li>; })}
      </ol>
    </Section>}

    <div className="as-layer-head is-behaviour"><span className="as-layer-glyph"><Icon name="spark" size={12} /></span><div><strong>{L('Behaviour', '行为')}</strong><small>{L('What agents notice, decide and do. Each one is a prescribed rule or decided by the agent.', '智能体发现、决定和执行的事。每项要么是规定的规则，要么由智能体决定。')}</small></div></div>

    {duties.length > 0 ? <Section title={L('Responsibilities', '职责')} count={duties.length} layer="behaviour" testid="as-duties">
      {duties.map(duty => <Responsibility key={duty.kind} {...duty} L={L} busy={busy} testid={`as-duty-${duty.kind}`} onMode={mode => onMode?.(modeText(duty, mode))} />)}
      {coop?.handoff_points?.length ? <p className="as-quiet"><Icon name="handoff" size={11} /> {L('Handoffs happen at', '交接地点：')} {coop.handoff_points.map(point => pick(point, zh) || point.id).join(', ')}{coop.photograph?.dwell_s ? L(` · the responder photographs for ${coop.photograph.dwell_s} s`, ` · 响应者拍摄 ${coop.photograph.dwell_s} 秒`) : ''}</p> : null}
    </Section> : <p className="as-quiet as-indent">{L('Agents follow their usual task rules: take the next order, fly, deliver, recharge.', '智能体按常规任务规则行动：接单、飞行、送达、充电。')}</p>}

    {((scene.rules?.length ?? 0) > 0 || algorithms.length > 0) && <Section title={L('Rules and decision methods', '规则与决策方法')} layer="behaviour" testid="as-rules">
      <dl className="as-rules">
        {scene.rules?.map(rule => <div key={rule.id} className={mark(rule.id) ? `is-${mark(rule.id)}` : ''}><dt>{pick(rule, zh)}</dt><dd className="wb-mono">{valueText(rule.value, rule.unit)}</dd></div>)}
        {algorithms.map(([slot, value]) => <div key={slot} className={mark(`algorithm:${slot}`) ? `is-${mark(`algorithm:${slot}`)}` : ''}><dt>{slot.replace(/_/g, ' ')}</dt><dd className="wb-mono">{value === 'llm' ? <><Icon name="spark" size={10} /> {L('agent (LLM)', '智能体（LLM）')}</> : value}</dd></div>)}
      </dl>
    </Section>}
  </div>;
}
