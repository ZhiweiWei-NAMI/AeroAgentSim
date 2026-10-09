import { Descriptions, Empty, Tag, Typography } from 'antd';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { useI18n } from '../i18n/I18nProvider';
import { exactValue, seconds } from '../feeds/format';
import { messagesForEntity } from './message-subjects';

export function EntityInspector({ store, selected }: { store: FeedStore; selected?: EntityKey }) {
  const { t } = useI18n();
  const entity = selected ? store.entities.get(entityId(selected)) : undefined;
  if (!entity) return <Empty description={t('viewerSelectEntity')} />;
  const related = [...store.edges.values()].filter(edge => entityId(edge.source) === entityId(entity.key) || entityId(edge.target) === entityId(entity.key));
  const messages = messagesForEntity(store.messages, entity.key);
  const ids = new Set(messages.map(message => message.id));
  const receipts = store.receipts.filter(receipt => ids.has(receipt.commandId));
  const type = store.header.types.find(type => type.typeId === entity.typeId);
  return <div className="entity-inspector" data-testid="inspector">
    <Typography.Title level={4}>{entity.key.id}</Typography.Title>
    <Tag>{type?.displayName ?? entity.typeId}</Tag><Tag>g{entity.key.generation}</Tag>
    <p className="inspector-note">{t('viewerExact')}</p>
    <Descriptions column={1} size="small" layout="vertical" items={[...entity.fields].map(([fieldId, fact]) => {
      const field = store.header.fields.find(field => field.fieldId === fieldId);
      return { key: fieldId, label: <span>{field?.displayName ?? fieldId} {field?.unit && <Tag>{field.unit}</Tag>}</span>, children: <div className="fact-record">
        <code className="fact-value">{exactValue(fact.value)}</code>
        <div className="fact-meta">{field?.role} · {fact.producer}</div>
        <div className="fact-meta" title={`${fact.validFrom.ns} ns · μ${fact.validFrom.microstep}`}>valid {seconds(fact.validFrom.ns,store.header.start.ns)} → {fact.validTo ? seconds(fact.validTo.ns,store.header.start.ns) : 'open'} · {field?.frame}</div>
        {fact.available && <div className="fact-meta" title={`${fact.available.ns} ns · μ${fact.available.microstep}`}>available {seconds(fact.available.ns,store.header.start.ns)}</div>}
        {fact.acquired && <div className="fact-meta">acquired {fact.acquired.numerator}/{fact.acquired.denominator} · {fact.acquired.clockId}/{fact.acquired.mappingId}</div>}
        {fact.version && <div className="fact-meta">version {fact.version.journalIndex}:{fact.version.itemOrdinal}</div>}
      </div> };
    })} />
    <Typography.Title level={5}>{t('viewerRelations')}</Typography.Title>
    {related.length ? related.map(edge => <div className="journal-item" key={edge.edgeId}><code>{edge.relationId}</code><div>{edge.source.id} → {edge.target.id}</div></div>) : <Typography.Text type="secondary">{t('viewerNone')}</Typography.Text>}
    <Typography.Title level={5}>{t('viewerMessages')}</Typography.Title>
    {messages.slice(-4).reverse().map(message => <div className="journal-item" key={message.id}><Tag>{message.kind}</Tag>{message.schemaId}<div className="fact-meta" title={`${message.at.ns} ns`}>{seconds(message.at.ns,store.header.start.ns)}</div><code>{exactValue(message.payload)}</code></div>)}
    {receipts.slice(-4).reverse().map((receipt, index) => <div className="journal-item" key={`${receipt.commandId}-${index}`}><Tag color="blue">{receipt.status}</Tag><code>{receipt.commandId}</code><div><code>{exactValue(receipt.result)}</code></div></div>)}
    {!messages.length && !receipts.length && <Typography.Text type="secondary">{t('viewerNone')}</Typography.Text>}
  </div>;
}
