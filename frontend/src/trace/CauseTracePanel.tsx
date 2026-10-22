import { useLayoutEffect, useRef } from 'react';
import type { EntityKey, Instant } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { Details } from '../console/Details';
import { displayTime } from '../pages/display-time';
import { readableLabel } from '../pages/inspection-format';
import { entityId } from '../viewport/bindings';
import { idLabel, writerName, type CauseRef, type EffectView, type FollowUpView, type LinkBasis, type PredicateView, type TraceModel, type TriggerView } from './cause-trace';
import { useI18n } from '../i18n/I18nProvider';
import './trace.css';

interface Props {
  store: TemporalFeedStore; model?: TraceModel; canGoBack: boolean;
  onOpen: (ref: CauseRef) => void; onBack: () => void; onClose: () => void;
  onSelectEntity: (key: EntityKey) => void; onSeek: (cut: number) => void;
}
const when = (store: TemporalFeedStore, instant?: Instant) => instant ? displayTime(instant.ns, store.header.start.ns) : '';
/** Never present a matched or co-occurring record as a causal claim. */
const basisLabel = (basis: LinkBasis, locale: string) => basis === 'recorded'
  ? (locale === 'zh-CN' ? '记录关联' : 'recorded link')
  : (locale === 'zh-CN' ? '同一时刻上下文' : 'same-cut context');
function Basis({ basis }: { basis?: LinkBasis }) {
  const { locale } = useI18n();
  return basis ? <small className="ct-note"> · <span className="ct-basis" data-basis={basis}>{basisLabel(basis, locale)}</span></small> : null;
}

function EntityChip({ store, entity, onSelectEntity }: { store: TemporalFeedStore; entity: EntityKey; onSelectEntity: (key: EntityKey) => void }) {
  const { locale } = useI18n();
  return <button type="button" className="ct-entity" onClick={() => onSelectEntity(entity)}>{idLabel(store, entityId(entity),locale)}</button>;
}

function PredicateCard({ store, predicate, onOpen, onSelectEntity }: { store: TemporalFeedStore; predicate: PredicateView; onOpen: (ref: CauseRef) => void; onSelectEntity: (key: EntityKey) => void }) {
  const { t, locale } = useI18n();
  return <div className="ct-predicate ct-split">
    <div>{predicate.expression && <p className="ct-rule"><span>{t('The rule')}</span> {predicate.expression}</p>}
    </div>
    {predicate.reads.length > 0 && <div>
      <p className="ct-sub">{t('Values it looked at')}</p>
      <ul className="ct-reads">{predicate.reads.map(read => <li key={`${entityId(read.entity)}/${read.fieldId}`}>
        <EntityChip store={store} entity={read.entity} onSelectEntity={onSelectEntity} /> <span className="ct-field">{read.fieldLabel}</span> = <strong>{read.value}</strong>
        <small>{read.writtenAt ? t('set by {writer} at {at}', {writer:writerName(read.writer,locale),at:when(store,read.writtenAt)}) : t('set by {writer}', {writer:writerName(read.writer,locale)})}</small>
        {read.ref && <button type="button" className="ct-link" onClick={() => onOpen(read.ref!)}>{t('Why this value?')}</button>}
      </li>)}</ul>
    </div>}
  </div>;
}

function Trigger({ store, trigger, onOpen, onSelectEntity }: { store: TemporalFeedStore; trigger: TriggerView; onOpen: (ref: CauseRef) => void; onSelectEntity: (key: EntityKey) => void }) {
  const { t, locale } = useI18n();
  const linkLabel = trigger.basis ? locale === 'zh-CN' ? '打开匹配的记录' : 'Open matching record' : t('See where it came from');
  return <li className="ct-card ct-trigger">
    <h4>{trigger.basis ? locale === 'zh-CN' ? '记录的触发条件' : 'Recorded trigger' : t('What set it off')}</h4>
    <p className="ct-main">{trigger.summary}{trigger.at ? <small> · {when(store, trigger.at)}</small> : null}<Basis basis={trigger.basis} /></p>
    {trigger.predicate && <PredicateCard store={store} predicate={trigger.predicate} onOpen={onOpen} onSelectEntity={onSelectEntity} />}
    {trigger.source && <p className="ct-source">{trigger.source}{trigger.ref && <button type="button" className="ct-link" onClick={() => onOpen(trigger.ref!)}>{linkLabel}</button>}</p>}
    {!trigger.source && trigger.ref && trigger.kind !== 'predicate' && <button type="button" className="ct-link" onClick={() => onOpen(trigger.ref!)}>{linkLabel}</button>}
  </li>;
}

