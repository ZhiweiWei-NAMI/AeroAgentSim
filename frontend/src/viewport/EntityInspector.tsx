import { Descriptions, Empty, Tag, Typography } from 'antd';
import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { useI18n } from '../i18n/I18nProvider';
import { exactValue, seconds } from '../feeds/format';
import { messagesForEntity } from './message-subjects';
import { displayLabel } from '../pages/inspection-format';

export function EntityInspector({ store, selected }: { store: FeedStore; selected?: EntityKey }) {
  const { locale, t } = useI18n();
  const entity = selected ? store.entities.get(entityId(selected)) : undefined;
  if (!entity) return <Empty description={t('Select an entity')} />;
  const related = [...store.edges.values()].filter(edge => entityId(edge.source) === entityId(entity.key) || entityId(edge.target) === entityId(entity.key));
  const messages = messagesForEntity(store.messages, entity.key);
  const ids = new Set(messages.map(message => message.id));
  const receipts = store.receipts.filter(receipt => ids.has(receipt.commandId));
  const type = store.header.types.find(type => type.typeId === entity.typeId);
  const receiptStatus = (status: string) => {
    switch (status) {
      case 'accepted': return t('Accepted');
      case 'rejected': return t('Rejected');
      case 'succeeded': return t('Succeeded');
      case 'failed': return t('Failed');
      case 'canceled': case 'cancelled': return t('Canceled');
      case 'pending': return t('Pending');
      case 'queued': return t('Queued');
      case 'running': return t('Running');
      case 'completed': return t('Completed');
      default: return status;
    }
  };
  return <div className="entity-inspector" data-testid="inspector">
    <Typography.Title level={4}>{entity.key.id}</Typography.Title>
    <Tag title={entity.typeId}>{type?.displayName ? displayLabel(type.displayName, entity.typeId, locale) : entity.typeId}</Tag><Tag>g{entity.key.generation}</Tag>
    <p className="inspector-note">{t('Exact committed values. Display interpolation never changes these facts.')}</p>
    <Descriptions column={1} size="small" layout="vertical" items={[...entity.fields].map(([fieldId, fact]) => {
      const field = store.header.fields.find(field => field.fieldId === fieldId);
      return { key: fieldId, label: <span title={fieldId}>{field?.displayName ? displayLabel(field.displayName, fieldId, locale) : fieldId} {field?.unit && <Tag>{field.unit}</Tag>}</span>, children: <div className="fact-record">
        <code className="fact-value">{exactValue(fact.value)}</code>
        <div className="fact-meta">{field?.role && <>{field.role} · </>}{fact.producer}</div>
        <div className="fact-meta" title={t('{ns} ns · μ{microstep}', { ns: fact.validFrom.ns, microstep: fact.validFrom.microstep })}>{t('Valid {from} → {to}', { from: seconds(fact.validFrom.ns, store.header.start.ns), to: fact.validTo ? seconds(fact.validTo.ns, store.header.start.ns) : t('Open-ended') })}{field?.frame && <> · {field.frame}</>}</div>
        {fact.available && <div className="fact-meta" title={t('{ns} ns · μ{microstep}', { ns: fact.available.ns, microstep: fact.available.microstep })}>{t('Available {time}', { time: seconds(fact.available.ns, store.header.start.ns) })}</div>}
        {fact.acquired && <div className="fact-meta">{t('Acquired {numerator}/{denominator} · {clock}/{mapping}', { numerator: fact.acquired.numerator, denominator: fact.acquired.denominator, clock: fact.acquired.clockId, mapping: fact.acquired.mappingId })}</div>}
        {fact.version && <div className="fact-meta">{t('Version {journal}:{ordinal}', { journal: fact.version.journalIndex, ordinal: fact.version.itemOrdinal })}</div>}
      </div> };
    })} />
    <Typography.Title level={5}>{t('Relations')}</Typography.Title>
    {related.length ? related.map(edge => <div className="journal-item" key={edge.edgeId}><code>{edge.relationId}</code><div>{edge.source.id} → {edge.target.id}</div></div>) : <Typography.Text type="secondary">{t('No recorded items')}</Typography.Text>}
    <Typography.Title level={5}>{t('Recent messages / receipts')}</Typography.Title>
    {messages.slice(-4).reverse().map(message => <div className="journal-item" key={message.id}><Tag>{message.kind === 'command' ? t('Command') : t('Event')}</Tag>{message.schemaId}<div className="fact-meta" title={t('{ns} ns', { ns: message.at.ns })}>{seconds(message.at.ns, store.header.start.ns)}</div><code>{exactValue(message.payload)}</code></div>)}
    {receipts.slice(-4).reverse().map((receipt, index) => <div className="journal-item" key={`${receipt.commandId}-${index}`}><Tag color="blue">{receiptStatus(receipt.status)}</Tag><code>{receipt.commandId}</code><div><code>{receipt.result === undefined ? t('Not recorded') : exactValue(receipt.result)}</code></div></div>)}
    {!messages.length && !receipts.length && <Typography.Text type="secondary">{t('No recorded items')}</Typography.Text>}
  </div>;
}
