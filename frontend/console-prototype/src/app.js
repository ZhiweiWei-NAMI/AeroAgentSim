import { createGraphWorkbench } from './graph-workbench.js';
import { t, html, message, getLocale, setLocale } from './i18n.js';
import { STUDY_PROFILES, STUDY_FIELD_METADATA, STUDY_SOURCES, STUDY_EVIDENCE_CLASSES, studyFieldRows, deriveStudyQuantities, applyStudyProfile } from './network-study.js';
import { DEFAULT_CONFIG, clone, validateConfig, diffConfig, compileConfig } from './config.js';
import { ADAPTERS, loadWorkspace, saveWorkspace, createVersion, safeImport, exportConfig, createFixtureRun, seekFrame, selectionKey, semanticEvidence, expandedEntities, exportObservations } from './runtime.js';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
})[c]);
let storageUnavailable = false;
let workspaceStorage;
try {
  workspaceStorage = localStorage;
} catch {
  storageUnavailable = true;
  const memory = new Map();
  workspaceStorage = {
    getItem: key => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, value)
  };
}
const stored = loadWorkspace(workspaceStorage, DEFAULT_CONFIG);
if (storageUnavailable) stored.warning = t('浏览器禁用了本地存储，当前仅在内存中编辑；请导出配置。');
const state = {
  ...stored,
  page: 'configuration',
  tab: 'scenario',
  selectedRun: stored.runs.find(run => run.id === stored.view?.run_id && run.epoch === stored.view?.run_epoch && String(run.manifest_revision) === stored.view?.manifest_revision)?.id || stored.runs[0]?.id || null,
  cursor: stored.runs[0]?.frames[0]?.relative_time_s ?? 0,
  selectedEntity: stored.view?.entity_id || null,
  selectionLocked: stored.view?.locked === true,
  playing: false,
  showValidation: false,
  filter: '',
  compare: stored.versions.at(-1)?.id || '',
  storageBlocked: storageUnavailable,
  recoveryNotice: stored.warning
};
const restoredRun = state.runs.find(run => run.id === state.selectedRun);
if (restoredRun && stored.view?.run_id === restoredRun.id && stored.view?.run_epoch === restoredRun.epoch && stored.view?.manifest_revision === String(restoredRun.manifest_revision)) {
  if (Number.isFinite(stored.view.cursor_s) && seekFrame(restoredRun, stored.view.cursor_s)) state.cursor = stored.view.cursor_s;
  if (!restoredRun.frames.some(frame => frame.entities.some(entity => entity.entity_id === state.selectedEntity))) {
    state.selectedEntity = null;
    state.selectionLocked = false;
  }
} else {
  state.selectedEntity = null;
  state.selectionLocked = false;
}
let playback = null;
let modalRefresh = null;
let modalReturnFocus = null;
const icon = (name, size = 18) => {
  const shapes = {
    config: '<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
    runs: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="m10 8 5 4-5 4z"/>',
    replay: '<path d="m9 7 8 5-8 5z"/><circle cx="12" cy="12" r="10"/>',
    versions: '<path d="M5 4v12a4 4 0 0 0 4 4h10M5 8h8a4 4 0 0 0 4-4"/><circle cx="5" cy="4" r="2"/><circle cx="17" cy="4" r="2"/><circle cx="19" cy="20" r="2"/>',
    adapter: '<path d="M8 3v4m8-4v4M6 7h12v4a6 6 0 0 1-12 0zM12 17v4"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    save: '<path d="M5 3h13l3 3v15H3V3zM7 3v6h10V3M7 21v-8h10v8"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    cube: '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10"/>',
    close: '<path d="m5 5 14 14M5 19 19 5"/>',
    file: '<path d="M6 2h8l5 5v15H5V2h1M14 2v6h6M8 13h8M8 17h8"/>',
    uav: '<path d="m3 5 18 14M21 5 3 19M12 3v18M7 12h10"/>',
    link: '<path d="M10 14 14 10M8 16l-2 2a4 4 0 0 1-6-6l6-6a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 1 6 6l-6 6a4 4 0 0 1-6 0"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/>'
  };
  return html`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name] || shapes.cube}</svg>`;
};
const nav = [['configuration', 'config', t('仿真配置')], ['runs', 'runs', t('运行管理')], ['replay', 'replay', t('回放与证据')], ['versions', 'versions', t('配置版本')], ['adapters', 'adapter', t('适配器与来源')]];
const tabs = [['graph', t('统一配置图')], ['scenario', t('场景与时钟')], ['entities', t('实体编排')], ['mobility', t('移动模型')], ['network', t('网络配置')], ['compute', t('计算资源')], ['semantics', t('语义绑定')]];
const types = {
  uav: t('无人机'),
  vehicle: t('地面车辆'),
  pedestrian: t('行人'),
  base_station: t('基站'),
  edge: t('边缘节点'),
  cloud: t('云节点')
};
const providers = {
  'local-demo': t('本地合成示例'),
  sumo: t('SUMO · 待连接'),
  static: t('静态实体'),
  ns3: t('ns-3 · 待连接')
};
let rendering = false,
  switchingLanguage = false;
