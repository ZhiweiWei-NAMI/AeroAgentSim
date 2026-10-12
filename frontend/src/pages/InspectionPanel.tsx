import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { displayName } from '../viewport/semantic';
import { entityId } from '../viewport/bindings';
import { Details } from '../console/Details';
import { PageState } from '../console/PageState';
import { readableLabel, stateValue, displayLabel, displayUnit } from './inspection-format';
import { ConceptHelp } from '../console/ConceptHelp';
import { MomentsList, ChangeList } from '../trace/EntityStory';
import type { CauseRef } from '../trace/cause-trace';
import { useI18n } from '../i18n/I18nProvider';

export function InspectionPanel({ store, selected, onSeek, onOpenTrace }: { store: TemporalFeedStore; selected?: EntityKey; onSeek: (cut: number) => void; onOpenTrace?: (ref: CauseRef, cut?: number) => void }) {
  const { t, locale } = useI18n();
  const entity = selected && store.entities.get(entityId(selected));
  if (!entity) return <PageState kind="empty" title={selected ? t('Entity absent at this moment') : t('Select an entity')} description={t('Select a node in the graph or an actor in the city view to explore its recorded state.')} />;
  const includes = (roles: Record<string, EntityKey>) => Object.values(roles).some(key => entityId(key) === entityId(entity.key));
  const chains = [...store.chainInstances.values()].filter(row => includes(row.roles));
  const predicates = [...store.predicateTruth.values()].filter(row => includes(row.roles));
  const nameFields = ['traffic.actor.label', 'oo:name', 'lowalt.label', 'name', 'label', 'display_name'];
  // The authored display name is the identity shown by every view. Raw aliases remain in Technical details.
  const identity = nameFields.find(id => entity.fields.has(id));
  const fields = [...entity.fields].filter(([id]) => !/(?:digest|hash)/i.test(id) && (!nameFields.some(name => id === name || id === `${name}_zh`) || id === identity));
  return <div className="inspection-entity" data-testid="selected-entity-panel">
    <section aria-label={t('Selected entity state')}><h3>{t('State')}<ConceptHelp topic={t('Entity state')} description={t('State fields show values recorded at the shared timeline cursor, with their declared units.')} guide="observations.md"/><span>{fields.length}</span></h3>
      {fields.length ? <dl className="inspection-fields">{fields.map(([id, fact]) => {
        const field = store.header.fields.find(row => row.fieldId === id), unit = displayUnit(field);
        const value = id === identity ? displayName(entity, store.header, locale) : Array.isArray(fact.value) && fact.value.length === 0 ? (locale === 'zh-CN' ? '无' : 'None')
          : fact.value === '' ? (locale === 'zh-CN' ? '空文本' : 'Empty text') : stateValue(fact.value, locale);
        return <div key={id}><dt>{displayLabel(field?.displayName,id,locale)}{unit && <small>{unit}</small>}</dt><dd>{value === undefined ? <Details title={displayLabel(field?.displayName,id,locale)} buttonLabel={t('View value')}><pre>{exactValue(fact.value)}</pre></Details> : <span>{value}</span>}</dd></div>;
      })}</dl> : <p className="inspection-empty">{t('No fields recorded at this moment.')}</p>}
    </section>
    {onOpenTrace && <section aria-label={t('Why did things happen to this entity')}><h3>{t('Why did it happen?')}<ConceptHelp topic={t('Cause and effect')} description={t('Pick a moment to see what set it off, which event-chain step ran, which shared-state fields it changed and what it caused next.')} guide="behaviours.md"/></h3>
      <MomentsList store={store} entity={entity.key} onOpen={onOpenTrace} /></section>}
    {onOpenTrace && <section aria-label={t('What changed for this entity')}><h3>{t('What changed')}<ConceptHelp topic={t('What changed')} description={t("Every recorded change to this entity's state, with who wrote it. Click one to jump to that moment and see its cause.")} guide="observations.md"/></h3>
      <ChangeList store={store} entity={entity.key} onOpen={onOpenTrace} /></section>}
    <section aria-label={t('Selected entity chains')}><h3>{t('Event chains')}<ConceptHelp topic={t('Event chains')} description={t('Chain instances record which workflow state is active for this entity at the selected moment.')} guide="behaviours.md"/><span>{chains.length}</span></h3>
      {!store.hasChainRecords ? <p className="inspection-empty">{t('This journal has no chain records.')}</p> : !chains.length ? <p className="inspection-empty">{t('No chains involve this entity at this moment.')}</p> : chains.map(row => <article className="inspection-chain" key={row.instanceId}><strong>{readableLabel(row.templateId,locale)}</strong><span className={`inspection-badge state-${row.lifecycle}`}>{readableLabel(row.state,locale)}</span><small>{readableLabel(row.lifecycle,locale)}</small><Details title={t('Chain record')} buttonLabel={t('Chain details')}><pre>{exactValue(row)}</pre>{store.chainRecords(row.instanceId).filter(item => item.value.op !== 'close' && item.commit.commitIndex <= (store.viewCursor?.knownAt ?? -1)).map((item, ordinal) => <p key={`${item.commit.commitIndex}/${ordinal}`}><button className="console-btn" onClick={() => onSeek(item.commit.commitIndex)}>{t('Seek {state}', {state:readableLabel(item.value.state,locale)})}</button></p>)}</Details></article>)}
    </section>
    <section aria-label={t('Selected entity predicates')}><h3>{t('Conditions (predicates)')}<ConceptHelp topic={t('Predicate truth')} description={t('Predicates show the recorded condition and whether its inputs were available at this moment.')} guide="predicates.md"/><span>{predicates.length}</span></h3>
      {!store.hasPredicateRecords ? <p className="inspection-empty">{t('This journal has no predicate evaluations.')}</p> : !predicates.length ? <p className="inspection-empty">{t('No evaluations involve this entity at this moment.')}</p> : predicates.map(row => <article className="inspection-predicate" key={row.contextId}><strong>{readableLabel(row.predicateId,locale)}</strong><span className={`inspection-badge truth-${row.value === null ? 'unknown' : row.value}`}>{row.status === 'known' ? (row.value ? t('True') : t('False')) : row.status === 'required_input' ? t('Missing input') : t('Invalid input')}</span><Details title={t('Predicate evaluation')} buttonLabel={t('Evaluation details')}><pre>{exactValue(row)}</pre><button className="console-btn" onClick={() => onSeek(row.readCut.index)}>{t('Seek evaluation cut')}</button></Details></article>)}
    </section>
    <Details title={t('Entity identity and recorded facts')} buttonLabel={t('Technical details')}><pre>{exactValue({key:entity.key,type:entity.typeId,fields:[...entity.fields.values()],cursor:store.viewCursor})}</pre></Details>
  </div>;
}
