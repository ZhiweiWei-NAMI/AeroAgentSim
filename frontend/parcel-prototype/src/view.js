import { assertFrame,selectionFor } from './fixture.js';
import { translator } from './i18n.js';
import { renderScene,escapeHtml as h } from './scene.js';

const fmt=v=>Number.isFinite(v)?v.toFixed(2):'—';
const clock=t=>`${Math.floor(t/60).toString().padStart(2,'0')}:${(t%60).toFixed(1).padStart(4,'0')}`;
export function mountParcelView(root,{language='zh',onSelectionChange=()=>{},onSeek=()=>{}}={}) {
  let frame=null,selectedId='parcel.p1042',follow=false,showPlanned=false;
  let t=translator(language);
  const names=id=>{
    const e=frame&&[...frame.parcels,...frame.entities].find(item=>item.id===id);
    return e?.labelKey?t(e.labelKey):e?.label||id;
  };
  function render(){
    if(!frame){root.textContent=t('hostEmpty');return;}
    const focused=root.ownerDocument.activeElement?.getAttribute('data-focus');
    const item=selectionFor(frame,selectedId)?.entity;
    const selectedEvents=frame.events.filter(e=>e.entities.includes(selectedId));
    const lastEvent=selectedEvents.at(-1);
    const parcel=frame.parcels[0];
    const stage=parcel?.state||'unknown';
    const stageIndex=stage==='delivered'?4:['unloading_locker','on_uav','holding','replanned'].includes(stage)?3:['unloading_ugv','at_pad','loading_uav'].includes(stage)?2:stage==='on_ugv'?1:0;
    const completed=frame.events.filter(e=>e.kind==='custody'&&e.entities.includes(selectedId));
    const fields=item ? [
      ['state',t(item.state||'idle')],
      ...(item.kind==='parcel'?[['custody',names(item.custodian)],['attachment',item.attachment?`${names(item.attachment.carrierId)} · ${item.attachment.carrierId}`:item.transfer?`${names(item.transfer.from)} → ${names(item.transfer.to)}`:t('noCarrier')],['dimensions',item.dimensionsM.map(fmt).join(' × ')+' m'],['mass',`${fmt(item.massKg)} kg`]]:[]),
      ['position',item.position.map(fmt).join(' / ')+' m'],
      ...(item.velocity?[['velocity',item.velocity.map(fmt).join(' / ')+' m/s']]:[]),
      ...(item.attachment?[['localOffset',item.attachment.offsetEnu.map(fmt).join(' / ')+' m']]:[]),
      ['authority',item.source?.authority||t('unknown')],
      ['sourceFrame',item.source?.frameId||frame.frameKey],
    ]:[];
    const rule=frame.rule;
    const latestFlip=frame.timeSeconds>=78?54:frame.timeSeconds>=54?54:frame.timeSeconds>=38?38:null;
    const eventRows=(showPlanned?frame.script:frame.events).filter(e=>e.entities.includes(selectedId)||['predicate','response','schedule'].includes(e.kind));
    root.innerHTML=`
      <div class="workspace">
        <section class="scene-panel" aria-label="${h(t('scene'))}">
          <div class="panel-header"><div><span class="eyebrow">PARK / 01</span><h2>${h(t('scene'))}</h2></div><div class="scene-controls"><button data-action="follow" data-focus="follow" aria-pressed="${follow}">${h(t(follow?'followOn':'follow'))}</button><button data-action="fit" data-focus="fit">${h(t('fit'))}</button></div></div>
          <div class="scene-canvas">${renderScene(frame,{t,selectedId,follow})}<div class="scene-badge"><span class="status-dot"></span>${h(t('fixture'))}<span class="scene-badge-divider">/</span>10 Hz</div><div class="scene-footer">${h(t('mapHint'))}</div></div>
          <div class="workflow" aria-label="${h(t('workflow'))}">${[0,1,2,3,4].map((i)=>`<button class="workflow-step ${i<stageIndex?'done':''} ${i===stageIndex?'current':''}" data-seek="${[.1,6,20,29,78][i]}" data-focus="stage-${i}"><span>${i<stageIndex?'✓':String(i+1).padStart(2,'0')}</span>${h(t(`process${i}`))}</button>`).join('')}</div>
        </section>
        <aside class="inspector" aria-label="${h(t('selection'))}">
          <div class="panel-header"><div><span class="eyebrow">ENTITY INSPECTOR</span><h2>${h(t('selection'))}</h2></div><span class="lock-badge">${h(t('selected'))}</span></div>
          <div class="selected-heading"><span class="entity-symbol ${item?.kind==='parcel'?'parcel-symbol':''}">${item?.kind==='parcel'?'▣':item?.kind==='uav'?'✣':'◈'}</span><div><h3>${h(names(selectedId))}</h3><code>${h(selectedId)}</code></div></div>
          <div class="state-banner ${stage==='holding'&&item?.custodian==='uav.01'?'warning':''}"><span class="status-dot"></span>${h(t(item?.state||'idle'))}${item?.transfer?`<small>${h(t('loadProgress'))} ${Math.round(item.transfer.progress*100)}%</small>`:''}</div>
          <dl class="state-fields">${fields.map(([key,value])=>`<div><dt>${key==='velocity'?(language==='zh'?'线速度 ENU':'ENU velocity'):h(t(key))}</dt><dd>${h(value)}</dd></div>`).join('')}</dl>
          ${item?.kind==='parcel'?`<div class="custody-strip"><span>${h(t(item.transfer?'inTransfer':'custodyStable'))}</span><small>${completed.length?`${h(t('custody'))} @ ${clock(completed.at(-1).time)}`:language==='zh'?'初始保管状态':'Initial custody'}</small></div>`:''}
          <div class="evidence-pointer"><span>${h(t('evidenceReference'))}</span><code>${h(item?.source?.pointer||'—')}</code></div>
          <div class="last-event"><span>${h(t('lastEvent'))}</span><p>${lastEvent?`${clock(lastEvent.time)} · ${h(t(lastEvent.labelKey))}`:h(t('noEvents'))}</p></div>
        </aside>
      </div>
      <section class="inventory-panel"><div class="inventory-title"><h2>${h(t('entities'))}</h2><span>${h(t('keyboard'))}</span></div><div class="entity-list">${[...frame.parcels,...frame.entities].map(e=>`<button data-select="${h(e.id)}" data-focus="pick-${h(e.id)}" aria-pressed="${e.id===selectedId}" class="entity-chip"><span class="mini-symbol ${e.kind==='parcel'?'box-mini':''}">${e.kind==='parcel'?'▣':e.kind==='uav'?'✣':e.kind==='ugv'?'▰':'◈'}</span><span><b>${h(names(e.id))}</b><small>${h(e.id)}</small></span><span class="chip-state">${h(t(e.state||'idle'))}</span></button>`).join('')}</div></section>
      <div class="evidence-grid">
        <section class="rule-panel"><div class="panel-header"><div><span class="eyebrow">STATE → RULE → PREDICATE → RESPONSE</span><h2>${h(t('eventChain'))}</h2></div><span class="truth-chip ${rule?.value?'truth-true':''}">${h(t(rule?.value?'true':'false'))}</span></div><p class="subtle">${h(t('eventNote'))}</p>
          <div class="chain-nodes"><div class="chain-node ${frame.timeSeconds>=38&&frame.timeSeconds<54?'active':''}"><span>01 · ${h(t('schedule'))}</span><b>${h(t('scheduleLabel'))}</b><small>38.0–54.0 s · phase = delivery</small></div><span class="chain-arrow">→</span><div class="chain-node ${rule?.value?'active':''}"><span>02 · ${h(t('predicate'))}</span><b>${h(t('ruleLabel'))}</b><small>RSSI &lt; −90 dBm</small></div><span class="chain-arrow">→</span><div class="chain-node ${frame.timeSeconds>=39?'active':''}"><span>03 · ${h(t('response'))}</span><b>${h(t('chainAction'))}</b><small>39.0 / 43.0 / 49.0 s</small></div></div>
          <div class="rule-inputs"><div><span>delivery_phase</span><strong>${rule?.inputs.deliveryPhase?'true':'false'}</strong></div><div><span>${h(t('rssi'))}</span><strong>${rule?.inputs.rssiDbm??'—'} dBm</strong></div><div><span>${language==='zh'?'最近真值翻转':'Latest truth flip'}</span><strong>${latestFlip===null?'—':clock(latestFlip)}</strong></div><div><span>${h(t('route'))}</span><strong>${h(t(frame.activeRoute==='alternate'?'alternateRoute':'primaryRoute'))}</strong></div></div>
          <p class="honesty-note">uav.01 × link.delivery · demo.delivery-link-degraded<br>${h(t('ruleWarning'))}<br>${h(t('atlasUnknown'))}</p>
        </section>
        <section class="events-panel"><div class="panel-header"><div><span class="eyebrow">CUSTODY + RESPONSE LEDGER</span><h2>${h(t('log'))}</h2></div><label class="toggle"><input type="checkbox" data-action="planned" data-focus="planned" ${showPlanned?'checked':''}>${h(t('showPlanned'))}</label></div><div class="event-list">${eventRows.length?eventRows.slice(-12).map(e=>`<button class="event-row ${e.time>frame.timeSeconds?'future':''}" data-seek="${e.time}" data-focus="event-${h(e.id)}"><time>${clock(e.time)}</time><span class="event-marker ${e.kind==='custody'?'custody-event':''}"></span><span><b>${h(t(e.labelKey))}</b><small>${h(e.id)}${e.dependsOn?` ← ${h(e.dependsOn.join(', '))}`:''}</small></span><span class="event-arrow">↗</span></button>`).join(''):`<p class="empty-events">${h(t('noEvents'))}</p>`}</div></section>
      </div>`;
    if(focused)[...root.querySelectorAll('[data-focus]')].find(element=>element.getAttribute('data-focus')===focused)?.focus({preventScroll:true});
  }
  function click(e){
    const entity=e.target.closest('[data-select],[data-entity-id]');
    if(entity){selectTarget(entity.getAttribute('data-select')||entity.getAttribute('data-entity-id'));return;}
    const seek=e.target.closest('[data-seek]');
    if(seek){onSeek(Number(seek.dataset.seek));return;}
    const action=e.target.closest('[data-action]')?.dataset.action;
    if(action==='follow'){follow=!follow;render();}
    if(action==='fit'){follow=false;render();}
  }
  function change(e){if(e.target.dataset.action==='planned'){showPlanned=e.target.checked;render();}}
  root.addEventListener('click',click);root.addEventListener('change',change);
  function selectTarget(id){if(!frame||!selectionFor(frame,id))return false;selectedId=id;render();onSelectionChange(selectionFor(frame,id));return true;}
  return {
    setFrame(next){assertFrame(next);frame=next;if(!selectionFor(frame,selectedId))selectedId=frame.parcels[0]?.id||frame.entities[0]?.id;render();return selectionFor(frame,selectedId);},
    selectTarget,
    setLanguage(next){language=next==='en'?'en':'zh';t=translator(language);render();},
    getSelection(){return frame?selectionFor(frame,selectedId):null;},
    destroy(){root.removeEventListener('click',click);root.removeEventListener('change',change);root.replaceChildren();},
  };
}
