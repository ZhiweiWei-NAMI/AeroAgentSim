import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { useI18n } from '../../i18n/I18nProvider';
import type { AssistantApi, PlanVersion } from '../api';
import type { DiffItem } from '../api';
import { bi } from '../present';
import { Icon } from '../icons';
import '../alternatives.css';

interface AltColumn { version: number; plan?: PlanVersion }

/**
 * Side-by-side review of the assistant's proposed alternatives (QA AST-015 / FE-09).
 * Read-only by construction: nothing here calls apply or choose; picking an
 * option only moves the plan board's review to that version — confirmation
 * stays on the existing explicit path.
 */
export function AlternativesCompare({ api, sessionId, versions, current, onSelect, onClose }: {
  api: AssistantApi; sessionId: string; versions: number[]; current?: number;
  onSelect: (version: number) => void; onClose: () => void;
}) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [columns, setColumns] = useState<AltColumn[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const control = new AbortController();
    setLoading(true); setError(undefined);
    void Promise.all(versions.map(async version => ({ version, plan: await api.plan(sessionId, version, control.signal) })))
      .then(rows => { if (!control.signal.aborted) { setColumns(rows); setLoading(false); } })
      .catch(problem => { if (!control.signal.aborted) { setError(problem instanceof Error ? problem.message : String(problem)); setLoading(false); } });
    return () => control.abort();
  }, [api, sessionId, versions.join(',')]);

  const pair = useMemo(() => columns.filter((column): column is AltColumn & { plan: PlanVersion } => !!column.plan), [columns]);
  const common = useMemo(() => commonItems(pair.map(column => column.plan)), [pair]);
  if (!versions.length) return null;
  return <section className="as-alt" data-testid="as-alternatives" aria-label={L('Alternative options side by side', '备选方案并排对比')}>
    <header className="as-alt-head">
      <h4>{L('Alternatives side by side', '备选方案并排对比')}</h4>
      <button className="wb-x" onClick={onClose} aria-label={L('Close the side-by-side comparison', '关闭并排对比')}>✕</button>
    </header>
    <p className="as-quiet">{L('Read-only review. Choosing an option only opens it for inspection; applying still needs “Confirm and apply”.', '只读对比。选择一项仅打开供查看；应用仍需“确认并应用”。')}</p>
    {loading && <p className="as-quiet" role="status"><span className="as-spin" />{L('Loading both versions…', '正在加载两个版本…')}</p>}
    {error && <p className="as-note bad" role="alert">{L('Could not load the alternatives for comparison:', '无法加载备选方案进行对比：')}{error}</p>}
    {!loading && !error && pair.length < 2 && <p className="as-quiet">{L('The alternatives are not compiled yet, so there is nothing to compare side by side.', '备选方案尚未编译完成，暂时无法并排对比。')}</p>}
    {pair.length >= 2 && <div className="as-alt-grid" style={{ '--as-alt-n': pair.length } as CSSProperties}>
      {pair.map(({ plan }) => <article key={plan.version} className={`as-alt-col${current === plan.version ? ' is-current' : ''}`} data-testid={`as-alt-${plan.version}`}>
        <header className="as-alt-col-head">
          <b>{plan.variant ? plan.variant : L('Option', '方案')} · v{plan.version}</b>
          <ValidationTag plan={plan} />
        </header>
        {plan.summary && <p className="as-alt-summary">{bi(plan.summary, zh)}</p>}
        <Group title={L('Shared with the other option(s)', '与其他方案相同')}>
          {common.length ? <ul className="as-alt-common">{common.map(item => <li key={item}>{item}</li>)}</ul>
            : <p className="as-quiet">{L('Everything differs — no shared items.', '全部不同——没有共同项。')}</p>}
        </Group>
        <Group title={L('Different in this option', '此方案的差异')}>
          <DeltaList items={plan.diff_previous?.added} sign="+" />
          <DeltaList items={plan.diff_previous?.changed} sign="~" />
          <DeltaList items={plan.diff_previous?.removed} sign="−" />
        </Group>
        {plan.assumptions.length > 0 && <Group title={L('Assumptions', '假设')}>
          <ul className="as-alt-common">{plan.assumptions.map(text => <li key={text}>{text}</li>)}</ul>
        </Group>}
        <footer className="as-alt-foot">
          <button className="wb-btn small" disabled={current === plan.version} onClick={() => onSelect(plan.version)} data-testid={`as-alt-review-${plan.version}`}>
            <Icon name="eye" size={12} />{current === plan.version ? L('Now under review', '正在查看') : L('Open for review', '打开查看')}
          </button>
          <span className="as-quiet">{L('Never applies on its own', '不会自动应用')}</span>
        </footer>
      </article>)}
    </div>}
  </section>;
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return <section className="as-alt-group"><h5>{title}</h5>{children}</section>;
}

function DeltaList({ items, sign }: { items?: DiffItem[]; sign: '+' | '~' | '−' }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN';
  if (!items?.length) return null;
  return <ul className="as-alt-deltas">{items.map(item => <li key={`${sign}${item.id}`} className={`as-delta is-${sign === '+' ? 'add' : sign === '~' ? 'change' : 'remove'}`}>
    <span className="as-delta-sign wb-mono">{sign}</span>
    <span>{(zh ? item.label_zh : undefined) ?? item.label}</span>
  </li>)}</ul>;
}

function ValidationTag({ plan }: { plan: PlanVersion }) {
  const { locale } = useI18n(), L = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const state = !plan.compile.ok ? 'invalid' : plan.validation.state;
  const text = state === 'valid' ? L('validated', '已校验') : state === 'pending' ? L('checking…', '校验中…') : L('rejected', '未通过');
  return <span className={`as-alt-tag is-${state}`}>{state === 'valid' ? <Icon name="check" size={10} /> : state === 'pending' ? <span className="as-spin" /> : <Icon name="cross" size={10} />}{text}</span>;
}

/**
 * Labels that every compared plan shares, drawn from the compiled scene.
 * Common = present with the same id in all versions; anything else is a difference.
 */
function commonItems(plans: PlanVersion[]): string[] {
  if (plans.length < 2) return [];
  const sceneOf = (plan: PlanVersion) => plan.scene;
  const first = sceneOf(plans[0]);
  if (!first) return [];
  const labels = (plan: PlanVersion) => {
    const scene = sceneOf(plan);
    if (!scene) return undefined;
    const rows: Array<[string, string | undefined]> = [
      ...(scene.places ?? []).map(place => [place.id, place.label_zh ?? place.label] as [string, string | undefined]),
      ...(scene.fleets ?? []).map(fleet => [fleet.id, fleet.label_zh ?? fleet.label] as [string, string | undefined]),
      ...(scene.workloads ?? []).map(workload => [workload.id, workload.label_zh ?? workload.label] as [string, string | undefined]),
      ...(scene.events ?? []).map(event => [event.id, event.label_zh ?? event.label] as [string, string | undefined]),
    ];
    return new Map(rows);
  };
  const maps = plans.map(labels);
  if (maps.some(map => map === undefined)) return [];
  const shared: string[] = [];
  const firstMap = maps[0] as Map<string, string | undefined>;
  for (const [id, label] of firstMap) if (maps.slice(1).every(map => (map as Map<string, string | undefined>).get(id) === label)) shared.push(label ?? id);
  return shared;
}

