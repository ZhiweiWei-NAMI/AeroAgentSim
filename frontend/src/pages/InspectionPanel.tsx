import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { exactValue } from '../feeds/format';
import { entityId } from '../viewport/bindings';
import { Details } from '../console/Details';
import { PageState } from '../console/PageState';
import { entityLabel, readableLabel, stateValue } from './inspection-format';

export function InspectionPanel({ store, selected, onSeek }: { store: TemporalFeedStore; selected?: EntityKey; onSeek: (cut: number) => void }) {
  const entity = selected && store.entities.get(entityId(selected));
  if (!entity) return <PageState kind="empty" title={selected ? 'Entity absent at this moment' : 'Select an entity'} description="Select a node in the graph or an actor in the city view to explore its recorded state." />;
  const includes = (roles: Record<string, EntityKey>) => Object.values(roles).some(key => entityId(key) === entityId(entity.key));
  const chains = [...store.chainInstances.values()].filter(row => includes(row.roles));
  const predicates = [...store.predicateTruth.values()].filter(row => includes(row.roles));
  const fields = [...entity.fields].filter(([id]) => !/(?:digest|hash)/i.test(id));
  const descriptor = store.header.types.find(row => row.typeId === entity.typeId);
  return <div className="inspection-entity" data-testid="selected-entity-panel">
    <p className="inspection-eyebrow">Selected entity</p>
    <h2>{entityLabel(entity)}</h2><p className="inspection-type">{readableLabel(descriptor?.displayName ?? entity.typeId)}</p>
    <section aria-label="Selected entity state"><h3>State <span>{fields.length}</span></h3>
      {fields.length ? <dl className="inspection-fields">{fields.map(([id, fact]) => {
        const field = store.header.fields.find(row => row.fieldId === id), value = stateValue(fact.value);
        return <div key={id}><dt>{readableLabel(field?.displayName ?? id)}{field?.unit && <small>{field.unit}</small>}</dt><dd>{value === undefined ? <Details title={readableLabel(field?.displayName ?? id)} buttonLabel="View value"><pre>{exactValue(fact.value)}</pre></Details> : <span>{value}</span>}</dd></div>;
      })}</dl> : <p className="inspection-empty">No fields recorded at this moment.</p>}
    </section>
    <section aria-label="Selected entity chains"><h3>Event chains <span>{chains.length}</span></h3>
      {!store.hasChainRecords ? <p className="inspection-empty">This journal has no chain records.</p> : !chains.length ? <p className="inspection-empty">No chains involve this entity at this moment.</p> : chains.map(row => <article className="inspection-chain" key={row.instanceId}><strong>{readableLabel(row.templateId)}</strong><span className={`inspection-badge state-${row.lifecycle}`}>{readableLabel(row.state)}</span><small>{readableLabel(row.lifecycle)}</small><Details title="Chain record" buttonLabel="Chain details"><pre>{exactValue(row)}</pre>{store.chainRecords(row.instanceId).filter(item => item.value.op !== 'close' && item.commit.commitIndex <= (store.viewCursor?.knownAt ?? -1)).map((item, ordinal) => <p key={`${item.commit.commitIndex}/${ordinal}`}><button className="console-btn" onClick={() => onSeek(item.commit.commitIndex)}>Seek {readableLabel(item.value.state)}</button></p>)}</Details></article>)}
    </section>
    <section aria-label="Selected entity predicates"><h3>Predicates <span>{predicates.length}</span></h3>
      {!store.hasPredicateRecords ? <p className="inspection-empty">This journal has no predicate evaluations.</p> : !predicates.length ? <p className="inspection-empty">No evaluations involve this entity at this moment.</p> : predicates.map(row => <article className="inspection-predicate" key={row.contextId}><strong>{readableLabel(row.predicateId)}</strong><span className={`inspection-badge truth-${row.value === null ? 'unknown' : row.value}`}>{row.status === 'known' ? (row.value ? 'True' : 'False') : row.status === 'required_input' ? 'Missing input' : 'Invalid input'}</span><Details title="Predicate evaluation" buttonLabel="Evaluation details"><pre>{exactValue(row)}</pre><button className="console-btn" onClick={() => onSeek(row.readCut.index)}>Seek evaluation cut</button></Details></article>)}
    </section>
    <Details title="Entity identity and recorded facts" buttonLabel="Technical details"><pre>{exactValue({key:entity.key,type:entity.typeId,fields:[...entity.fields.values()],cursor:store.viewCursor})}</pre></Details>
  </div>;
}
