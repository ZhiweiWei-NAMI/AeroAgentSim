import type { EntityKey, Instant } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { Details } from '../console/Details';
import { displayTime } from '../pages/display-time';
import { entityId } from '../viewport/bindings';
import { idLabel, writerName, type CauseRef, type EffectView, type FollowUpView, type PredicateView, type TraceModel, type TriggerView } from './cause-trace';
import './trace.css';

interface Props {
  store: TemporalFeedStore; model?: TraceModel; canGoBack: boolean;
  onOpen: (ref: CauseRef) => void; onBack: () => void; onClose: () => void;
  onSelectEntity: (key: EntityKey) => void; onSeek: (cut: number) => void;
}
const when = (store: TemporalFeedStore, instant?: Instant) => instant ? displayTime(instant.ns, store.header.start.ns) : '';

function EntityChip({ store, entity, onSelectEntity }: { store: TemporalFeedStore; entity: EntityKey; onSelectEntity: (key: EntityKey) => void }) {
  return <button type="button" className="ct-entity" onClick={() => onSelectEntity(entity)}>{idLabel(store, entityId(entity))}</button>;
}

function PredicateCard({ store, predicate, onOpen, onSelectEntity }: { store: TemporalFeedStore; predicate: PredicateView; onOpen: (ref: CauseRef) => void; onSelectEntity: (key: EntityKey) => void }) {
  return <div className="ct-predicate ct-split">
    <div>{predicate.expression && <p className="ct-rule"><span>The rule</span> {predicate.expression}</p>}
    </div>
    {predicate.reads.length > 0 && <div>
      <p className="ct-sub">Values it looked at</p>
      <ul className="ct-reads">{predicate.reads.map(read => <li key={`${entityId(read.entity)}/${read.fieldId}`}>
        <EntityChip store={store} entity={read.entity} onSelectEntity={onSelectEntity} /> <span className="ct-field">{read.fieldLabel}</span> = <strong>{read.value}</strong>
        <small>set by {writerName(read.writer)}{read.writtenAt ? ` at ${when(store, read.writtenAt)}` : ''}</small>
        {read.ref && <button type="button" className="ct-link" onClick={() => onOpen(read.ref!)}>Why this value?</button>}
      </li>)}</ul>
    </div>}
  </div>;
}

function Trigger({ store, trigger, onOpen, onSelectEntity }: { store: TemporalFeedStore; trigger: TriggerView; onOpen: (ref: CauseRef) => void; onSelectEntity: (key: EntityKey) => void }) {
  return <li className="ct-card ct-trigger">
    <h4>What set it off</h4>
    <p className="ct-main">{trigger.summary}{trigger.at ? <small> · {when(store, trigger.at)}</small> : null}</p>
    {trigger.predicate && <PredicateCard store={store} predicate={trigger.predicate} onOpen={onOpen} onSelectEntity={onSelectEntity} />}
    {trigger.source && <p className="ct-source">{trigger.source}{trigger.ref && <button type="button" className="ct-link" onClick={() => onOpen(trigger.ref!)}>See where it came from</button>}</p>}
    {!trigger.source && trigger.ref && trigger.kind !== 'predicate' && <button type="button" className="ct-link" onClick={() => onOpen(trigger.ref!)}>See where it came from</button>}
  </li>;
}

function Effects({ store, effects, onSeek }: { store: TemporalFeedStore; effects: EffectView[]; onSeek: (cut: number) => void }) {
  return <li className="ct-card ct-effects">
    <h4>What changed in the shared state</h4>
    {effects.length === 0 ? <p className="ct-empty">No shared state fields were written at this step.</p> : <>
      <ul className="ct-changes">{effects.map(effect => <li key={`${entityId(effect.entity)}/${effect.fieldId}`}>
        <div><span className="ct-entity-name">{effect.entityLabel}</span> · <span className="ct-field">{effect.fieldLabel}</span>{effect.unit ? <small> ({effect.unit})</small> : null}</div>
        <div className="ct-delta"><span className="ct-before">{effect.hadBefore ? effect.before : 'not set yet'}</span><span aria-label="changed to">→</span><strong className="ct-after">{effect.after}</strong></div>
        <div className={`ct-writer${effect.writerOk ? '' : ' ct-writer-bad'}`}>{effect.writerOk ? '✓ ' : '⚠ '}Only {writerName(effect.declaredWriter ?? effect.writer)} may write this field{effect.writerOk ? '' : ` (this write came from ${writerName(effect.writer)})`}</div>
      </li>)}</ul>
      <p className="ct-note">Every shared field has exactly one writer, so you can always tell who changed it.</p>
      <button type="button" className="ct-link" onClick={() => onSeek(effects[0].cut)}>Show the world right after this</button>
    </>}
  </li>;
}

