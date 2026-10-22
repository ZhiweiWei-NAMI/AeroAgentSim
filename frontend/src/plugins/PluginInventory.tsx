import { useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { PluginInventory as Inventory, PluginRow, ScenarioPlugin } from './api';
import './plugins.css';

/** `status` may be absent on older servers; grouped names still expose duplicates. */
function conflictOf<Row extends PluginRow>(row: Row, rows: Row[]): { duplicate: boolean; names: string[] } {
  const sameName = rows.filter(candidate => candidate.name === row.name);
  if (row.status !== 'duplicate' && row.conflict.length === 0 && sameName.length < 2) return { duplicate: false, names: [] };
  const names = [...new Set([...row.conflict, ...sameName.map(candidate => candidate.distribution)]
    .filter((name): name is string => typeof name === 'string' && name.length > 0))];
  return { duplicate: true, names };
}

function duplicatesIn<Row extends PluginRow>(rows: Row[]): string[] {
  return [...new Set(rows.filter(row => conflictOf(row, rows).duplicate).map(row => row.name))];
}

function Contribution({ label, values, empty, unknown, zh }: { label: string; values: string[]; empty: string; unknown: boolean; zh: boolean }) {
  const L = (en: string, cn: string) => zh ? cn : en;
  return <div className="wb-plugin-contrib">
    <span className="wb-plugin-label">{label}</span>
    <span className="wb-plugin-tags">
      {unknown ? <span className="hint">{L('unknown (duplicate names)', '未知（名称重复）')}</span>
        : values.length ? values.map(value => <span key={value} className="wb-tag wb-mono">{value}</span>)
        : <span className="hint">{empty}</span>}
    </span>
  </div>;
}

function ScenarioRow({ row, rows, zh }: { row: ScenarioPlugin; rows: ScenarioPlugin[]; zh: boolean }) {
  const L = (en: string, cn: string) => zh ? cn : en;
  const conflict = conflictOf(row, rows);
  // A duplicate scenario name makes discovery fall back to entry-point metadata,
  // so empty contributions are unknown rather than proof of a hook-only plugin.
  const degraded = rows.some(candidate => conflictOf(candidate, rows).duplicate);
  const unknown = degraded && row.templates.length === 0 && row.apply_hooks.length === 0;
  return <li className="wb-plugin-row" data-testid={`wb-plugin-scenario-${row.name}`}>
    <div className="wb-plugin-name">
      <strong>{row.name}</strong>
      <span className="wb-tag">{row.origin === 'builtin' ? L('built-in', '内置') : L('entry point', '入口点')}</span>
      {row.distribution && <span className="wb-tag info" title={L('Distribution', '发行包')}>{row.distribution}</span>}
      {conflict.duplicate
        ? <span className="wb-tag warn">{L('duplicate name', '名称重复')}</span>
        : <span className="wb-tag ok">{L('discovered', '已发现')}</span>}
    </div>
    {conflict.duplicate && <p className="hint wb-plugin-warning">{conflict.names.length
      ? L(`Declared by ${conflict.names.join(', ')}. Discovery rejects duplicated names; contributions may be unavailable.`, `声明方：${conflict.names.join('、')}。发现过程会拒绝重名插件，贡献可能不可用。`)
      : L('Discovery rejects duplicated installed names; contributions may be unavailable.', '发现过程会拒绝已安装的重名插件，贡献可能不可用。')}</p>}
    <Contribution label={L('Templates', '模板')} values={row.templates} empty={row.apply_hooks.length ? L('none (hook-only)', '无（仅提供 hook）') : L('none', '无')} unknown={unknown} zh={zh} />
    <Contribution label={L('Hooks', 'Hook')} values={row.apply_hooks} empty={L('none', '无')} unknown={unknown} zh={zh} />
  </li>;
}

function Catalog({ title, rows, testId, zh }: { title: string; rows: PluginRow[]; testId: string; zh: boolean }) {
  const [open, setOpen] = useState(false);
  const L = (en: string, cn: string) => zh ? cn : en;
  const duplicates = duplicatesIn(rows);
  return <div className="wb-plugin-catalog">
    <button type="button" className="wb-plugin-catalog-toggle" aria-expanded={open} data-testid={testId} onClick={() => setOpen(value => !value)}>
      <span>{title}</span><span className="wb-tag">{rows.length}</span>
      {duplicates.length > 0 && <span className="wb-tag warn">{L('duplicate', '重复')}</span>}
      <span className="wb-plugin-chevron" aria-hidden="true">{open ? '−' : '+'}</span>
    </button>
    {open && (rows.length ? <ul>
      {rows.map((row, index) => {
        const conflict = conflictOf(row, rows);
        return <li key={`${row.name}:${index}`}>
          <span className="wb-mono">{row.name}</span>
          <span className="wb-tag">{row.origin === 'builtin' ? L('built-in', '内置') : L('entry point', '入口点')}</span>
          {row.distribution && <span className="wb-tag info" title={L('Distribution', '发行包')}>{row.distribution}</span>}
          {conflict.duplicate && <span className="wb-tag warn" title={conflict.names.join(', ')}>{L('duplicate name', '名称重复')}</span>}
        </li>;
      })}
    </ul> : <p className="hint">{L('None discovered.', '未发现。')}</p>)}
  </div>;
}

/** Compact, read-only view of `GET /v1/plugins`; it never claims a scenario is runnable. */
export function PluginInventory({ inventory, onClose }: { inventory: Inventory; onClose?: () => void }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const duplicates = duplicatesIn(inventory.scenarios);
  return <section className="wb-plugins" data-testid="wb-plugin-inventory" aria-label={L('Installed plugins', '已安装插件')}>
    <div className="wb-plugins-head">
      <div>
        <div className="wb-section-title">{L('Installed plugins', '已安装插件')}</div>
        <p className="hint">{L(
          'Discovered from /v1/plugins. Inventory describes installation only; it does not validate or run a scenario. Template badges match by template id, and a configured template root can register the same id.',
          '来自 /v1/plugins 的发现结果。清单只说明安装情况，不验证也不运行场景。模板徽标按模板 ID 匹配，配置的模板根目录也可能注册相同 ID。',
        )}</p>
      </div>
      {onClose && <button type="button" className="wb-btn" onClick={onClose}>{L('Close', '关闭')}</button>}
    </div>
    {duplicates.length > 0 && <p className="wb-issues" role="alert">{L(
      `Duplicate scenario plugin names: ${duplicates.join(', ')}. Discovery rejects them, so their contributions may be unavailable.`,
      `场景插件名称重复：${duplicates.join('、')}。发现过程会拒绝这些插件，其贡献可能不可用。`,
    )}</p>}
    {inventory.scenarios.length
      ? <ul className="wb-plugin-list">{inventory.scenarios.map((row, index) => <ScenarioRow key={`${row.name}:${index}`} row={row} rows={inventory.scenarios} zh={zh} />)}</ul>
      : <div className="wb-empty">{L('No scenario plugins discovered.', '未发现场景插件。')}</div>}
    <div className="wb-plugin-catalogs">
      <Catalog title={L('Engines', '引擎')} rows={inventory.engines} testId="wb-plugin-catalog-engines" zh={zh} />
      <Catalog title={L('Algorithms', '算法')} rows={inventory.algorithms} testId="wb-plugin-catalog-algorithms" zh={zh} />
      <Catalog title={L('Models', '模型')} rows={inventory.models} testId="wb-plugin-catalog-models" zh={zh} />
    </div>
  </section>;
}