function pathGet(path, obj = state.draft) {
  return path.split('.').reduce((o, k) => o?.[k], obj);
}
let authoringCache = null;
function invalidateAuthoring() { authoringCache = null; graphWorkbench.invalidate(); }
function authoringReview() {
  if (!authoringCache || authoringCache.draft !== state.draft || authoringCache.version !== state.versions.at(-1)) {
    authoringCache = { draft: state.draft, version: state.versions.at(-1), validation: validateConfig(state.draft), changes: state.versions.length ? diffConfig(state.versions.at(-1).config, state.draft).length : 0 };
  }
  return authoringCache;
}
const graphWorkbench = createGraphWorkbench({
  getConfig: () => state.draft,
  commitGraph(graph) { state.draft.graph = graph; invalidateAuthoring(); persist(); render(); },
  requestRender: render,
  getSelection: () => ({ entityId: state.selectedEntity, locked: state.selectionLocked, cursor: state.cursor }),
  selectEntity(id) { if (state.selectionLocked && state.selectedEntity !== id) return; state.selectedEntity = id; persist(); render(); }
});
function pathSet(path, value) {
  const keys = path.split('.');
  let target = state.draft;
  for (const key of keys.slice(0, -1)) target = target[key];
  if (target[keys.at(-1)] !== value) invalidateAuthoring();
  target[keys.at(-1)] = value;
}
function input(path, label, {
  type = 'text',
  unit = '',
  hint = '',
  min,
  step = 'any',
  disabled = false
} = {}) {
  const value = pathGet(path);
  const id = 'field-' + path.replaceAll('.', '-');
  return html`<div class="field"><label for="${id}">${t(label)}</label><div class="input-unit"><input id="${id}" data-path="${path}" type="${type}" value="${esc(value)}" ${min !== undefined ? html`min="${min}"` : ''} ${type === 'number' ? html`step="${step}"` : ''} ${disabled ? 'disabled' : ''}>${unit ? html`<span>${unit}</span>` : ''}</div>${hint ? html`<div class="field-hint">${hint}</div>` : ''}</div>`;
}
function select(path, label, options, hint = '') {
  const id = 'field-' + path.replaceAll('.', '-');
  return html`<div class="field"><label for="${id}">${t(label)}</label><select id="${id}" data-path="${path}">${options.map(o => {
    const [value, text] = Array.isArray(o) ? o : [o, o];
    return html`<option value="${esc(value)}" ${String(pathGet(path)) === String(value) ? 'selected' : ''}>${esc(t(text))}</option>`;
  }).join('')}</select>${hint ? html`<div class="field-hint">${hint}</div>` : ''}</div>`;
}
function toggle(path, label, hint) {
  return html`<label class="toggle-line"><span><strong>${t(label)}</strong>${hint ? html`<small>${hint}</small>` : ''}</span><input type="checkbox" data-path="${path}" ${pathGet(path) ? 'checked' : ''}><span class="toggle-track" aria-hidden="true"></span></label>`;
}
function panel(title, description, body, action = '') {
  return html`<section class="panel"><div class="panel-header"><div><h2 class="panel-title">${title}</h2>${description ? html`<p class="panel-description">${description}</p>` : ''}</div>${action}</div><div class="panel-body">${body}</div></section>`;
}
function badge(text, color = 'muted') {
  return html`<span class="badge ${color}">${text}</span>`;
}
function button(text, action, variant = '', extra = '') {
  return html`<button class="button ${variant}" data-action="${action}" ${extra}>${text}</button>`;
}
function persist() {
  try {
    saveWorkspace(workspaceStorage, {
      draft: state.draft,
      versions: state.versions,
      runs: state.runs,
      view: state.selectedRun ? {
        run_id: state.selectedRun,
        run_epoch: activeRun()?.epoch,
        manifest_revision: String(activeRun()?.manifest_revision),
        cursor_s: state.cursor,
        entity_id: state.selectedEntity,
        locked: state.selectionLocked
      } : null
    });
    state.storageBlocked = storageUnavailable;
    state.recoveryNotice = null;
  } catch {
    state.storageBlocked = true;
    toast(t('浏览器存储已满或不可用。请导出配置备份。'), 'error');
  }
}
function toast(text, type = '') {
  const el = $('#toast');
  el.className = 'toast ' + type;
  el.textContent = message(text);
  el.dataset.message = text;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add('hidden'), 4000);
}
function render() {
  if (rendering) return;
  rendering = true;
  try {
    history.replaceState(null, '', '#' + state.page);
    const { validation, changes } = authoringReview();
    $('#app').innerHTML = html`<div class="app-shell"><aside class="sidebar"><a class="brand" href="#configuration"><span class="brand-icon">${icon('uav', 24)}</span><span><span class="brand-name">AeroAgentSim</span><span class="brand-subtitle">SIMULATION CONSOLE</span></span></a><div class="project-switch"><span class="project-letter">A</span><span>低空协同仿真<span class="brand-subtitle">Local workspace</span></span><span class="subtle">⌄</span></div><div class="nav-group-label">工作空间</div><nav>${nav.map(([id, i, label]) => html`<button class="nav-button ${state.page === id ? 'active' : ''}" data-nav="${id}" aria-label="${t(label)}"><span class="nav-icon">${icon(i)}</span><span class="nav-label">${t(label)}</span>${id === 'runs' && state.runs.length ? html`<span class="nav-count">${state.runs.length}</span>` : ''}${id === 'versions' && state.versions.length ? html`<span class="nav-count">${state.versions.length}</span>` : ''}</button>`).join('')}</nav><div class="sidebar-bottom"><div class="sidebar-note"><span class="status-dot"></span>本地 Fixture 工作区<p>配置可保存，示例可回放<br>外部仿真服务未连接</p></div><div class="sidebar-footer"><span class="avatar">AS</span><span>Integration workspace<small>配置契约 v1.0</small></span></div></div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <strong>${t(nav.find(x => x[0] === state.page)?.[2])}</strong></div><div class="topbar-actions">${languagePicker()}<span class="mode-pill"><span class="status-dot"></span> Fixture mode</span><span class="topbar-divider"></span><span class="subtle">AeroAgentSim × BENCH × Atlas</span><button class="icon-button" data-action="help" aria-label="查看模式说明">${icon('info')}</button></div></header><main class="page"><div class="page-heading"><div><div class="eyebrow">${t(state.page === 'configuration' ? 'CONFIGURATION WORKSPACE' : state.page === 'runs' ? 'RUN LIFECYCLE' : state.page === 'replay' ? 'SYNCHRONIZED EVIDENCE' : state.page === 'versions' ? 'VERSION CONTROL' : 'INTEGRATION CONTRACTS')}</div><h1>${state.page === 'configuration' ? t('仿真配置') : t(nav.find(x => x[0] === state.page)?.[2])} ${state.page === 'configuration' ? badge(t('草稿'), 'blue') : ''}</h1><p class="page-description">${{
      configuration: t('从场景、实体到语义绑定，构建一份可追溯的仿真配置。'),
      runs: t('冻结配置快照，准备合成回放；真实运行由 BENCH 统一控制。'),
      replay: t('场景、实体、状态与语义证据共用同一个回放游标。'),
      versions: t('保存不可变配置快照，比较变更并恢复工作草稿。'),
      adapters: t('明确每个系统的权责、数据来源与尚未接通的能力。')
    }[state.page]}</p></div><div class="heading-actions">${state.page === 'configuration' ? html`${button(icon('download') + t(' 导出'), 'export', '')}${button(icon('save') + t(' 保存版本'), 'save', '')}${button(t('校验配置'), 'validate', '')}${button(icon('replay') + t(' 准备示例运行'), 'prepare', 'primary', !validation.valid ? 'disabled' : '')}` : state.page === 'versions' ? html`${button(t('导入配置'), 'import')}${button(icon('save') + t(' 保存当前版本'), 'save', 'primary')}` : state.page === 'runs' ? button(icon('plus') + t(' 准备示例运行'), 'prepare', 'primary', !validation.valid ? 'disabled' : '') : ''}</div></div>${state.storageBlocked ? t('<div class="notice warning">本地存储不可用。请先导出配置；本次编辑仍可使用。</div>') : ''}${state.recoveryNotice ? html`<div class="notice warning">${esc(message(state.recoveryNotice))}</div>` : ''}${state.page === 'configuration' ? configurationPage(validation, changes) : state.page === 'runs' ? runsPage() : state.page === 'replay' ? replayPage() : state.page === 'versions' ? versionsPage() : adaptersPage()}</main><footer class="workspace-footer"><span>唯一物理时钟：AERO_BENCH · 当前未连接</span><span>Local draft · ${state.storageBlocked ? t('未持久化') : t('浏览器本地持久化')}</span></footer></div></div>`;
    document.title = html`${t(nav.find(x => x[0] === state.page)?.[2])} · AeroAgentSim`;
  } finally {
    rendering = false;
  }
}
function configurationPage(validation, changes) {
  return html`<div class="config-summary"><div class="config-summary-title">${icon('file', 20)}<span><strong>${esc(state.draft.metadata.name)}</strong><small class="mono">${esc(state.draft.metadata.id)} · ${esc(state.draft.schema_version)}</small></span></div><div class="config-summary-stats"><span><strong>${state.draft.entities.reduce((n, e) => n + e.count, 0)}</strong> 实体</span><span><strong>${state.draft.scenario.duration_s}</strong> s 时长</span><span><strong>${state.draft.scenario.step_ms}</strong> ms 步长</span>${badge(validation.valid ? t('结构校验通过') : html`${validation.errors.length} 项错误`, validation.valid ? 'green' : 'red')}</div></div><div class="tabs" role="tablist" aria-label="配置分类">${tabs.map(([id, label], i) => html`<button role="tab" aria-selected="${state.tab === id}" class="tab ${state.tab === id ? 'active' : ''}" data-tab="${id}"><span class="tab-number">0${i + 1}</span>${t(label)}</button>`).join('')}</div><div class="workspace-grid ${state.tab === 'graph' ? 'graph-layout' : ''}"><div class="editor-stack">${state.showValidation ? validationPanel(validation) : ''}${{
    graph: () => graphWorkbench.render(),
    scenario: scenarioPanel,
    entities: entitiesPanel,
    mobility: mobilityPanel,
    network: networkPanel,
    compute: computePanel,
    semantics: semanticsPanel
  }[state.tab]()}</div>${state.tab === 'graph' ? '' : html`<aside class="inspector">${inspector(validation, changes)}</aside>`}</div>`;
}
function inspector(validation, changes) {
  return panel(t('配置就绪检查'), t('每项能力单独报告状态'), html`<div class="readiness-list">${[[t('配置结构'), validation.valid ? t('有效') : t('待修复'), validation.valid], [t('实体标识与绑定'), validation.errors.some(e => e.path.includes('entit')) ? t('待修复') : t('已检查'), !validation.errors.some(e => e.path.includes('entit'))], [t('外部运行通道'), t('未连接'), false], [t('Atlas 执行器'), t('未加载'), false]].map(([label, value, ok]) => html`<div class="readiness-item"><span class="check-dot ${ok ? 'is-ready' : ''}">${ok ? '✓' : '·'}</span><span>${t(label)}</span>${badge(value, ok ? 'green' : 'muted')}</div>`).join('')}</div><div class="divider"></div><div class="key-value"><span>坐标系</span><strong>ENU · 米</strong></div><div class="key-value"><span>高度基准</span><strong>Local z</strong></div><div class="key-value"><span>配置来源</span><strong>人工合成示例</strong></div><div class="key-value"><span>版本状态</span><strong>${state.versions.length ? html`v${state.versions.at(-1).number} + ${changes} 处变更` : t('尚未保存版本')}</strong></div><div class="divider"></div><p class="field-hint">配置值是期望输入。配置带宽不等于实测吞吐；本地高度不自动解释为 AGL / AMSL。</p>${button(t('查看编译计划 ') + icon('arrow', 15), 'compile', 'full-width')}`) + panel(t('系统权责'), t('一份配置，一个物理时钟'), html`<div class="authority-row"><span class="authority-logo">A</span><div><strong>AeroAgentSim</strong><small>配置编排与适配契约</small></div></div><div class="authority-row"><span class="authority-logo">B</span><div><strong>AERO_BENCH</strong><small>物理运行 / Three.js 主视图</small></div></div><div class="authority-row"><span class="authority-logo">S</span><div><strong>Atlas</strong><small>状态、谓词与事件解释</small></div></div>`);
}
function validationPanel(v) {
  return html`<section class="notice validation-notice ${v.valid ? 'info' : 'warning'}"><strong>${v.valid ? t('配置结构校验通过') : t('配置需要修正')}</strong>${v.errors.map(e => html`<div class="error-text"><span class="mono">${esc(e.path)}</span> · ${esc(message(e.message))}</div>`).join('')}${v.warnings.slice(0, 8).map(e => html`<div class="validation-warning"><span class="mono">${esc(e.path)}</span> · ${esc(message(e.message))}</div>`).join('')}<p class="field-hint">结构通过仅表示可保存或生成示例，不代表外部适配器已接入。</p></section>`;
}
function scenarioPanel() {
  return panel(t('场景定义'), t('设置可复现的运行边界与坐标约定。'), html`<div class="form-grid">${input('metadata.name', t('配置名称'))}${input('metadata.id', t('配置标识'), {
    hint: t('小写标识，保留为稳定配置 ID')
  })}${input('metadata.description', t('说明'))}${input('scenario.scene_id', t('场景资源引用'), {
    hint: t('逻辑资源引用；当前不加载私有地图')
  })}</div><div class="divider"></div><h3 class="section-title">时间与复现</h3><div class="form-grid three">${input('scenario.duration_s', t('仿真时长'), {
    type: 'number',
    unit: 's',
    min: .1
  })}${input('scenario.step_ms', t('物理步长'), {
    type: 'number',
    unit: 'ms',
    min: 1,
    step: 1
  })}${input('scenario.seed', t('随机种子'), {
    type: 'number',
    min: 0,
    step: 1
  })}</div><div class="notice info compact">${icon('info', 16)} BENCH 是唯一物理时钟。前端回放只移动查看游标，不驱动 SUMO 或 ns-3。</div>`) + panel(t('空间参考'), t('坐标、单位与高度语义不能混用。'), html`<div class="form-grid">${select('scenario.frame', t('水平坐标系'), [['ENU', 'East · North · Up (ENU)']])}${select('scenario.vertical_datum', t('垂直参考'), [['local', t('Local z · 未声明海拔基准')]])}</div><div class="coordinate-card"><div class="coordinate-axes"><span class="axis-e">E</span><span class="axis-n">N</span><span class="axis-u">U</span></div><div><strong>分析坐标 [E, N, U]</strong><p>BENCH 渲染映射为 [E, U, −N]，只在渲染边界转换一次。</p><span class="mono">position: m &nbsp; velocity: m/s &nbsp; time: ns</span></div></div>`);
}
function entitiesPanel() {
  const entities = state.draft.entities.filter(e => !state.filter || html`${e.id} ${t(types[e.type])}`.toLowerCase().includes(state.filter.toLowerCase()));
  return panel(t('实体编排'), t('区分类型、实例数量、运动来源与资源绑定。'), html`<div class="toolbar"><div class="search-box">${icon('search', 16)}<input class="search-input" id="entity-filter" placeholder="搜索实体标识或类型" value="${esc(state.filter)}" aria-label="搜索实体"></div><span class="subtle">${state.draft.entities.length} 个模板 / ${expandedEntities(state.draft).length} 个实例</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>实体 / 标识</th><th>类型</th><th>数量</th><th>运动来源</th><th>资源绑定</th><th></th></tr></thead><tbody>${entities.map(e => html`<tr><td><div class="entity-cell"><span class="entity-avatar ${e.type}">${icon(e.type === 'uav' ? 'uav' : 'cube', 17)}</span><div><strong>${esc(e.id)}</strong><small class="mono">[${e.position_enu_m.join(', ')}] m</small></div></div></td><td>${t(types[e.type])}</td><td>${e.count}</td><td>${badge(t(providers[e.provider]) || esc(e.provider), e.provider === 'sumo' ? 'amber' : 'muted')}</td><td><div class="resource-tags">${e.radio_profile_id ? html`<span>${esc(e.radio_profile_id)}</span>` : t('<span>无无线绑定</span>')}${e.compute_profile_id ? html`<span>${esc(e.compute_profile_id)}</span>` : ''}</div></td><td>${button(t('编辑'), 'entity-edit', 'small', html`data-id="${esc(e.id)}"`)}</td></tr>`).join('')}</tbody></table></div><div class="notice info compact">群组数量 > 1 时，以模板 ID + 序号展开为稳定实例。语义绑定必须引用具体实例。</div>`, button(icon('plus', 16) + t(' 添加实体'), 'entity-add', 'small primary')) + panel(t('显式身份映射'), t('禁止按数组位置或删除前缀推测实体身份。'), html`<div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>Canonical entity</th><th>Template ID</th><th>Provider</th><th>Generation</th></tr></thead><tbody>${expandedEntities(state.draft).slice(0, 12).map(e => html`<tr><td class="mono">${esc(e.entity_id)}</td><td class="mono">${esc(e.template_id)}</td><td>${esc(e.provider)}</td><td>0 · fixture epoch</td></tr>`).join('')}</tbody></table></div>${expandedEntities(state.draft).length > 12 ? t('<p class="field-hint">预览前 12 个实例，导出保留完整实体配置。</p>') : ''}`);
}
function mobilityPanel() {
  return panel(t('地面交通 · SUMO'), t('由 BENCH motion provider 接管；当前仅配置引用。'), html`${toggle('mobility.sumo.enabled', t('启用 SUMO 配置'), t('不会在此启动 SUMO 或连接 TraCI'))}<div class="divider"></div><div class="form-grid">${input('mobility.sumo.network_asset', t('路网资源引用'), {
    hint: t('必须由未来资源适配器解析，不接受随意读取路径')
  })}${input('mobility.sumo.route_asset', t('交通路线引用'))}${input('mobility.sumo.step_ms', t('SUMO 步长'), {
    type: 'number',
    unit: 'ms',
    min: 1,
    step: 1
  })}</div><div class="notice warning compact">${icon('info', 16)} SUMO 步长必须与场景物理步长一致；禁止建立第二个 simulationStep 循环。</div>`) + panel(t('空中移动 · UAV'), t('配置期望模型；真实移动由 motion provider 负责。'), html`<div class="form-grid">${select('mobility.uav.model', t('飞行模型'), [['waypoint', t('Waypoint · 航点')], ['hold', t('Hold · 定点保持')]])}${input('mobility.uav.cruise_speed_mps', t('巡航速度'), {
    type: 'number',
    unit: 'm/s',
    min: 0
  })}${input('mobility.uav.altitude_m', t('期望飞行高度'), {
    type: 'number',
    unit: 'm',
    min: 0,
    hint: t('Local z，不自动转换为离地高度')
  })}</div><p class="field-hint">示例回放采用明确标记的预制轨迹偏移，仅用于检验选择同步与证据缺失。它不会执行本页飞行模型。</p>`);
}
function networkPanel() {
  return networkStudyPanels() + panel(t('网络执行模型'), t('配置参数与网络观测严格区分。'), html`${toggle('network.enabled', t('启用网络配置'), t('网络 stage 与 motion stage 独立声明就绪状态'))}<div class="divider"></div><div class="form-grid">${select('network.provider', t('网络提供方'), [['local-demo', t('本地配置示例 · 无网络计算')], ['ns3', t('ns-3 / BENCH · 未连接')]])}${input('network.link.configured_rate_mbps', t('配置数据率'), {
    type: 'number',
    unit: 'Mbps',
    min: .001,
    hint: t('Configured rate，不作为 measured throughput')
  })}${input('network.link.propagation_delay_ms', t('配置传播时延'), {
    type: 'number',
    unit: 'ms',
    min: 0,
    hint: t('Propagation delay，不等同于端到端时延')
  })}</div>`) + state.draft.network.radio_profiles.map((p, i) => panel(html`无线配置 · ${esc(p.id)}`, t('独立人工配置；非服务器实测无线参数。'), html`<div class="form-grid three">${input(html`network.radio_profiles.${i}.id`, t('Profile ID'))}${select(html`network.radio_profiles.${i}.wifi_standard`, t('Wi-Fi 制式'), ['802.11n', '802.11ac', '802.11ax'])}${input(html`network.radio_profiles.${i}.frequency_ghz`, t('中心频率'), {
    type: 'number',
    unit: 'GHz',
    min: .1
  })}${input(html`network.radio_profiles.${i}.channel_width_mhz`, t('信道带宽'), {
    type: 'number',
    unit: 'MHz',
    min: 1
  })}${input(html`network.radio_profiles.${i}.tx_power_dbm`, t('发送功率'), {
    type: 'number',
    unit: 'dBm'
  })}${input(html`network.radio_profiles.${i}.rx_sensitivity_dbm`, t('接收灵敏度'), {
    type: 'number',
    unit: 'dBm'
  })}</div><div class="profile-footer">${badge(t('Authored · configured'), 'blue')}<span class="field-hint">灵敏度不能证明连接或成功交付</span></div>`, button(t('删除'), 'radio-delete', 'small ghost', html`data-index="${i}"`))).join('') + html`<div>${button(icon('plus', 16) + t(' 添加无线配置'), 'radio-add')}</div>`;
}
function computePanel() {
  return panel(t('计算模型'), t('容量、队列与任务约束作为独立期望配置。'), html`<div class="form-grid">${select('compute.provider', t('计算提供方'), [['local-demo', t('本地配置示例 · 未执行任务模型')]])}<div class="notice info compact">CPU / GPU 容量不自动产生任务时延。到达率、服务时间与调度执行器仍需接入。</div></div>`) + state.draft.compute.profiles.map((p, i) => panel(html`计算资源 · ${esc(p.id)}`, t('保留字段单位，不把规范化 GPU 单位解释为硬件卡数。'), html`<div class="form-grid three">${input(html`compute.profiles.${i}.id`, t('Profile ID'))}${input(html`compute.profiles.${i}.cpu_cores`, t('CPU 容量'), {
    type: 'number',
    unit: 'cores',
    min: .1
  })}${input(html`compute.profiles.${i}.gpu_units`, t('GPU 容量'), {
    type: 'number',
    unit: 'units',
    min: 0
  })}${input(html`compute.profiles.${i}.memory_mb`, t('内存'), {
    type: 'number',
    unit: 'MB',
    min: 1
  })}${input(html`compute.profiles.${i}.queue_limit`, t('队列上限'), {
    type: 'number',
    unit: 'tasks',
    min: 0,
    step: 1
  })}${input(html`compute.profiles.${i}.deadline_ms`, t('任务时限'), {
    type: 'number',
    unit: 'ms',
    min: 1
  })}</div><div class="profile-footer">${badge(t('Authored · configured'), 'blue')}<span class="field-hint">不宣称来自真实服务器当前值</span></div>`, button(t('删除'), 'compute-delete', 'small ghost', html`data-index="${i}"`))).join('') + html`<div>${button(icon('plus', 16) + t(' 添加计算配置'), 'compute-add')}</div>`;
}
function semanticsPanel() {
  return html`<div class="notice warning"><strong>Atlas 未加载：当前只编辑绑定，不执行谓词</strong><p>示例目标 ID 不代表完整目录。未绑定、输入缺失和未执行分别保留；unknown 绝不当作 false。</p></div>` + panel(t('语义绑定'), t('每个实体 / 实体对使用隔离的绑定、参数与历史上下文。'), html`<div class="table-wrap"><table class="data-table"><thead><tr><th>绑定 ID / 目标</th><th>实体</th><th>参数</th><th>状态</th><th></th></tr></thead><tbody>${state.draft.semantics.bindings.map((b, i) => html`<tr><td><strong>${esc(b.id)}</strong><small class="mono">${esc(b.target)}</small></td><td class="mono">${esc(b.entity_id)}</td><td class="mono">${esc(JSON.stringify(b.parameters))}</td><td>${badge(t('未执行'), 'amber')}</td><td>${button(t('编辑'), 'binding-edit', 'small', html`data-index="${i}"`)}</td></tr>`).join('') || t('<tr><td colspan="5" class="empty-state">尚无绑定，添加一个实体与目标的显式关联。</td></tr>')}</tbody></table></div>`, button(icon('plus', 16) + t(' 添加绑定'), 'binding-add', 'small primary')) + panel(t('输入映射边界'), t('保留 exact source pointer、单位与每一帧的来源。'), html`<div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>证据输入</th><th>BENCH 来源路径</th><th>单位 / 状态</th></tr></thead><tbody><tr><td>中心位置</td><td class="mono">samples[].pose.position.enu</td><td>m · source required</td></tr><tr><td>线速度</td><td class="mono">samples[].linear_velocity_enu</td><td>m/s · source required</td></tr><tr><td>机体间净距</td><td>移动实体尺寸尚未提供</td><td>${badge(t('Unsupported'), 'amber')}</td></tr><tr><td>网络链路观测</td><td class="mono">public.network.link.v3</td><td>需同 tick 网络 stage</td></tr></tbody></table></div><p class="field-hint">参数改动要求新 evaluation revision 或完整历史重算。模型资产 ID 不能代替真实机体尺寸。</p>`);
}
function runsPage() {
  return html`<div class="notice info"><strong>运行通道：本地 Fixture</strong><p>“准备示例运行”会冻结当前配置并建立合成快照。没有启动外部仿真；BENCH 连接后再开放真实开始、暂停和终止控制。</p></div><div class="steps"><div class="step active"><span>1</span>校验配置</div><div class="step active"><span>2</span>冻结快照</div><div class="step active"><span>3</span>生成合成证据</div><div class="step"><span>4</span>接入真实执行器</div></div>` + panel(t('运行记录'), t('每条记录固定配置哈希和独立的回放证据。'), state.runs.length ? html`<div class="table-wrap"><table class="data-table"><thead><tr><th>运行 / 配置</th><th>模式</th><th>状态</th><th>快照</th><th>创建时间</th><th></th></tr></thead><tbody>${state.runs.map(r => html`<tr><td><strong>${esc(r.name)}</strong><small class="mono">${esc(r.id)}</small></td><td>${badge(t('Synthetic fixture'), 'blue')}</td><td>${badge(t('已封存'), 'green')}</td><td>${r.frames.length} 帧<small class="mono">${r.config_digest.slice(0, 12)}…</small></td><td>${formatDate(r.created_at)}</td><td><div class="row">${button(t('打开回放'), 'run-open', 'small', html`data-id="${esc(r.id)}"`)}${button(icon('download', 16), 'run-export', 'small ghost', html`data-id="${esc(r.id)}" aria-label="导出运行证据"`)}</div></td></tr>`).join('')}</tbody></table></div>` : html`<div class="empty-state">${icon('runs', 34)}<h3>准备第一条可追溯运行</h3><p>保存当前配置与哈希，用合成证据验证共享游标和实体选择。</p>${button(t('准备示例运行'), 'prepare', 'primary')}</div>`);
}
function activeRun() {
  return state.runs.find(r => r.id === state.selectedRun) || state.runs[0];
}
function replayPage() {
  const run = activeRun();
  if (!run) return panel(t('暂无回放'), t('先准备一条合成运行。'), html`<div class="empty-state">${icon('replay', 40)}<p>回放只查看不可变快照，不创建另一个物理时钟。</p>${button(t('准备示例运行'), 'prepare', 'primary')}</div>`);
  const frame = seekFrame(run, state.cursor);
  if (!frame) return panel(t("No frame at this time"), t("No evidence is substituted outside the indexed bounds."), button(t("First indexed frame"), "first-frame", "primary"));
  if (!state.selectionLocked && (!state.selectedEntity || !frame.entities.some(e => e.entity_id === state.selectedEntity))) state.selectedEntity = frame.entities[0]?.entity_id;
  const selected = frame.entities.find(e => e.entity_id === state.selectedEntity);
  const evidence = semanticEvidence(run.config, frame, state.selectedEntity);
  return html`${!frame.observation_hash ? html`<div class="notice warning">${t('This saved fixture predates the observation adapter. Prepare a new fixture for contract export.')}</div>` : ''}${run.truncated ? t('<div class="notice warning compact">当前合成证据最多覆盖前 120 秒；完整场景时长保留在配置中。</div>') : ''}<div class="replay-toolbar"><select id="run-select" aria-label="选择回放记录">${state.runs.map(r => html`<option value="${esc(r.id)}" ${r.id === run.id ? 'selected' : ''}>${esc(r.name)} · ${r.id}</option>`).join('')}</select>${badge(t('Synthetic evidence'), 'blue')}${badge(t('BENCH renderer not mounted'), 'muted')}<span class="spacer"></span>${button(t('Export adapter observations'), 'observation-export', 'small')}${button(t('导出证据包'), 'run-export', 'small', html`data-id="${esc(run.id)}"`)}</div><div class="replay-layout"><div class="editor-stack">${panel(t('共享场景游标'), t('本地 ENU 二维检查视图 · 实际生产主视图将复用 BENCH Three.js'), html`<div class="scene-preview">${sceneSVG(frame)}</div><div class="legend"><span><i style="background:#168f8c"></i>无人机</span><span><i style="background:#477cd6"></i>地面实体</span><span><i style="background:#83929f"></i>基础设施</span><span>点击实体 → 同步证据</span></div><div class="timeline"><div class="row">${button(state.playing ? t('暂停查看') : t('播放快照'), 'play', 'small primary')}${button(t('前一帧'), 'previous-frame', 'small')}${button(t('后一帧'), 'next-frame', 'small')}<span class="spacer"></span><strong class="mono" id="cursor-label">${frame.relative_time_s.toFixed(1)} / ${run.duration_s.toFixed(1)} s</strong></div><input class="timeline-range" id="timeline" type="range" min="${run.frames[0].relative_time_s}" max="${run.duration_s}" step="${run.config.scenario.step_ms / 1000}" value="${frame.relative_time_s}" aria-label="回放时间游标"><div class="row subtle"><span>tick ${frame.tick} · ${frame.sim_time_ns} ns</span><span class="spacer"></span><span>精确 / 前值查找 · 无插值证据</span></div></div>`)}${panel(t('帧与阶段证据'), t('每个依赖阶段独立标注可用性。'), html`<div class="stage-grid">${Object.entries(frame.stages).map(([name, status]) => html`<div><span>${t(name)}</span>${badge(t(status), status === 'fixture' ? 'blue' : 'muted')}</div>`).join('')}</div><div class="divider"></div><div class="key-value"><span>配置 SHA-256</span><code class="mono hash">${run.config_digest}</code></div><div class="key-value"><span>帧 SHA-256</span><code class="mono hash">${frame.hash}</code></div>${frame.observation_hash ? html`<div class="key-value"><span>${t('Observation SHA-256')}</span><code class="mono hash">${frame.observation_hash}</code></div>` : ''}<div class="key-value"><span>来源</span><strong>fixture.authored · hypothetical</strong></div><div class="key-value"><span>源游标</span><strong>无 SSE 游标，合成快照索引</strong></div>`)}${panel(t('运行事件'), t('平台操作记录与领域语义事件分开。'), html`<div class="evidence-list">${run.events.map(e => html`<div class="evidence-row"><span class="mono">tick ${e.tick}</span><div><strong>${e.type}</strong><small>${esc(message(e.message))}</small></div></div>`).join('')}</div>`)}</div><aside class="inspector">${panel(t('实体与状态'), t('选择与场景游标一致。'), html`<select id="entity-select" aria-label="选择实体" ${state.selectionLocked ? 'disabled' : ''}>${frame.entities.map(e => html`<option value="${esc(e.entity_id)}" ${e.entity_id === state.selectedEntity ? 'selected' : ''}>${esc(e.entity_id)} · ${t(types[e.type])}</option>`).join('')}</select><div class="selection-lock">${button(state.selectionLocked ? t('Unlock selection') : t('Lock selection'), 'selection-lock', 'small', html`aria-pressed="${state.selectionLocked}"`)}${state.selectionLocked ? badge(t('Locked entity'), 'blue') : ''}</div><div class="divider"></div>${selected ? html`<div class="entity-detail-heading"><span class="entity-avatar ${selected.type}">${icon(selected.type === 'uav' ? 'uav' : 'cube', 22)}</span><div><strong>${esc(selected.entity_id)}</strong><small>${t(types[selected.type])} · generation ${selected.generation}</small></div></div><div class="key-value"><span>证据有效性</span>${badge(t(selected.validity), selected.validity === 'present' ? 'green' : 'amber')}</div><div class="key-value"><span>位置 [E,N,U]</span><strong class="mono">${selected.position_enu_m ? selected.position_enu_m.map(n => n.toFixed(1)).join(', ') + ' m' : 'null'}</strong></div><div class="key-value"><span>速度 [E,N,U]</span><strong class="mono">${selected.velocity_enu_mps ? selected.velocity_enu_mps.map(n => n.toFixed(2)).join(', ') + ' m/s' : 'null'}</strong></div><div class="key-value"><span>Provider</span><strong class="mono">${selected.source_provider_id}</strong></div>${selected.reason ? html`<div class="notice warning compact">${esc(message(selected.reason))}</div>` : ''}${motionEvidence(selected)}` : html`<div class="notice warning compact">${t('No entity sample at this cursor. The locked identity is retained without substituting evidence.')}</div>`}`)}${panel(t('语义证据'), t('与当前帧、实体和绑定隔离关联。'), evidence.length ? evidence.map(e => html`<div class="semantic-card"><span class="mono">${esc(e.binding_id)}</span><strong>${esc(e.target)}</strong>${badge(t('unknown · 未执行'), 'amber')}<p>${esc(message(e.reason))}</p><div class="rule-parameters"><small>${t('Target parameters · Not validated')}</small><pre class="code-preview compact">${esc(JSON.stringify(e.parameters, null, 2))}</pre><span>${t('Last truth flip')}: ${t('No evaluation history')} · null</span></div><small class="mono">tick ${e.frame_tick} · ${e.frame_hash.slice(0, 10)}</small></div>`).join('') : t('<div class="empty-state compact">此实体未绑定目标<br><small>Not bound</small></div>'))}${panel(t('选择契约'), t('完整版本键，避免异步旧结果覆盖。'), html`<pre id="selection-key" class="code-preview compact">${esc(JSON.stringify(frame.observation_hash ? selectionKey(run, frame, state.selectedEntity) : {
    status: 'legacy_fixture_projection_unavailable',
    run_id: run.id,
    source_integrity: run.integrity
  }, null, 2))}</pre>`)}</aside></div>`;
}
function sceneSVG(frame) {
  const valid = frame.entities.filter(e => e.position_enu_m);
  if (!valid.length) return t('<div class="empty-state">当前帧没有有效位置证据。</div>');
  const xs = valid.map(e => e.position_enu_m[0]),
    ys = valid.map(e => e.position_enu_m[1]);
  const all = activeRun().frames.flatMap(f => f.entities.filter(e => e.position_enu_m).map(e => e.position_enu_m));
  const bounds = all.reduce((b, p) => ({
    minX: Math.min(b.minX, p[0]),
    maxX: Math.max(b.maxX, p[0]),
    minY: Math.min(b.minY, p[1]),
    maxY: Math.max(b.maxY, p[1])
  }), {
    minX: Infinity,
    maxX: -Infinity,
    minY: Infinity,
    maxY: -Infinity
  });
  const minX = bounds.minX - 25,
    maxX = bounds.maxX + 25,
    minY = bounds.minY - 25,
    maxY = bounds.maxY + 25;
  const sx = x => 50 + (x - minX) / (maxX - minX) * 620,
    sy = y => 330 - (y - minY) / (maxY - minY) * 280;
  return html`<svg viewBox="0 0 720 390" role="img" aria-label="合成 ENU 场景与实体选择"><defs><pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M 32 0 L 0 0 0 32" fill="none" stroke="#e0e8ed" stroke-width="1"/></pattern></defs><rect width="720" height="390" fill="#f7fafb"/><rect x="24" y="24" width="672" height="342" rx="8" fill="url(#grid)"/><path d="M38 338H682M52 352V42" stroke="#b9c7d1" stroke-width="1.3"/><text class="scene-axis" x="674" y="357" fill="#708390" font-size="12">E</text><text class="scene-axis" x="34" y="48" fill="#708390" font-size="12">N</text>${valid.map(e => {
    const x = sx(e.position_enu_m[0]),
      y = sy(e.position_enu_m[1]),
      sel = e.entity_id === state.selectedEntity,
      color = e.type === 'uav' ? '#168f8c' : ['vehicle', 'pedestrian'].includes(e.type) ? '#477cd6' : '#83929f';
    return html`<g class="scene-entity" data-action="select-scene-entity" data-id="${esc(e.entity_id)}" tabindex="0" role="button" aria-label="选择 ${esc(e.entity_id)}" transform="translate(${x},${y})">${sel ? '<circle r="18" fill="#d5eee9" stroke="#168f8c" stroke-width="1.3"/>' : ''}${e.type === 'uav' ? html`<path d="M0-9 8 8 0 4-8 8Z" fill="${color}" stroke="#fff" stroke-width="1.5"/>` : html`<rect x="-6" y="-6" width="12" height="12" rx="${e.type === 'pedestrian' ? 6 : 3}" fill="${color}" stroke="#fff" stroke-width="1.5"/>`}<text class="scene-label" x="13" y="4" fill="${sel ? '#156e68' : '#5b6d7a'}" font-size="10" font-weight="${sel ? 600 : 400}">${esc(e.entity_id)}</text>${sel ? html`<text class="scene-label" x="13" y="19" fill="#82909b" font-size="9">z ${e.position_enu_m[2].toFixed(1)} m</text>` : ''}</g>`;
  }).join('')}</svg><p class="scene-caption">ENU · m · ${t('人工 Fixture · 无实时仿真')}</p>${frame.entities.some(e => !e.position_enu_m) ? html`<div class="notice warning scene-gap-notice">存在缺失位置 · 不使用前值补齐证据</div>` : ''}<div class="scene-entity-list">${frame.entities.map(e => html`<button class="scene-label-button ${e.entity_id === state.selectedEntity ? 'selected' : ''}" data-action="select-scene-entity" data-id="${esc(e.entity_id)}" aria-pressed="${e.entity_id === state.selectedEntity}"><span>${esc(e.entity_id)}</span>${badge(t(e.validity), e.position_enu_m ? 'green' : 'amber')}</button>`).join('')}</div>`;
}
function versionsPage() {
  const compare = state.versions.find(v => v.id === state.compare) || state.versions.at(-1);
  const diff = compare ? diffConfig(compare.config, state.draft) : [];
  return html`<div class="workspace-grid"><div class="editor-stack">${panel(t('保存的版本'), t('仅保存在此浏览器工作区，导出文件可迁移。'), state.versions.length ? html`<div class="version-list">${[...state.versions].reverse().map(v => html`<div class="version-row"><span class="version-icon">${icon('versions', 18)}</span><div><strong>v${v.number} · ${esc(v.config.metadata.name)}</strong><small>${formatDate(v.created_at)} · ${esc(v.note === '配置快照' ? t('配置快照') : v.note)}</small></div><span class="spacer"></span>${button(t('比较'), 'version-compare', 'small', html`data-id="${esc(v.id)}"`)}${button(t('载入草稿'), 'version-load', 'small', html`data-id="${esc(v.id)}"`)}${button(icon('download', 15), 'version-export', 'small ghost', html`data-id="${esc(v.id)}" aria-label="导出此版本"`)}</div>`).join('')}</div>` : t('<div class="empty-state">尚未保存版本。保存当前配置，建立第一份基线。</div>'))}${panel(t('配置差异'), compare ? html`比较 v${compare.number} → 当前工作草稿 · ${diff.length} 处字段变更` : t('先保存一个版本以比较变更。'), compare ? diff.length ? diff.map(d => html`<div class="diff-row"><div class="diff-path mono">${esc(d.path)}</div><div class="diff-values"><span class="diff-before">− ${esc(JSON.stringify(d.before) ?? '(unset)')}</span><span class="diff-after">+ ${esc(JSON.stringify(d.after) ?? '(unset)')}</span></div></div>`).join('') : t('<div class="empty-state compact">工作草稿与此版本一致。</div>') : t('<div class="empty-state compact">没有比较基线。</div>'))}</div><aside class="inspector">${panel(t('可移植配置'), t('完整 JSON，保留版本与单位。'), html`<p class="field-hint">导入前会校验结构、标识和引用。导入只替换工作草稿，已有版本和运行记录保留。</p>${button(t('导入配置 JSON'), 'import', 'full-width')}${button(t('导出工作草稿'), 'export', 'full-width')}<div class="divider"></div><div class="key-value"><span>Schema</span><strong class="mono">aero-console.config/v1</strong></div><div class="key-value"><span>保存版本</span><strong>${state.versions.length}</strong></div>`)}${panel(t('工作草稿'), t('不会覆盖已封存运行的配置。'), html`<pre class="code-preview">${esc(JSON.stringify(state.draft, null, 2))}</pre>`)}</aside></div>`;
}
function adaptersPage() {
  return html`<div class="notice info"><strong>当前部署边界</strong><p>独立配置前端 + 纯契约适配器 + 人工合成回放。没有连接私有服务器，没有加载私有 BENCH 源码、地图或原始轨迹。</p></div><div class="card-grid adapter-grid">${ADAPTERS.map(a => panel(t(a.name), t(a.owner), html`<div class="row">${badge(t(a.status), a.status === 'ready' ? 'green' : 'muted')}</div><h3 class="section-title">${esc(t(a.transport))}</h3><p class="field-hint">${esc(t(a.note))}</p>`)).join('')}</div>${panel(t('约定与能力边界'), t('这些是待连接接口，不宣称运行能力。'), html`<div class="table-wrap"><table class="data-table"><thead><tr><th>边界</th><th>精确约定</th><th>当前状态</th></tr></thead><tbody><tr><td>BENCH motion</td><td class="mono">scene_state.samples → pose.position.enu / linear_velocity_enu</td><td>${badge(t('独立适配校验'), 'blue')}</td></tr><tr><td>恢复游标</td><td class="mono">after_transition / after_scene_tick / after_event_sequence</td><td>独立递增；不合并为 Last-Event-ID</td></tr><tr><td>sealed replay</td><td>Manifest + content-addressed shards + prior history</td><td>${badge(t('真实读取器待接入'), 'amber')}</td></tr><tr><td>网络证据</td><td>Receipt aggregate ≠ geometry ≠ per-link observation</td><td>禁止把总吞吐分派为各链路实测值</td></tr><tr><td>Atlas engine</td><td>Versioned frame / binding / evaluation revision</td><td>${badge(t('真实执行器待接入'), 'amber')}</td></tr><tr><td>机体净距</td><td>必须有实际 body / footprint geometry</td><td>${badge(t('Unsupported'), 'amber')}</td></tr></tbody></table></div>`)}${panel(t('配置来源原则'), t('每个显示值都保留其语义和使用范围。'), html`<div class="form-grid"><div><h3 class="section-title">此工作区的数值</h3><p class="field-hint">由独立示例人工编写，用于验证配置、绑定、保存与回放交互。不是 AERO_WORLD 或 BENCH 服务器的实际参数。</p></div><div><h3 class="section-title">真实接入时</h3><p class="field-hint">为每个属性记录 source pointer、单位、有效区间、所有者与 provenance。simulated / measured / derived 不可混写。</p></div></div>`)}`;
}
function formatDate(s) {
  return new Date(s).toLocaleString(getLocale(), {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  });
}
function download(name, value) {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
      type: 'application/json'
    }),
    url = URL.createObjectURL(blob),
    a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}