function Effects({ store, effects, onSeek }: { store: TemporalFeedStore; effects: EffectView[]; onSeek: (cut: number) => void }) {
  const { t, locale } = useI18n();
  const basis = effects.find(effect => effect.basis)?.basis;
  return <li className="ct-card ct-effects">
    <h4>{basis ? locale === 'zh-CN' ? '同一时刻的状态记录' : 'Same-cut state records' : t('What changed in the shared state')}</h4>
    {basis && <p className="ct-note"><span className="ct-basis" data-basis={basis}>{basisLabel(basis, locale)}</span></p>}
    {effects.length === 0 ? <p className="ct-empty">{t('No shared state fields were written at this step.')}</p> : <>
      <ul className="ct-changes">{effects.map(effect => <li key={`${entityId(effect.entity)}/${effect.fieldId}`}>
        <div><span className="ct-entity-name">{effect.entityLabel}</span> · <span className="ct-field">{effect.fieldLabel}</span>{effect.unit ? <small> ({effect.unit})</small> : null}</div>
        <div className="ct-delta"><span className="ct-before">{effect.hadBefore ? effect.before : t('not set yet')}</span><span aria-label={t('changed to')}>→</span><strong className="ct-after">{effect.after}</strong></div>
        <div className={`ct-writer${effect.writerOk ? '' : ' ct-writer-bad'}`}>{effect.writerOk ? '✓ ' : '⚠ '}{t('Only {writer} may write this field', {writer:writerName(effect.declaredWriter ?? effect.writer,locale)})}{effect.writerOk ? '' : t(' (this write came from {writer})', {writer:writerName(effect.writer,locale)})}</div>
      </li>)}</ul>
      <p className="ct-note">{t('Every shared field has exactly one writer, so you can always tell who changed it.')}</p>
      <button type="button" className="ct-link" onClick={() => onSeek(effects[0].cut)}>{t('Show the world right after this')}</button>
    </>}
  </li>;
}

function FollowUps({ items, onOpen, store }: { items: FollowUpView[]; onOpen: (ref: CauseRef) => void; store: TemporalFeedStore }) {
  const { t, locale } = useI18n();
  return <li className="ct-card ct-next">
    <h4>{items.some(item => item.basis) ? locale === 'zh-CN' ? '已记录的后续活动' : 'Recorded follow-ups' : t('What happened next')}</h4>
    {items.length === 0 ? <p className="ct-empty">{locale === 'zh-CN' ? '没有关联的后续记录。' : 'No further linked records.'}</p> : <ul className="ct-follow">{items.map((item, index) => <li key={index} className={`ct-follow-${item.kind}`}>
      {item.ref ? <button type="button" className="ct-follow-btn" onClick={() => onOpen(item.ref!)}><span>{item.label}</span><small>{item.detail}{item.at ? ` · ${when(store, item.at)}` : ''}{item.basis ? <> · <span className="ct-basis" data-basis={item.basis}>{basisLabel(item.basis, locale)}</span></> : null}</small></button>
        : <div className="ct-follow-plain"><span>{item.label}</span><small>{item.detail}{item.at ? ` · ${when(store, item.at)}` : ''}{item.basis ? <> · <span className="ct-basis" data-basis={item.basis}>{basisLabel(item.basis, locale)}</span></> : null}</small></div>}
    </li>)}</ul>}
  </li>;
}

