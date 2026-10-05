// Owned host view. Attribution: layout, inspector and interaction patterns are
// adapted from the P02 parcel prototype (validation/p02-parcel-host/source @
// 15f473a4dc0ed4f80e3000b0acef6e875c617393, file src/view.js, Apache-2.0 per the checkout's LICENSE
// the checkout's LICENSE). This copy changes the authored-fixture surfaces into
// host-evidence surfaces: unknown custody/Atlas stay unknown, rule inputs come
// from the host, entity kinds are host-declared, and every panel labels the
// evidence source (BENCH SceneState vs demo host evidence).
import { selectionFor, h } from './adapter.js';
import { renderScene } from './scene.js';
import { translator } from './i18n.js';

const fmt = v => Number.isFinite(v) ? v.toFixed(2) : '—';
const clock = t => `${Math.floor(t / 60).toString().padStart(2, '0')}:${(t % 60).toFixed(1).padStart(4, '0')}`;

/**
 * Mount the parcel view inside one host element. The component has no clock,
 * no simulation step, no network access and no command submission: the host
 * calls setFrame with the frame of its single authoritative replay cursor.
 */
export function mountParcelView(root, {
  language = 'zh',
  onSelectionChange = () => {},
  onSeek = () => {},
  entityKind = () => 'facility',
} = {}) {
  let frame = null, selectedId = null, follow = false, showPlanned = false;
  let t = translator(language);

  const names = id => {
    const e = frame && [...frame.parcels, ...frame.entities].find(item => item.id === id);
    return e?.labelKey ? t(e.labelKey) : e?.label || id;
  };

  function render() {
    if (!frame) { root.textContent = t('hostEmpty'); return; }
    const focused = root.ownerDocument.activeElement?.getAttribute('data-focus');
    const item = selectionFor(frame, selectedId)?.entity ?? null;
    const selectedEvents = frame.events.filter(e => e.entities.includes(selectedId));
    const lastEvent = selectedEvents.at(-1);
    const stage = item?.state || 'unknown';
    const stageIndex = stage === 'delivered' ? 4
      : ['unloading_locker', 'on_uav', 'holding', 'replanned'].includes(stage) ? 3
      : ['unloading_ugv', 'at_pad', 'loading_uav'].includes(stage) ? 2
      : stage === 'on_ugv' ? 1 : 0;
    const completed = frame.events.filter(e => e.kind === 'custody' && e.entities.includes(selectedId));
    const hostCtx = frame.hostContext ?? {};
    const identityNote = frame.identityComplete === false
      ? `<div class="identity-note">${h(language === 'zh' ? '身份不完整：epoch/代 未知（主机未声明场景上下文）' : 'Incomplete identity: epoch/generation UNKNOWN (host declared no scene context)')}</div>`
      : '';
    const custodyCell = item?.kind === 'parcel'
      ? (item.custodian !== null ? names(item.custodian) : t('custodyUnknown'))
      : null;

    const fields = item ? [
      ['state', item.state ? t(item.state) : t('unknown')],
      ...(item.kind === 'parcel' ? [
        ['custody', custodyCell],
        ['attachment', item.attachment
          ? `${names(item.attachment.carrierId)} · ${item.attachment.carrierId}`
          : item.transfer ? `${names(item.transfer.fromId)} → ${names(item.transfer.toId)}` : t('noCarrier')],
        ['dimensions', item.dimensionsM ? item.dimensionsM.map(fmt).join(' × ') + ` ${t('positionUnit')}` : t('hostMissing')],
        ['mass', item.massKg !== null && item.massKg !== undefined ? `${fmt(item.massKg)} ${t('massUnit')}` : t('hostMissing')],
      ] : []),
      ['position', item.position ? item.position.map(fmt).join(' / ') + ` ${t('positionUnit')}` : t('notVisibleHost')],
      ...(item.velocity ? [['velocity', item.velocity.map(fmt).join(' / ') + ` ${t('velocityUnit')}`]] : []),
      ...(item.attachment ? [['localOffset', item.attachment.offsetEnu.map(fmt).join(' / ') + ` ${t('positionUnit')}`]] : []),
      ['authority', item.source?.authority || t('unknown')],
      ['sourceFrame', item.source?.frameId || frame.frameKey],
    ] : [];

    const rule = frame.rule;
    const latestFlip = rule?.lastFlip ?? null;
    const eventRows = (showPlanned ? frame.script : frame.events)
      .filter(e => e.entities.includes(selectedId) || ['predicate', 'response', 'schedule'].includes(e.kind));
    const motionBadge = frame.motionSource === 'bench-scene-state'
      ? (frame.parcels.some(parcel => parcel.position !== null) ? 'motionSourceMixed' : 'motionSourceBench')
      : 'motionSourceNone';

    root.innerHTML = `
      <div class="workspace">
        ${identityNote}
        <section class="scene-panel" aria-label="${h(t('scene'))}">
          <div class="panel-header"><div><span class="eyebrow">${h(t('hostMode'))} / 01</span><h2>${h(t('scene'))}</h2></div><div class="scene-controls"><button data-action="follow" data-focus="follow" aria-pressed="${follow}">${h(t(follow ? 'followOn' : 'follow'))}</button><button data-action="fit" data-focus="fit">${h(t('fit'))}</button></div></div>
          <div class="scene-canvas">${renderScene(frame, { t, selectedId, follow, entityKind })}<div class="scene-badge"><span class="status-dot"></span>${h(t('hostFrameBadge'))}<span class="scene-badge-divider">/</span>${h(motionBadge)}</div><div class="scene-footer">${h(t('mapHint'))}</div></div>
          <div class="workflow" aria-label="${h(t('workflow'))}">${[0, 1, 2, 3, 4].map((i) => `<button class="workflow-step ${i < stageIndex ? 'done' : ''} ${i === stageIndex ? 'current' : ''}" data-seek="${[.1, 6, 20, 29, 78][i]}" data-focus="stage-${i}"><span>${i < stageIndex ? '✓' : String(i + 1).padStart(2, '0')}</span>${h(t(`process${i}`))}</button>`).join('')}</div>
        </section>
        <aside class="inspector" aria-label="${h(t('selection'))}">
          <div class="panel-header"><div><span class="eyebrow">ENTITY INSPECTOR</span><h2>${h(t('selection'))}</h2></div><span class="lock-badge">${h(t('selected'))}</span></div>
          <div class="selected-heading"><span class="entity-symbol ${item?.kind === 'parcel' ? 'parcel-symbol' : ''}">${item?.kind === 'parcel' ? '▣' : '◈'}</span><div><h3>${h(names(selectedId))}</h3><code>${h(selectedId ?? '—')}</code></div></div>
          <div class="state-banner ${stage === 'holding' ? 'warning' : ''}"><span class="status-dot"></span>${h(item?.state ? t(item.state) : t('unknown'))}${item?.transfer ? `<small>${h(t('loadProgress'))} ${Math.round(item.transfer.progress * 100)}%</small>` : ''}</div>
          <dl class="state-fields">${fields.map(([key, value]) => `<div><dt>${key === 'velocity' ? (language === 'zh' ? '线速度 ENU' : 'ENU velocity') : h(t(key))}</dt><dd>${h(value)}</dd></div>`).join('')}</dl>
          ${item?.kind === 'parcel' ? `<div class="custody-strip"><span>${h(t(item.transfer ? 'inTransfer' : 'custodyStable'))}</span><small>${completed.length ? `${h(t('custody'))} @ ${clock(completed.at(-1).time)}` : item.custodian === null ? h(t('custodyUnknown')) : language === 'zh' ? '初始保管状态' : 'Initial custody'}</small></div>` : ''}
          <div class="evidence-pointer"><span>${h(t('evidenceReference'))}</span><code>${h(item?.source?.pointer || '—')}</code></div>
          <div class="last-event"><span>${h(t('lastEvent'))}</span><p>${lastEvent ? `${clock(lastEvent.time)} · ${h(t(lastEvent.labelKey))}` : h(t('noEvents'))}</p></div>
        </aside>
      </div>
      <section class="inventory-panel"><div class="inventory-title"><h2>${h(t('entities'))}</h2><span>${h(t('keyboard'))}</span></div><div class="entity-list">${[...frame.parcels, ...frame.entities].map(e => `<button data-select="${h(e.id)}" data-focus="pick-${h(e.id)}" aria-pressed="${e.id === selectedId}" class="entity-chip"><span class="mini-symbol ${e.kind === 'parcel' ? 'box-mini' : ''}">${e.kind === 'parcel' ? '▣' : '◈'}</span><span><b>${h(names(e.id))}</b><small>${h(e.id)}${e.generation ? ` · ${h(t('generationShort'))} ${h(String(e.generation))}` : ''}</small></span><span class="chip-state">${h(e.state ? t(e.state) : t('unknown'))}</span></button>`).join('')}</div></section>
      <div class="evidence-grid">
        <section class="rule-panel"><div class="panel-header"><div><span class="eyebrow">STATE → RULE → PREDICATE → RESPONSE</span><h2>${h(t('eventChain'))}</h2></div><span class="truth-chip ${rule?.value === true ? 'truth-true' : ''}">${h(rule?.value === true ? t('true') : rule?.value === false ? t('false') : t('ruleUnknown'))}</span></div><p class="subtle">${h(t('eventNote'))}</p>
          <div class="chain-nodes"><div class="chain-node ${rule?.inputsKnown ? 'active' : ''}"><span>01 · ${h(t('schedule'))}</span><b>${h(t('scheduleLabel'))}</b><small>${rule.inputsKnown ? h(ruleInputSummary(rule.inputs)) : h(t('ruleInputsUnknown'))}</small></div><span class="chain-arrow">→</span><div class="chain-node ${rule?.value === true ? 'active' : ''}"><span>02 · ${h(t('predicate'))}</span><b>${h(t('ruleLabel'))}</b><small>${h(rule?.id ?? t('ruleUnknown'))}</small></div><span class="chain-arrow">→</span><div class="chain-node ${lastEvent ? 'active' : ''}"><span>03 · ${h(t('response'))}</span><b>${h(t('chainAction'))}</b><small>${lastEvent ? `${clock(lastEvent.time)} · ${h(t(lastEvent.labelKey))}` : h(t('noEvents'))}</small></div></div>
          <div class="rule-inputs">${ruleInputs(rule, t, language)}</div>
          <p class="honesty-note">${h(rule?.id ?? '—')} · ${h(rule?.engine ?? t('ruleUnknown'))}<br>${h(t('ruleWarning'))}<br>${h(t('atlasUnknown'))}</p>
        </section>
        <section class="events-panel"><div class="panel-header"><div><span class="eyebrow">CUSTODY + RESPONSE LEDGER</span><h2>${h(t('log'))}</h2></div><label class="toggle"><input type="checkbox" data-action="planned" data-focus="planned" ${showPlanned ? 'checked' : ''}>${h(t('showPlanned'))}</label></div><div class="event-list">${eventRows.length ? eventRows.slice(-12).map(e => `<button class="event-row ${e.time > frame.timeSeconds ? 'future' : ''}" data-seek="${e.time}" data-focus="event-${h(e.id)}"><time>${clock(e.time)}</time><span class="event-marker ${e.kind === 'custody' ? 'custody-event' : ''}"></span><span><b>${h(t(e.labelKey))}</b><small>${h(e.id)}${e.dependsOn ? ` ← ${h(e.dependsOn.join(', '))}` : ''}</small></span><span class="event-arrow">↗</span></button>`).join('') : `<p class="empty-events">${h(t('noEvents'))}</p>`}</div></section>
      </div>`;
    if (focused) [...root.querySelectorAll('[data-focus]')].find(element => element.getAttribute('data-focus') === focused)?.focus({ preventScroll: true });
  }

  function ruleInputSummary(inputs) {
    return Object.entries(inputs ?? {}).slice(0, 3).map(([key, value]) => `${key}=${value}`).join(' · ');
  }

  function ruleInputs(rule, t, language) {
    if (!rule || rule.inputs === null) {
      return `<div><span>${h(t('ruleInputsUnknown'))}</span><strong>—</strong></div><div><span>${h(t('atlasStaysUnknown'))}</span><strong>${h(t('unknown'))}</strong></div>`;
    }
    const entries = Object.entries(rule.inputs);
    return entries.map(([key, value]) =>
      `<div><span>${h(key)}</span><strong>${h(String(value))}</strong></div>`).join('') +
      `<div><span>${language === 'zh' ? '最近真值翻转' : 'Latest truth flip'}</span><strong>${rule.lastFlip === null || rule.lastFlip === undefined ? '—' : clock(rule.lastFlip)}</strong></div>` +
      `<div><span>${h(t('inputSource'))}</span><strong>${h(rule.inputSource ?? t('hostMissing'))}</strong></div>`;
  }

  function click(e) {
    const entity = e.target.closest('[data-select],[data-entity-id]');
    if (entity) { selectTarget(entity.getAttribute('data-select') || entity.getAttribute('data-entity-id')); return; }
    const seek = e.target.closest('[data-seek]');
    if (seek) { onSeek(Number(seek.dataset.seek)); return; }
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (action === 'follow') { follow = !follow; render(); }
    if (action === 'fit') { follow = false; render(); }
  }
  function change(e) { if (e.target.dataset.action === 'planned') { showPlanned = e.target.checked; render(); } }
  root.addEventListener('click', click);
  root.addEventListener('change', change);

  function selectTarget(id) {
    if (!frame || !selectionFor(frame, id)) return false;
    selectedId = id;
    render();
    onSelectionChange(selectionFor(frame, id));
    return true;
  }

  /** Clear the view selection (SelectionState null direction). */
  function clearSelection() {
    selectedId = null;
    render();
    onSelectionChange(null);
  }

  return {
    setFrame(next) {
      if (next !== null) {
        frame = next;
        if (!selectionFor(frame, selectedId)) {
          selectedId = frame.parcels[0]?.id ?? frame.entities[0]?.id ?? null;
        }
        render();
      }
      return frame ? selectionFor(frame, selectedId) : null;
    },
    selectTarget,
    clearSelection,
    setLanguage(next) { language = next === 'en' ? 'en' : 'zh'; t = translator(language); render(); },
    getSelection() { return frame ? selectionFor(frame, selectedId) : null; },
    destroy() { root.removeEventListener('click', click); root.removeEventListener('change', change); root.replaceChildren(); },
  };
}