function openModal(title, body, footer = '') {
  pause();
  if (!$('#modal-root').children.length) {
    const el = document.activeElement;
    modalReturnFocus = {
      id: el?.id,
      action: el?.dataset?.action,
      entity: el?.dataset?.id
    };
  }
  $('#modal-root').innerHTML = html`<div class="modal-backdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="panel-header"><h2 id="modal-title">${title}</h2>${languagePicker("modal-locale")}<button class="icon-button" data-action="modal-close" aria-label="关闭">${icon('close')}</button></div><div class="modal-body">${body}</div>${footer ? html`<div class="modal-footer">${footer}</div>` : ''}</section></div>`;
  $('#modal-root').querySelector('input,select,button')?.focus();
}
function closeModal() {
  $('#modal-root').innerHTML = '';
  modalRefresh = null;
  const target = modalReturnFocus?.id ? document.getElementById(modalReturnFocus.id) : modalReturnFocus?.action ? document.querySelector(`[data-action="${modalReturnFocus.action}"]${modalReturnFocus.entity ? `[data-id="${modalReturnFocus.entity}"]` : ''}`) : null;
  target?.focus?.();
  modalReturnFocus = null;
}
function pause() {
  state.playing = false;
  if (playback) clearInterval(playback);
  playback = null;
}
function entityModal(id) {
  modalRefresh = () => entityModal(id);
  const index = state.draft.entities.findIndex(e => e.id === id),
    entity = index < 0 ? {
      id: 'uav-new',
      type: 'uav',
      count: 1,
      provider: 'local-demo',
      position_enu_m: [0, 0, 40],
      radio_profile_id: null,
      compute_profile_id: null
    } : state.draft.entities[index];
  const localField = (name, label, value, type = 'text') => html`<div class="field"><label for="entity-${name}">${t(label)}</label><input id="entity-${name}" name="${name}" value="${esc(value)}" type="${type}" ${type === 'number' ? 'step="any"' : ''}></div>`;
  openModal(index < 0 ? t('添加实体模板') : t('编辑实体模板'), html`<form id="entity-form" data-index="${index}"><div class="form-grid">${localField('id', t('稳定 ID'), entity.id)}<div class="field"><label for="entity-type">实体类型</label><select id="entity-type" name="type">${Object.entries(types).map(([k, v]) => html`<option value="${k}" ${entity.type === k ? 'selected' : ''}>${t(v)}</option>`).join('')}</select></div>${localField('count', t('实例数量'), entity.count, 'number')}<div class="field"><label for="entity-provider">运动来源</label><select id="entity-provider" name="provider">${['local-demo', 'sumo', 'static'].map(k => html`<option value="${k}" ${entity.provider === k ? 'selected' : ''}>${t(providers[k])}</option>`).join('')}</select></div></div><h3 class="section-title">初始位置 · ENU (m)</h3><div class="form-grid three">${localField('east', t('East'), entity.position_enu_m[0], 'number')}${localField('north', t('North'), entity.position_enu_m[1], 'number')}${localField('up', t('Up · local z'), entity.position_enu_m[2], 'number')}</div><div class="form-grid"><div class="field"><label for="entity-radio">无线配置</label><select id="entity-radio" name="radio"><option value="">不绑定</option>${state.draft.network.radio_profiles.map(p => html`<option value="${esc(p.id)}" ${entity.radio_profile_id === p.id ? 'selected' : ''}>${esc(p.id)}</option>`).join('')}</select></div><div class="field"><label for="entity-compute">计算配置</label><select id="entity-compute" name="compute"><option value="">不绑定</option>${state.draft.compute.profiles.map(p => html`<option value="${esc(p.id)}" ${entity.compute_profile_id === p.id ? 'selected' : ''}>${esc(p.id)}</option>`).join('')}</select></div></div><div id="modal-error" class="error-text" role="alert"></div></form>`, html`${index >= 0 ? button(t('删除实体'), 'entity-delete', 'danger', html`data-index="${index}"`) : ''}<span class="spacer"></span>${button(t('取消'), 'modal-close')}${button(t('保存实体'), 'entity-save', 'primary')}`);
}
function bindingModal(index) {
  modalRefresh = () => bindingModal(index);
  const b = index >= 0 ? state.draft.semantics.bindings[index] : {
    id: html`binding-${state.draft.semantics.bindings.length + 1}`,
    target: 'hu.predicate.actor_moving',
    entity_id: expandedEntities(state.draft)[0]?.entity_id || '',
    parameters: {}
  };
  const targets = ['hu.predicate.actor_moving', 'hu.predicate.vertical_ascent', 'hu.predicate.vertical_descent', 'hu.predicate.actor_stationary', 'hu.predicate.pair_close'];
  openModal(index >= 0 ? t('编辑语义绑定') : t('添加语义绑定'), html`<form id="binding-form" data-index="${index}"><div class="form-grid"><div class="field"><label for="binding-id">绑定 ID</label><input id="binding-id" name="id" value="${esc(b.id)}"></div><div class="field"><label for="binding-target">示例目标 ID · 非完整目录</label><select id="binding-target" name="target">${[...new Set([...targets, b.target])].map(t => html`<option ${t === b.target ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div><div class="field"><label for="binding-entity">显式实体实例</label><select id="binding-entity" name="entity">${expandedEntities(state.draft).map(e => html`<option ${e.entity_id === b.entity_id ? 'selected' : ''}>${esc(e.entity_id)}</option>`).join('')}</select></div></div><div class="field"><label for="binding-params">参数 JSON · 语义与单位由目标定义约束</label><textarea rows="5" id="binding-params" name="parameters" class="mono">${esc(JSON.stringify(b.parameters, null, 2))}</textarea></div><div class="notice warning compact">这里只保存绑定草稿。参数、实体对与历史依赖需由真实 Atlas 目录与执行器进一步校验。</div><div id="modal-error" class="error-text" role="alert"></div></form>`, html`${index >= 0 ? button(t('删除绑定'), 'binding-delete', 'danger', html`data-index="${index}"`) : ''}<span class="spacer"></span>${button(t('取消'), 'modal-close')}${button(t('保存绑定'), 'binding-save', 'primary')}`);
}
let preparing = false;
document.addEventListener('click', async event => {
  if (graphWorkbench.handleClick(event)) return;
  graphWorkbench.captureEditor();
  const navEl = event.target.closest('[data-nav]');
  if (navEl) {
    pause();
    state.page = navEl.dataset.nav;
    persist();
    state.filter = '';
    history.replaceState(null, '', '#' + state.page);
    render();
    return;
  }
  const tabEl = event.target.closest('[data-tab]');
  if (tabEl) {
    state.tab = tabEl.dataset.tab;
    state.filter = '';
    render();
    return;
  }
  const el = event.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  try {
    if (action === 'modal-close') {
      closeModal();
      return;
    }
    if (action === 'help') {
      showHelp();
      return;
    }
    if (action === 'study-preview') {
      showStudyPreview(el.dataset.id, el.closest('.study-profile')?.querySelector('select')?.value || undefined);
      return;
    }
    if (action === 'study-apply') {
      const result = applyStudyProfile(state.draft, el.dataset.id, {
        variant_id: el.dataset.variant || undefined
      });
      state.draft = result.config;
      invalidateAuthoring();
      persist();
      closeModal();
      render();
      toast(html`已应用 ${el.dataset.id} 到本地草稿，请保存版本以保留差异。`);
      return;
    }
    if (action === 'validate') {
      state.showValidation = true;
      const v = validateConfig(state.draft);
      render();
      toast(v.valid ? t('配置结构通过；外部服务仍未连接。') : html`发现 ${v.errors.length} 项配置错误。`, v.valid ? '' : 'error');
      return;
    }
    if (action === 'save') {
      const v = validateConfig(state.draft);
      if (!v.valid) {
        state.showValidation = true;
        state.page = 'configuration';
        render();
        toast(t('请先修正配置错误。'), 'error');
        return;
      }
      const version = createVersion(state.draft, state.versions);
      state.versions.push(version);
      invalidateAuthoring();
      state.compare = version.id;
      persist();
      render();
      toast(html`已保存本地版本 v${version.number}`);
      return;
    }
    if (action === 'export') {
      download(html`${state.draft.metadata.id}.config.json`, exportConfig(state.draft));
      toast(t('配置已导出。'));
      return;
    }
    if (action === 'import') {
      $('#import-file').click();
      return;
    }
    if (action === 'compile') {
      showCompilation();
      return;
    }
    if (action === 'compile-export') {
      download(html`${state.draft.metadata.id}.desired-plan.json`, compileConfig(state.draft));
      return;
    }
    if (action === 'prepare') {
      if (preparing) return;
      const v = validateConfig(state.draft);
      if (!v.valid) {
        state.showValidation = true;
        state.page = 'configuration';
        render();
        toast(t('配置存在错误，无法准备示例。'), 'error');
        return;
      }
      preparing = true;
      el.disabled = true;
      el.textContent = t('正在冻结配置…');
      try {
        const run = await createFixtureRun(clone(state.draft));
        state.runs.unshift(run);
        state.selectedRun = run.id;
        state.cursor = run.frames[0].relative_time_s;
        state.selectedEntity = null;
        state.selectionLocked = false;
        state.page = 'runs';
        persist();
        render();
        toast(t('合成运行已封存，配置与证据可回放。'));
      } finally {
        preparing = false;
      }
      return;
    }
    if (action === 'run-open') {
      pause();
      state.selectedRun = el.dataset.id;
      state.page = 'replay';
      state.cursor = activeRun().frames[0].relative_time_s;
      state.selectedEntity = null;
      state.selectionLocked = false;
      persist();
      render();
      return;
    }
    if (action === 'first-frame') {
      state.cursor = activeRun().frames[0].relative_time_s;
      render();
      return;
    }
    if (action === 'observation-export') {
      const run = activeRun();
      if (!run.frames.every(frame => frame.observation_hash)) throw new Error('This saved fixture predates the observation adapter. Prepare a new fixture for contract export.');
      download(`${run.id}.observations.json`, exportObservations(run));
      return;
    }
    if (action === 'run-export') {
      const run = state.runs.find(r => r.id === el.dataset.id);
      if (run) download(html`${run.id}.evidence.json`, run);
      return;
    }
    if (action === 'play') {
      if (state.playing) {
        pause();
        render();
        return;
      }
      const run = activeRun();
      if (!run) return;
      if (state.cursor >= run.duration_s) state.cursor = run.frames[0].relative_time_s;
      state.playing = true;
      render();
      playback = setInterval(() => {
        if (state.page !== 'replay') {
          pause();
          return;
        }
        const current = seekFrame(run, state.cursor),
          index = run.frames.indexOf(current);
        if (index >= run.frames.length - 1) {
          pause();
          render();
          return;
        }
        state.cursor = run.frames[index + 1].relative_time_s;
        render();
      }, 400);
      return;
    }
    if (action === 'previous-frame' || action === 'next-frame') {
      pause();
      const run = activeRun(),
        current = seekFrame(run, state.cursor),
        index = run.frames.indexOf(current);
      state.cursor = run.frames[Math.max(0, Math.min(run.frames.length - 1, index + (action === 'next-frame' ? 1 : -1)))].relative_time_s;
      render();
      return;
    }
    if (action === 'selection-lock') {
      state.selectionLocked = !state.selectionLocked;
      persist();
      render();
      return;
    }
    if (action === 'select-scene-entity') {
      if (state.selectionLocked) return;
      state.selectedEntity = el.dataset.id;
      persist();
      render();
      return;
    }
    if (action === 'version-compare') {
      state.compare = el.dataset.id;
      render();
      return;
    }
    if (action === 'version-load') {
      showVersionLoad(el.dataset.id);
      return;
    }
    if (action === 'version-load-confirm') {
      const v = state.versions.find(v => v.id === el.dataset.id);
      state.draft = clone(v.config);
      invalidateAuthoring();
      state.compare = v.id;
      persist();
      closeModal();
      render();
      toast(html`已载入 v${v.number}。`);
      return;
    }
    if (action === 'version-export') {
      const v = state.versions.find(v => v.id === el.dataset.id);
      download(html`${v.config.metadata.id}.v${v.number}.json`, exportConfig(v.config));
      return;
    }
    if (action === 'entity-add' || action === 'entity-edit') {
      entityModal(el.dataset.id);
      return;
    }
    if (action === 'entity-save') {
      const form = $('#entity-form'),
        data = new FormData(form),
        index = Number(form.dataset.index);
      const entity = {
        id: data.get('id'),
        type: data.get('type'),
        count: Number(data.get('count')),
        provider: data.get('provider'),
        position_enu_m: [Number(data.get('east')), Number(data.get('north')), Number(data.get('up'))],
        radio_profile_id: data.get('radio') || null,
        compute_profile_id: data.get('compute') || null
      };
      const next = clone(state.draft);
      if (index < 0) next.entities.push(entity);else next.entities[index] = {
        ...next.entities[index],
        ...entity
      };
      const errors = validateConfig(next).errors;
      if (errors.length) {
        $('#modal-error').textContent = errors.map(e => html`${e.path}: ${message(e.message)}`).join('; ');
        return;
      }
      state.draft = next;
      invalidateAuthoring();
      persist();
      closeModal();
      render();
      toast(t('实体配置已保存。'));
      return;
    }
    if (action === 'entity-delete') {
      const next = clone(state.draft);
      next.entities.splice(Number(el.dataset.index), 1);
      const errors = validateConfig(next).errors;
      if (errors.length) {
        $('#modal-error').textContent = t('无法删除：') + errors.map(e => message(e.message)).join('; ');
        return;
      }
      state.draft = next;
      invalidateAuthoring();
      persist();
      closeModal();
      render();
      toast(t('实体已从草稿移除。'));
      return;
    }
    if (action === 'radio-add') {
      invalidateAuthoring();
      state.draft.network.radio_profiles.push({
        id: html`radio-${Date.now().toString().slice(-6)}`,
        wifi_standard: '802.11ac',
        frequency_ghz: 5.8,
        channel_width_mhz: 20,
        tx_power_dbm: 20,
        rx_sensitivity_dbm: -90
      });
      persist();
      render();
      return;
    }
    if (action === 'compute-add') {
      invalidateAuthoring();
      state.draft.compute.profiles.push({
        id: html`compute-${Date.now().toString().slice(-6)}`,
        cpu_cores: 4,
        gpu_units: 1,
        memory_mb: 4096,
        queue_limit: 8,
        deadline_ms: 100
      });
      persist();
      render();
      return;
    }
    if (action === 'radio-delete' || action === 'compute-delete') {
      const next = clone(state.draft);
      const list = action === 'radio-delete' ? next.network.radio_profiles : next.compute.profiles;
      list.splice(Number(el.dataset.index), 1);
      const errors = validateConfig(next).errors;
      if (errors.length) {
        toast(t('无法删除：该配置仍被实体引用，或至少需要一项配置。'), 'error');
        return;
      }
      state.draft = next;
      invalidateAuthoring();
      persist();
      render();
      return;
    }
    if (action === 'binding-add' || action === 'binding-edit') {
      bindingModal(action === 'binding-add' ? -1 : Number(el.dataset.index));
      return;
    }
    if (action === 'binding-save') {
      const form = $('#binding-form'),
        data = new FormData(form),
        index = Number(form.dataset.index);
      let parameters;
      try {
        parameters = JSON.parse(data.get('parameters'));
        if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') throw new Error(t('参数必须是 JSON object'));
        if (Object.keys(parameters).some(k => ['__proto__', 'constructor', 'prototype'].includes(k))) throw new Error(t('参数包含非法键'));
      } catch (e) {
        $('#modal-error').textContent = message(e.message);
        return;
      }
      const binding = {
        id: data.get('id'),
        target: data.get('target'),
        entity_id: data.get('entity'),
        parameters
      };
      const next = clone(state.draft);
      if (index < 0) next.semantics.bindings.push(binding);else next.semantics.bindings[index] = binding;
      const errors = validateConfig(next).errors;
      if (errors.length) {
        $('#modal-error').textContent = errors.map(e => html`${e.path}: ${message(e.message)}`).join('; ');
        return;
      }
      state.draft = next;
      invalidateAuthoring();
      persist();
      closeModal();
      render();
      toast(t('绑定已保存；Atlas 尚未执行。'));
      return;
    }
    if (action === 'binding-delete') {
      invalidateAuthoring();
      state.draft.semantics.bindings.splice(Number(el.dataset.index), 1);
      persist();
      closeModal();
      render();
      return;
    }
  } catch (e) {
    toast(e.message || t('操作失败。'), 'error');
    console.error(e);
  }
});
document.addEventListener('change', event => {
  if (graphWorkbench.handleInput(event)) return;
  const el = event.target;
  if (el.dataset.locale !== undefined) {
    switchLanguage(el.value);
    return;
  }
  if (el.dataset.path) {
    let value = el.type === 'checkbox' ? el.checked : el.type === 'number' ? el.value === '' && el.dataset.path?.startsWith('network.study.traffic.') ? null : Number(el.value) : el.value;
    pathSet(el.dataset.path, value);
    persist();
    render();
    return;
  }
  if (el.id === 'run-select') {
    pause();
    state.selectedRun = el.value;
    state.cursor = activeRun().frames[0].relative_time_s;
    state.selectedEntity = null;
    state.selectionLocked = false;
    persist();
    render();
    return;
  }
  if (el.id === 'entity-select') {
    if (state.selectionLocked) return;
    state.selectedEntity = el.value;
    persist();
    render();
    return;
  }
});
document.addEventListener('input', event => {
  if (graphWorkbench.handleInput(event)) return;
  if (event.target.id === 'timeline') {
    pause();
    state.cursor = Number(event.target.value);
    persist();
    render();
    return;
  }
  if (event.target.id === 'entity-filter') {
    const start = event.target.selectionStart;
    state.filter = event.target.value;
    render();
    const input = $('#entity-filter');
    input.focus();
    input.setSelectionRange(start, start);
  }
});
$('#import-file').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error(t('配置文件超过 2 MB。'));
    const imported = safeImport(await file.text());
    showImport(imported);
  } catch (e) {
    toast(html`导入失败：${e.message}`, 'error');
  } finally {
    event.target.value = '';
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('#modal-root').children.length) {
    closeModal();
    return;
  }
  if (event.target.classList?.contains('scene-entity') && ['Enter', ' '].includes(event.key)) {
    event.preventDefault();
    if (state.selectionLocked) return;
    state.selectedEntity = event.target.dataset.id;
    persist();
    render();
    return;
  }
  if (event.key === 'Tab' && $('#modal-root').children.length) {
    const nodes = [...$('#modal-root').querySelectorAll('button,input,select,textarea')].filter(x => !x.disabled);
    if (event.shiftKey && document.activeElement === nodes[0]) {
      event.preventDefault();
      nodes.at(-1)?.focus();
    } else if (!event.shiftKey && document.activeElement === nodes.at(-1)) {
      event.preventDefault();
      nodes[0]?.focus();
    }
  }
});
window.addEventListener('beforeunload', () => {
  pause();
  persist();
});
if (nav.some(n => n[0] === location.hash.slice(1))) state.page = location.hash.slice(1);
render();
if (stored.warning) toast(stored.warning, 'error');
function networkStudyPanels() {
  const study = state.draft.network.study;
  const catalog = panel(t('有依据的网络研究档'), t('选择前查看差异；所有配置档均为研究参考，未连接服务器、未经本地外场校准。'), html`<div class="study-profile-grid">${STUDY_PROFILES.map(p => html`<article class="study-profile ${study?.profile_id === p.id ? 'active' : ''}"><div class="row"><span class="study-profile-id">${esc(p.id)}</span>${study?.profile_id === p.id ? badge(t('当前草稿'), 'green') : ''}</div><strong>${esc(t(p.name))}</strong><p>${esc(t(p.summary))}</p>${p.variants.length ? html`<select aria-label="${esc(p.id)} 研究变体">${p.variants.map(v => html`<option value="${esc(v.id)}" ${p.study.variant_id === v.id ? 'selected' : ''}>${esc(v.label || v.name || v.id)}</option>`).join('')}</select>` : ''}${button(t('查看差异并应用'), 'study-preview', 'small', html`data-id="${esc(p.id)}"`)}</article>`).join('')}</div><div class="notice info compact">频道宽度 MHz、PHY 名义速率 Mbps、业务 offered Mbps 是三个不同量。应用 TTL 到期不能解释为 PHY 丢包。</div>`);
  if (!study) return catalog;
  const radio = state.draft.network.radio_profiles.find(p => p.id === study.radio_profile_id) || state.draft.network.radio_profiles[0];
  const quantities = deriveStudyQuantities(state.draft),
    reference = quantities?.free_space_reference_loss_db,
    noise = quantities?.integrated_noise_dbm;
  const sourceRows = studyFieldRows(state.draft);
  const sourced = (path, label, options = {}) => {
    const match = sourceRows.find(row => row.path === path.replace('network.study.', '')) || {};
    return input(path, label, {
      ...options,
      hint: html`${t(STUDY_EVIDENCE_CLASSES[match.evidence_class] || 'Research reference')}${match.modified_from_profile ? t(' · 已修改原档') : ''}${match.note ? ' · ' + t(match.note) : ''}`
    });
  };
  return catalog + panel(html`研究档 ${esc(study.profile_id)} · v${esc(study.profile_version)}`, t('期望参数独立版本化。R0 保留原值作复现，R1 只修正频率对应的参考损耗。'), html`<div class="metric-strip"><div class="metric"><small>信道宽度</small><strong>${radio?.channel_width_mhz ?? '—'} <em>MHz</em></strong></div><div class="metric"><small>PHY 名义速率</small><strong>${study.phy.nominal_rate_mbps ?? '—'} <em>Mbps</em></strong></div><div class="metric"><small>每源 offered load</small><strong>${study.traffic.offered_load_mbps ?? t('未指定')} <em>${study.traffic.offered_load_mbps === null ? '' : 'Mbps'}</em></strong></div></div><div class="form-grid three">${sourced('network.study.phy.mcs', t('固定 MCS'))}${sourced('network.study.phy.nominal_rate_mbps', t('PHY 名义速率'), {
    type: 'number',
    unit: 'Mbps',
    min: 0
  })}${sourced('network.study.phy.spatial_streams', t('空间流'), {
    type: 'number',
    min: 1,
    step: 1
  })}${sourced('network.study.phy.guard_interval_ns', 'Guard interval', {
    type: 'number',
    unit: 'ns',
    min: 0
  })}</div><div class="notice warning compact">MCS 与名义速率必须保持一致；PHY 数字不包含 MAC / IP / UDP 开销，也不保证应用 goodput。</div>`) + panel(t('传播、接收机与派生量'), t('参考损耗、指数和接收噪声分别配置，不能把额外损耗藏在错误频率里。'), html`<div class="form-grid three">${sourced('network.study.propagation.reference_distance_m', t('参考距离'), {
    type: 'number',
    unit: 'm',
    min: .01
  })}${sourced('network.study.propagation.reference_loss_db', t('参考损耗 L₀'), {
    type: 'number',
    unit: 'dB'
  })}${sourced('network.study.propagation.path_loss_exponent', t('路径损耗指数'), {
    type: 'number',
    min: .1
  })}${sourced('network.study.propagation.noise_figure_db', t('噪声系数'), {
    type: 'number',
    unit: 'dB',
    min: 0
  })}</div><div class="derived-grid"><div><small>按当前频率、参考距离推导的自由空间 L₀</small><strong>${reference?.toFixed(4) ?? '—'} dB</strong><span>Derived · 20 log₁₀(4πfd₀/c)</span></div><div><small>积分接收噪声（无外部干扰项）</small><strong>${noise?.toFixed(4) ?? '—'} dBm</strong><span>Derived · −174 + 10 log₁₀(B) + NF</span></div></div>${reference !== null && Math.abs(reference - study.propagation.reference_loss_db) > .05 ? html`<div class="notice warning compact">参考损耗与当前频率自由空间基准相差 ${(study.propagation.reference_loss_db - reference).toFixed(4)} dB。若用于 R0 原样复现应保留并注明；不能称作已校准墙损。</div>` : ''}`) + panel(t('业务、队列与应用时限'), t('永久未交付、迟到、队列溢出和 PHY 接收失败应分别统计。'), html`<div class="form-grid three">${sourced('network.study.traffic.offered_load_mbps', t('每源 offered load'), {
    type: 'number',
    unit: 'Mbps',
    min: 0
  })}${sourced('network.study.traffic.payload_bytes', t('应用 payload'), {
    type: 'number',
    unit: 'B',
    min: 1,
    step: 1
  })}${sourced('network.study.queue.max_packets', t('MAC 队列上限'), {
    type: 'number',
    unit: 'packets',
    min: 1,
    step: 1
  })}${sourced('network.study.queue.max_delay_ms', t('MAC 队列寿命'), {
    type: 'number',
    unit: 'ms',
    min: 0
  })}${sourced('network.study.traffic.observation_ttl_ms', t('观察 TTL'), {
    type: 'number',
    unit: 'ms',
    min: 0
  })}${sourced('network.study.traffic.application_deadline_ms', t('应用 deadline'), {
    type: 'number',
    unit: 'ms',
    min: 0
  })}</div><div class="notice info compact">观察 TTL 从 first_tx 计时；应用 deadline 从 generation time 计时。空白表示尚未指定，不补为零。IP TTL 是跳数，不与这些时限共用字段。</div><div class="divider"></div><h3 class="section-title">尚未执行的研究模型要求</h3>${study.research_hints.length ? study.research_hints.map(h => html`<div class="research-hint"><strong>${esc(t(h.label))}</strong>${badge(t('Unimplemented'), 'amber')}<p>${esc(t(h.note))}</p><pre class="code-preview compact">${esc(JSON.stringify(h.values, null, 2))}</pre></div>`).join('') : t('<p class="field-hint">当前研究档不加入额外几何、阴影或硬件模型。</p>')}<h3 class="section-title">字段来源与适用范围</h3><div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>字段</th><th>单位</th><th>证据类别</th></tr></thead><tbody>${sourceRows.map(row => html`<tr><td class="mono">${esc(row.path)}</td><td>${esc(row.unit || '—')}</td><td>${esc(t(STUDY_EVIDENCE_CLASSES[row.evidence_class] || row.evidence_class))}${row.modified_from_profile ? ' · modified' : ''}</td></tr>`).join('')}</tbody></table></div><div class="divider"></div><div class="source-reference-list">${Object.entries(STUDY_SOURCES).filter(([id, source]) => source.url).map(([id, source]) => html`<div><a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title || id)} ↗</a><small>${esc(t(source.scope || source.note || source.kind || 'Primary source'))}</small></div>`).join('')}</div><p class="field-hint">研究假设、硬件示例和项目配置参考各有适用范围，不证明当前场景已校准或设备已合规。未知前导、CCA、重试与聚合参数保持未知。</p>`);
}
function showHelp() {
  modalRefresh = showHelp;
  openModal(t('本地 Fixture 模式'), html`<p>这是一份可运行的配置中台原型。配置、版本和合成运行保存在当前浏览器。</p><p>BENCH、SUMO、ns-3 和 Atlas 均未连接。外部仿真控制不会在此执行。未来由 BENCH Three.js 承载生产场景，Atlas 与其共用帧和选择游标。</p><p>本地回放是独立人工合成证据，包含可见的缺失数据，所有语义评估保持 unknown。</p>`, button(t('明白'), 'modal-close', 'primary'));
}
function showStudyPreview(id, variant) {
  modalRefresh = () => showStudyPreview(id, variant);
  const result = applyStudyProfile(state.draft, id, {
    variant_id: variant
  });
  openModal(html`应用研究档 ${esc(id)} · 先查看差异`, html`<div class="notice warning compact">只修改本地草稿；不会改动服务器配置。研究先验不表示已校准。</div><div class="study-change-list">${result.diff.map(d => html`<div class="diff-row"><div class="diff-path mono">${esc(d.path)}</div><div class="diff-values"><span class="diff-before">− ${esc(JSON.stringify(d.before) ?? '(unset)')}</span><span class="diff-after">+ ${esc(JSON.stringify(d.after) ?? '(unset)')}</span></div></div>`).join('')}</div>`, html`${button(t('取消'), 'modal-close')}${button(t('确认应用到草稿'), 'study-apply', 'primary', html`data-id="${esc(id)}" data-variant="${esc(variant || '')}"`)}`);
}
function showCompilation() {
  modalRefresh = showCompilation;
  const compiled = compileConfig(state.draft);
  openModal(t('配置编译计划 · 非已解析场景'), html`<div class="notice info compact">此计划不会提交到 BENCH。适配器尚未连接，unsupported 字段必须在接入时显式处理。</div><pre class="code-preview large">${esc(JSON.stringify(compiled, null, 2))}</pre>`, html`${button(t('关闭'), 'modal-close')}${button(t('导出计划'), 'compile-export', 'primary')}`);
}
function showVersionLoad(id) {
  modalRefresh = () => showVersionLoad(id);
  const v = state.versions.find(v => v.id === id);
  openModal(html`载入 v${v.number} 到工作草稿`, t('<p>当前工作草稿将被替换。已保存版本和运行记录会保留。</p>'), html`${button(t('取消'), 'modal-close')}${button(t('确认载入'), 'version-load-confirm', 'primary', html`data-id="${esc(v.id)}"`)}`);
}
function showImport(imported) {
  modalRefresh = () => showImport(imported);
  openModal(t('导入配置'), html`<p>即将载入 <strong>${esc(imported.metadata.name)}</strong>，包含 ${imported.entities.length} 个实体模板。</p><p>此操作替换工作草稿，保留已保存版本与运行记录。</p>`, html`${button(t('取消'), 'modal-close')}<button class="button primary" id="confirm-import">确认导入</button>`);
  $('#confirm-import').addEventListener('click', () => {
    state.draft = imported;
    invalidateAuthoring();
    persist();
    closeModal();
    render();
    toast(t('配置导入成功。'));
  });
}
function languagePicker(id = 'locale') {
  return html`<label class="locale-picker" for="${id}"><span>${t('语言')}</span><select id="${id}" data-locale aria-label="${t('语言')}"><option value="zh-CN" ${getLocale() === 'zh-CN' ? 'selected' : ''}>中文</option><option value="en-US" ${getLocale() === 'en-US' ? 'selected' : ''}>English</option></select></label>`;
}
function switchLanguage(locale) {
  graphWorkbench.captureEditor();
  if (switchingLanguage) return;
  switchingLanguage = true;
  try {
    const focused = document.activeElement;
    const focusedId = focused?.id;
    const range = focused && typeof focused.selectionStart === 'number' ? [focused.selectionStart, focused.selectionEnd] : null;
    const modal = $('#modal-root');
    const fields = [...modal.querySelectorAll('input,select,textarea')].filter(el => !el.hasAttribute('data-locale')).map(el => ({
      id: el.id,
      value: el.value,
      checked: el.checked
    }));
    const error = $('#modal-error')?.textContent;
    const scroll = modal.querySelector('.modal-body')?.scrollTop;
    const refresh = modalRefresh;
    for (const el of $('#app').querySelectorAll('[data-path]')) {
      const value = el.type === 'checkbox' ? el.checked : el.type === 'number' ? el.value === '' && el.dataset.path.startsWith('network.study.traffic.') ? null : Number(el.value) : el.value;
      pathSet(el.dataset.path, value);
    }
    persist();
    const persisted = setLocale(locale);
    render();
    if (!$('#toast').classList.contains('hidden')) $('#toast').textContent = message($('#toast').dataset.message);
    if (refresh) {
      refresh();
      for (const field of fields) {
        const el = document.getElementById(field.id);
        if (el) {
          el.value = field.value;
          el.checked = field.checked;
        }
      }
      if (error && $('#modal-error')) $('#modal-error').textContent = message(error);
      if (scroll !== undefined) modal.querySelector('.modal-body').scrollTop = scroll;
    }
    const target = focusedId && document.getElementById(focusedId);
    target?.focus?.();
    if (range && target?.setSelectionRange) target.setSelectionRange(...range);
    if (!persisted) toast(t('语言偏好无法保存；本次切换仍有效。'), 'error');
  } finally {
    switchingLanguage = false;
  }
}
function motionEvidence(entity) {
  const velocity = entity.velocity_enu_mps;
  const speed = velocity ? Math.hypot(velocity[0], velocity[1]) : null;
  return html`<div class="divider"></div><h3 class="section-title">${t('State inputs')}</h3><div class="key-value"><span>${t('Horizontal speed')}</span><strong class="mono">${speed === null ? 'null' : speed.toFixed(3)} m/s</strong></div><div class="key-value"><span>${t('Vertical speed')}</span><strong class="mono">${velocity ? velocity[2].toFixed(3) : 'null'} m/s</strong></div><p class="field-hint">${t('Derived from authored ENU velocity; no native state producer is connected.')}</p><code class="source-pointer">entities/${esc(entity.entity_id)}/velocity_enu_mps</code><p class="field-hint">${t('Body extent unavailable; surface clearance remains unknown.')}</p>`;
}