/** "Why did this happen?" vertical story: what set it off, what ran, what changed, what came next. */
export function CauseTracePanel({ store, model, canGoBack, onOpen, onBack, onClose, onSelectEntity, onSeek }: Props) {
  const { t, locale } = useI18n();
  const root = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    // Event lists can leave their shared dock scrolled far down. A new cause
    // must open at its heading; live follow-up arrivals retain reading position.
    for (let parent = root.current?.parentElement; parent; parent = parent.parentElement) {
      if (parent.scrollHeight > parent.clientHeight) { parent.scrollTop = 0; break; }
    }
  }, [model?.focus, model?.cut, model?.heading]);
  return <section ref={root} className="cause-trace" aria-label={t('Why did this happen?')} data-testid="cause-trace">
    <header className="ct-head">
      <h3>{t('Why did this happen?')}</h3>
      <span>{canGoBack && <button type="button" className="ct-small" onClick={onBack}>{t('← Back')}</button>}<button type="button" className="ct-small" onClick={onClose} aria-label={t('Close explanation')}>{t('Close')}</button></span>
    </header>
    {!model ? <p className="ct-empty">{t('That moment is not loaded yet. Let the timeline load further and try again.')}</p> : <>
      <h4 className="ct-title">{model.heading}</h4>
      {model.subtitle && <p className="ct-when">{model.subtitle}</p>}
      {model.choices.length > 0 && <div className="ct-choices"><p className="ct-sub">{model.choices.every(choice => choice.basis === 'same-cut') ? locale === 'zh-CN' ? '同一时刻上下文' : 'Same-cut context' : locale === 'zh-CN' ? '已记录的关联' : 'Recorded links'}</p>{model.choices.map((choice, index) => <button key={index} type="button" className="ct-link" onClick={() => onOpen(choice.ref)}>{choice.label}<Basis basis={choice.basis} /></button>)}</div>}
      <ol className={`ct-story${model.focus === 'message' ? ' ct-message-story' : ''}`}>
        {model.trigger && <Trigger store={store} trigger={model.trigger} onOpen={onOpen} onSelectEntity={onSelectEntity} />}
        {model.predicate && <li className="ct-card ct-trigger"><h4>{t('The condition')}</h4><p className="ct-main">{t('{condition} is ', {condition:model.predicate.label})}<strong>{model.predicate.value === null ? t('unknown') : model.predicate.value ? t('true') : t('false')}</strong><small> · {when(store, model.predicate.at)}</small></p><PredicateCard store={store} predicate={model.predicate} onOpen={onOpen} onSelectEntity={onSelectEntity} /></li>}
        {model.sender && (model.sender.ref || !model.trigger) && <li className="ct-card ct-trigger"><h4>{model.sender.basis ? locale === 'zh-CN' ? '匹配的已记录步骤' : 'Matching recorded step' : t('Where it came from')}</h4><p className="ct-main">{model.sender.text}<Basis basis={model.sender.basis} /></p>{model.sender.ref && <button type="button" className="ct-link" onClick={() => onOpen(model.sender!.ref!)}>{t('See that step')}</button>}</li>}
        {model.step && <li className="ct-card ct-step"><h4>{t('What ran')}</h4>
          <p className="ct-main">{t('Event chain')} <strong>{model.step.chainLabel}</strong></p>
          <p className="ct-states">{model.step.fromState ? <><span className="ct-state">{readableLabel(model.step.fromState,locale)}</span> → </> : null}<span className="ct-state ct-state-to">{readableLabel(model.step.toState,locale)}</span><small> · {t('step “{step}”', {step:model.step.stepId})}</small></p>
          {model.step.actions.length > 0 && <ul className="ct-actions">{model.step.actions.map(action => <li key={action.id} className={`ct-action-${action.kind}`}>{action.text}</li>)}</ul>}
        </li>}
        {(model.step || model.focus === 'change' || model.effects.length > 0) && <Effects store={store} effects={model.effects} onSeek={onSeek} />}
        {(model.focus !== 'moment' || model.followUps.length > 0) && <FollowUps items={model.followUps} onOpen={onOpen} store={store} />}
      </ol>
      {model.involved.length > 0 && <p className="ct-involved">{locale === 'zh-CN' ? '涉及对象：' : 'Involved objects:'} {model.involved.map(key => <EntityChip key={entityId(key)} store={store} entity={key} onSelectEntity={onSelectEntity} />)}</p>}
      <div className="ct-foot">{model.notes.map((note, index) => <p key={index} className="ct-note">{note}</p>)}</div>
      <Details title={t('Technical details')} buttonLabel={t('Technical details')}><pre>{exactValue({ focus: model.focus, cut: model.cut, step: model.step?.technical, predicate: model.predicate?.technical ?? model.trigger?.predicate?.technical })}</pre></Details>
    </>}
  </section>;
}