function FollowUps({ items, onOpen, store }: { items: FollowUpView[]; onOpen: (ref: CauseRef) => void; store: TemporalFeedStore }) {
  return <li className="ct-card ct-next">
    <h4>What happened next</h4>
    {items.length === 0 ? <p className="ct-empty">Nothing else was recorded as a result.</p> : <ul className="ct-follow">{items.map((item, index) => <li key={index} className={`ct-follow-${item.kind}`}>
      {item.ref ? <button type="button" className="ct-follow-btn" onClick={() => onOpen(item.ref!)}><span>{item.label}</span><small>{item.detail}{item.at ? ` · ${when(store, item.at)}` : ''}</small></button>
        : <div className="ct-follow-plain"><span>{item.label}</span><small>{item.detail}{item.at ? ` · ${when(store, item.at)}` : ''}</small></div>}
    </li>)}</ul>}
  </li>;
}

/** "Why did this happen?" vertical story: what set it off, what ran, what changed, what came next. */
export function CauseTracePanel({ store, model, canGoBack, onOpen, onBack, onClose, onSelectEntity, onSeek }: Props) {
  return <section className="cause-trace" aria-label="Why did this happen?" data-testid="cause-trace">
    <header className="ct-head">
      <h3>Why did this happen?</h3>
      <span>{canGoBack && <button type="button" className="ct-small" onClick={onBack}>← Back</button>}<button type="button" className="ct-small" onClick={onClose} aria-label="Close explanation">Close</button></span>
    </header>
    {!model ? <p className="ct-empty">That moment is not loaded yet. Let the timeline load further and try again.</p> : <>
      <h4 className="ct-title">{model.heading}</h4>
      {model.subtitle && <p className="ct-when">{model.subtitle}</p>}
      {model.choices.length > 0 && <div className="ct-choices"><p className="ct-sub">Other things happened at this moment</p>{model.choices.map((choice, index) => <button key={index} type="button" className="ct-link" onClick={() => onOpen(choice.ref)}>{choice.label}</button>)}</div>}
      <ol className="ct-story">
        {model.trigger && <Trigger store={store} trigger={model.trigger} onOpen={onOpen} onSelectEntity={onSelectEntity} />}
        {model.predicate && <li className="ct-card ct-trigger"><h4>The condition</h4><p className="ct-main">{model.predicate.label} is <strong>{model.predicate.value === null ? 'unknown' : model.predicate.value ? 'true' : 'false'}</strong><small> · {when(store, model.predicate.at)}</small></p><PredicateCard store={store} predicate={model.predicate} onOpen={onOpen} onSelectEntity={onSelectEntity} /></li>}
        {model.sender && <li className="ct-card ct-trigger"><h4>Where it came from</h4><p className="ct-main">{model.sender.text}</p>{model.sender.ref && <button type="button" className="ct-link" onClick={() => onOpen(model.sender!.ref!)}>See that step</button>}</li>}
        {model.step && <li className="ct-card ct-step"><h4>What ran</h4>
          <p className="ct-main">Event chain <strong>{model.step.chainLabel}</strong></p>
          <p className="ct-states">{model.step.fromState ? <><span className="ct-state">{model.step.fromState}</span> → </> : null}<span className="ct-state ct-state-to">{model.step.toState}</span><small> · step “{model.step.stepId}”</small></p>
          {model.step.actions.length > 0 && <ul className="ct-actions">{model.step.actions.map(action => <li key={action.id} className={`ct-action-${action.kind}`}>{action.text}</li>)}</ul>}
        </li>}
        {(model.step || model.focus === 'change') && <Effects store={store} effects={model.effects} onSeek={onSeek} />}
        <FollowUps items={model.followUps} onOpen={onOpen} store={store} />
      </ol>
      {model.involved.length > 0 && <p className="ct-involved">Highlighted in the city and the graph: {model.involved.map(key => <EntityChip key={entityId(key)} store={store} entity={key} onSelectEntity={onSelectEntity} />)}</p>}
      <div className="ct-foot">{model.notes.map((note, index) => <p key={index} className="ct-note">{note}</p>)}</div>
      <Details title="Technical details" buttonLabel="Technical details"><pre>{exactValue({ focus: model.focus, cut: model.cut, step: model.step?.technical, predicate: model.predicate?.technical ?? model.trigger?.predicate?.technical })}</pre></Details>
    </>}
  </section>;
}
