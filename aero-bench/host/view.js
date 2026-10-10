// Owned host view. Attribution: layout, inspector and interaction patterns are
// adapted from the P02 parcel prototype (validation/p02-parcel-host/source @
// 15f473a4dc0ed4f80e3000b0acef6e875c617393, file src/view.js, Apache-2.0 per the checkout's LICENSE
// the checkout's LICENSE). This copy changes the authored-fixture surfaces into
// host-evidence surfaces: unknown custody/Atlas stay unknown, rule inputs come
// from the host, entity kinds are host-declared, and every panel labels the
// evidence source (BENCH SceneState vs demo host evidence).
// PR12 truth-in-source pass: the motion-feed identity is typed input (never
// inferred), feed badges/banner texts are localized through i18n, the response
// chain node binds ONLY to an explicit causal reference, the attachment field
// shows the carrier id once when its display name is the same id, and on
// narrow screens stable-ID chips are overlaid in screen space (real projected
// coordinates — never fabricated positions) so cargo IDs stay readable.
import { selectionFor, h, project, UNKNOWN_IDENTITY, FEED_MOTION_DEMO, FEED_MOTION_BENCH, HOST_DEMO_AUTHORITY } from './adapter.js';
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
  // Typed motion-feed identity, declared by the page (setFeedMotion). null
  // falls back to the frame's own typed value; neither is ever inferred from
  // ids, provider names or digests.
  let feedMotion = null;
  let t = translator(language);

  const names = id => {
    const e = frame && [...frame.parcels, ...frame.entities].find(item => item.id === id);
    return e?.labelKey ? t(e.labelKey) : e?.label || id;
  };

  /** The effective typed feed identity for this frame. */
  const activeFeed = f => feedMotion ?? f.feedMotion ?? UNKNOWN_IDENTITY;

  /** Localized motion pill: the fed identity plus parcel-evidence status. */
  function motionBadgeText(f) {
    const feed = activeFeed(f);
        const key = feed === FEED_MOTION_BENCH ? 'motionSourceBench'
      : feed === FEED_MOTION_DEMO ? 'motionSourceHost'
      : 'motionSourceNone';
    return t(key);
  }

  /** Localized parcel-evidence pill, separate from the motion identity. */
  function parcelBadgeText(f) {
    const demoEvidence = f.parcels.some(parcel => parcel.demoEvidence === true);
    return t(demoEvidence ? 'feedParcelDemo' : 'feedParcelUnknown');
  }

  function render() {
    if (!frame) { root.textContent = t('hostEmpty'); return; }
    const focused = root.ownerDocument.activeElement?.getAttribute('data-focus');
    const item = selectionFor(frame, selectedId)?.entity ?? null;
    const rule = frame.rule;
    const selectedEvents = frame.events.filter(e => e.entities.includes(selectedId));
    const lastEvent = selectedEvents.at(-1);
    const stage = item?.state || 'unknown';
    const stageIndex = stage === 'delivered' ? 4
      : ['unloading_locker', 'on_uav', 'holding', 'replanned'].includes(stage) ? 3
      : ['unloading_ugv', 'at_pad', 'loading_uav'].includes(stage) ? 2
      : stage === 'on_ugv' ? 1 : 0;
    const completed = frame.events.filter(e => e.kind === 'custody' && e.entities.includes(selectedId));
    const identityNote = frame.identityComplete === false
      ? `<div class="identity-note">${h(language === 'zh' ? '身份不完整：epoch/代 未知（主机未声明场景上下文）' : 'Incomplete identity: epoch/generation UNKNOWN (host declared no scene context)')}</div>`
      : '';
    const custodyCell = item?.kind === 'parcel'
      ? (item.custodian !== null ? names(item.custodian) : t('custodyUnknown'))
      : null;
    // Attachment shows the carrier id once: when the display name equals the
    // id there is no second rendering. Nothing is removed from the data.
    const attachmentCell = item?.attachment
      ? (names(item.attachment.carrierId) === item.attachment.carrierId
        ? item.attachment.carrierId
        : `${names(item.attachment.carrierId)} · ${item.attachment.carrierId}`)
      : item?.transfer ? `${names(item.transfer.fromId)} → ${names(item.transfer.toId)}` : t('noCarrier');

    const fields = item ? [
      ['state', item.state ? t(item.state) : t('unknown')],
      ...(item.kind === 'parcel' ? [
        ['custody', custodyCell],
        ['attachment', attachmentCell],
        ['dimensions', item.dimensionsM ? item.dimensionsM.map(fmt).join(' × ') + ` ${t('positionUnit')}` : t('hostMissing')],
        ['mass', item.massKg !== null && item.massKg !== undefined ? `${fmt(item.massKg)} ${t('massUnit')}` : t('hostMissing')],
      ] : []),
      ['position', item.position ? item.position.map(fmt).join(' / ') + ` ${t('positionUnit')}` : t('notVisibleHost')],
      ...(item.velocity ? [['velocity', item.velocity.map(fmt).join(' / ') + ` ${t('velocityUnit')}`]] : []),
      ...(item.attachment ? [['localOffset', item.attachment.offsetEnu.map(fmt).join(' / ') + ` ${t('positionUnit')}`]] : []),
      ['authority', item.source?.authority || t('unknown')],
      ['sourceFrame', item.source?.frameId || frame.frameKey],
    ] : [];

    // Response node: bound only by the frame's EXPLICIT causal reference
    // (adapter ruleResponse: response_rule_id + response_flip_time_seconds
    // declared by the source). Without one the response is UNKNOWN/unbound —
    // recent custody events are records, never inferred responses.
    const response = frame.ruleResponse ?? null;
    const responseDetail = response
      ? `${clock(response.time)} · ${h(t(response.labelKey))}`
      : h(t('responseUnbound'));
    const eventRows = (showPlanned ? frame.script : frame.events)
      .filter(e => e.entities.includes(selectedId) || ['predicate', 'response', 'schedule'].includes(e.kind));

    root.innerHTML = `
      <div class="workspace">
        ${identityNote}
        <section class="scene-panel" aria-label="${h(t('scene'))}">
          <div class="panel-header"><div><span class="eyebrow">${h(t('hostMode'))} / 01</span><h2>${h(t('scene'))}</h2></div><div class="scene-controls"><button data-action="follow" data-focus="follow" aria-pressed="${follow}">${h(t(follow ? 'followOn' : 'follow'))}</button><button data-action="fit" data-focus="fit">${h(t('fit'))}</button></div></div>
          <div class="scene-canvas">${renderScene(frame, { t, selectedId, follow, entityKind })}<div class="scene-label-overlay"></div><div class="scene-badge"><span class="status-dot"></span>${h(t('hostFrameBadge'))}<span class="scene-badge-divider">/</span>${h(motionBadgeText(frame))}<span class="scene-badge-divider">/</span>${h(parcelBadgeText(frame))}</div><div class="scene-footer">${h(t('mapHint'))}</div></div>
          <div class="workflow" aria-label="${h(t('workflow'))}">${[0, 1, 2, 3, 4].map((i) => `<button class="workflow-step ${i < stageIndex ? 'done' : ''} ${i === stageIndex ? 'current' : ''}" data-seek="${[.1, 6, 20, 29, 78][i]}" data-focus="stage-${i}"><span>${i < stageIndex ? '✓' : String(i + 1).padStart(2, '0')}</span>${h(t(`process${i}`))}</button>`).join('')}</div>
        </section>
        <aside class="inspector" aria-label="${h(t('selection'))}">
          <div class="panel-header"><div><span class="eyebrow">ENTITY INSPECTOR</span><h2>${h(t('selection'))}</h2></div><span class="lock-badge">${h(t('selected'))}</span></div>
          <div class="selected-heading"><span class="entity-symbol ${item?.kind === 'parcel' ? 'parcel-symbol' : ''}">${item?.kind === 'parcel' ? '▣' : '◈'}</span><div><h3>${h(names(selectedId))}</h3><code>${h(selectedId ?? '—')}</code></div></div>
          <div class="state-banner ${stage === 'holding' ? 'warning' : ''}"><span class="status-dot"></span>${h(item?.state ? t(item.state) : t('unknown'))}${item?.transfer ? `<small>${h(t('loadProgress'))} ${Math.round(item.transfer.progress * 100)}%</small>` : ''}</div>
          <dl class="state-fields">${fields.map(([key, value]) => `<div data-field="${h(key)}"><dt>${key === 'velocity' ? (language === 'zh' ? '线速度 ENU' : 'ENU velocity') : h(t(key))}</dt><dd>${h(value)}</dd></div>`).join('')}</dl>
          ${item?.kind === 'parcel' ? `<div class="custody-strip"><span>${h(t(item.transfer ? 'inTransfer' : 'custodyStable'))}</span><small>${completed.length ? `${h(t('custody'))} @ ${clock(completed.at(-1).time)}` : item.custodian === null ? h(t('custodyUnknown')) : language === 'zh' ? '初始保管状态' : 'Initial custody'}</small></div>` : ''}
          <div class="evidence-pointer"><span>${h(t('evidenceReference'))}</span><code>${h(item?.source?.pointer || '—')}</code></div>
          <div class="last-event"><span>${h(t('lastEvent'))}</span><p>${lastEvent ? `${clock(lastEvent.time)} · ${h(t(lastEvent.labelKey))}` : h(t('noEvents'))}</p></div>
        </aside>
      </div>
      <section class="inventory-panel"><div class="inventory-title"><h2>${h(t('entities'))}</h2><span>${h(t('keyboard'))}</span></div><div class="entity-list">${[...frame.parcels, ...frame.entities].map(e => `<button data-select="${h(e.id)}" data-focus="pick-${h(e.id)}" aria-pressed="${e.id === selectedId}" class="entity-chip"><span class="mini-symbol ${e.kind === 'parcel' ? 'box-mini' : ''}">${e.kind === 'parcel' ? '▣' : '◈'}</span><span><b>${h(names(e.id))}</b><small>${h(e.id)}${e.generation ? ` · ${h(t('generationShort'))} ${h(String(e.generation))}` : ''}</small></span><span class="chip-state">${h(e.state ? t(e.state) : t('unknown'))}</span></button>`).join('')}</div></section>
      <div class="evidence-grid">
        <section class="rule-panel"><div class="panel-header"><div><span class="eyebrow">STATE → RULE → PREDICATE → RESPONSE</span><h2>${h(t('eventChain'))}</h2></div><span class="truth-chip ${rule?.value === true ? 'truth-true' : ''}">${h(rule?.value === true ? t('true') : rule?.value === false ? t('false') : t('ruleUnknown'))}</span></div><p class="subtle">${h(t('eventNote'))}</p>
          <div class="chain-nodes"><div class="chain-node ${rule?.inputsKnown ? 'active' : ''}"><span>01 · ${h(t('schedule'))}</span><b>${h(t('scheduleLabel'))}</b><small>${rule?.inputsKnown ? h(ruleInputSummary(rule.inputs)) : h(t('ruleInputsUnknown'))}</small></div><span class="chain-arrow">→</span><div class="chain-node ${rule?.value === true ? 'active' : ''}"><span>02 · ${h(t('predicate'))}</span><b>${h(t('ruleLabel'))}</b><small>${h(rule?.id ?? t('ruleUnknown'))}</small></div><span class="chain-arrow">→</span><div class="chain-node ${response ? 'active' : ''}"><span>03 · ${h(t('response'))}</span><b>${h(t('chainAction'))}</b><small>${responseDetail}</small></div></div>
          <div class="rule-inputs">${ruleInputs(rule, t, language)}</div>
          <p class="honesty-note">${h(rule?.id ?? '—')} · ${h(rule?.engine ?? t('ruleUnknown'))}<br>${h(t('ruleWarning'))}<br>${h(t('atlasUnknown'))}</p>
        </section>
        <section class="events-panel"><div class="panel-header"><div><span class="eyebrow">CUSTODY + RESPONSE LEDGER</span><h2>${h(t('log'))}</h2></div><label class="toggle"><input type="checkbox" data-action="planned" data-focus="planned" ${showPlanned ? 'checked' : ''}>${h(t('showPlanned'))}</label></div><div class="event-list">${eventRows.length ? eventRows.slice(-12).map(e => `<button class="event-row ${e.time > frame.timeSeconds ? 'future' : ''}" data-seek="${e.time}" data-focus="event-${h(e.id)}"><time>${clock(e.time)}</time><span class="event-marker ${e.kind === 'custody' ? 'custody-event' : ''}"></span><span><b>${h(t(e.labelKey))}</b><small>${h(e.id)}${e.dependsOn ? ` ← ${h(e.dependsOn.join(', '))}` : ''}</small></span><span class="event-arrow">↗</span></button>`).join('') : `<p class="empty-events">${h(t('noEvents'))}</p>`}</div></section>
      </div>`;
    if (focused) [...root.querySelectorAll('[data-focus]')].find(element => element.getAttribute('data-focus') === focused)?.focus({ preventScroll: true });
    buildLabelOverlay();
  }

  /**
   * Screen-space stable-ID chips over the scene (narrow screens only). Chips
   * use REAL projected coordinates of the drawn entities (viewBox → client
   * mapping incl. letterboxing); unknown positions produce no fake chip
   * placement — unplaced parcels anchor to the explicit unknown list corner.
   */
  function buildLabelOverlay() {
    const overlay = root.querySelector('.scene-label-overlay');
    const svg = root.querySelector('svg.park-scene');
    const canvas = root.querySelector('.scene-canvas');
    const win = root.ownerDocument.defaultView;
    if (!overlay || !svg || !canvas) return;
    overlay.replaceChildren();
    const on = win?.matchMedia?.('(max-width:850px)')?.matches ?? false;
    overlay.classList.toggle('active', on);
    if (!on) return;
    const items = [];
    for (const entity of frame.entities) {
      if (entity.position) items.push({ id: entity.id, text: entity.label ?? entity.id, position: entity.position, kind: 'entity', selected: entity.id === selectedId });
    }
    for (const parcel of frame.parcels) {
      if (parcel.position) items.push({ id: parcel.id, text: parcel.label ?? parcel.id, position: parcel.position, kind: 'parcel', selected: parcel.id === selectedId });
    }
    for (const id of frame.unresolvedParcelIds ?? []) {
      items.push({ id, text: t('parcelUnknown'), position: null, kind: 'unknown', selected: id === selectedId });
    }
    if (!items.length) return;
    const vb = svg.viewBox.baseVal;
    const srect = svg.getBoundingClientRect();
    const crect = canvas.getBoundingClientRect();
    const scale = Math.min(srect.width / vb.width, srect.height / vb.height);
    const offsetX = (srect.width - vb.width * scale) / 2 - vb.x * scale;
    const offsetY = (srect.height - vb.height * scale) / 2 - vb.y * scale;
    let unknownIndex = 0;
    const placed = [];
    items.sort((a, b) => Number(b.selected) - Number(a.selected));
    for (const item of items) {
      const chip = root.ownerDocument.createElement('button');
      chip.type = 'button';
      chip.className = `scene-label-chip ${item.kind}${item.selected ? ' selected' : ''}`;
      chip.dataset.select = item.id;
      chip.setAttribute('aria-pressed', String(item.selected));
      const name = root.ownerDocument.createElement('b');
      name.textContent = item.text;
      const code = root.ownerDocument.createElement('code');
      code.textContent = item.id;
      if (item.text !== item.id) chip.append(name);
      chip.append(code);
      chip.title = item.id;
      overlay.append(chip);
      const bounds = chip.getBoundingClientRect();
      const width = bounds.width || 150, height = bounds.height || 30;
      let anchorX = 10, anchorY = 10 + 36 * unknownIndex++;
      if (item.position) {
        const [px, py] = project(item.position);
        anchorX = offsetX + px * scale + srect.left - crect.left;
        anchorY = offsetY + py * scale + srect.top - crect.top;
      }
      const candidates = [];
      for (let ring = 0; ring < 16; ring += 1) {
        const gap = 12 + ring * (height + 6);
        candidates.push([anchorX + 10, anchorY - height - gap],
                        [anchorX - width - 10, anchorY - height - gap],
                        [anchorX + 10, anchorY + gap],
                        [anchorX - width - 10, anchorY + gap]);
      }
      const overlaps = box => placed.some(other =>
        box.x < other.x + other.w + 5 && box.x + box.w + 5 > other.x &&
        box.y < other.y + other.h + 5 && box.y + box.h + 5 > other.y);
      const boxes = candidates.map(([x, y]) => ({
        x: Math.max(6, Math.min(x, crect.width - width - 6)),
        y: Math.max(6, Math.min(y, crect.height - height - 6)), w: width, h: height,
      }));
      const box = boxes.find(candidate => !overlaps(candidate)) ?? boxes[0];
      placed.push(box);
      chip.style.left = `${box.x.toFixed(1)}px`;
      chip.style.top = `${box.y.toFixed(1)}px`;
      if (item.position) {
        // A display leader connects the offset label to the unchanged
        // projected marker. Label placement never changes source positions.
        const leader = root.ownerDocument.createElement('span');
        leader.className = 'scene-label-leader';
        const endX = Math.max(box.x, Math.min(anchorX, box.x + width));
        const endY = Math.max(box.y, Math.min(anchorY, box.y + height));
        const dx = endX - anchorX, dy = endY - anchorY;
        leader.style.left = `${anchorX}px`; leader.style.top = `${anchorY}px`;
        leader.style.width = `${Math.hypot(dx, dy)}px`;
        leader.style.transform = `rotate(${Math.atan2(dy, dx)}rad)`;
        overlay.prepend(leader);
      }

    }
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
  // Re-project the screen-space chips when the viewport changes size.
  const onResize = () => { if (frame) render(); };
  root.ownerDocument.defaultView?.addEventListener?.('resize', onResize);

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
    /**
     * Declare the typed motion-feed identity (FEED_MOTION_DEMO,
     * FEED_MOTION_BENCH, or UNKNOWN_IDENTITY). Stored verbatim, never
     * inferred; any other value renders as UNKNOWN.
     */
    setFeedMotion(next) {
      feedMotion = next === FEED_MOTION_DEMO || next === FEED_MOTION_BENCH || next === UNKNOWN_IDENTITY
        ? next
        : null;
      if (frame) render();
    },
    getFeedMotion: () => feedMotion,
    setLanguage(next) { language = next === 'en' ? 'en' : 'zh'; t = translator(language); render(); },
    getSelection() { return frame ? selectionFor(frame, selectedId) : null; },
    destroy() {
      root.removeEventListener('click', click);
      root.removeEventListener('change', change);
      root.ownerDocument.defaultView?.removeEventListener?.('resize', onResize);
      root.replaceChildren();
    },
  };
}
