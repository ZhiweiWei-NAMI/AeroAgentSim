import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { messagesForEntity } from './message-subjects';
import { seconds } from '../feeds/format';
import { useI18n } from '../i18n/I18nProvider';

/** Uses exact recorded subjects/receipts; never infers mission completion from position. */
export function MissionTimeline({ store, selected }: { store: FeedStore; selected?: EntityKey }) {
  const { t } = useI18n();
  const messages = messagesForEntity(store.messages, selected);
  const receipts = new Map(store.receipts.map(receipt => [receipt.commandId, receipt]));
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
  return <details className="mission-timeline">
    <summary>{selected?.id ?? t('Select a unit')} <span> · {t('{count} recorded commands / events', { count: messages.length })}</span></summary>
    <ol>{messages.slice(-12).map(message => {
      const status = receipts.get(message.id)?.status;
      return <li key={message.id} data-kind={message.kind}>
        <time title={t('{ns} ns', { ns: message.at.ns })}>{seconds(message.at.ns, store.header.start.ns)}</time>
        <span>{message.schemaId}</span><b>{status ? receiptStatus(status) : message.kind === 'command' ? t('Command') : t('Event')}</b>
      </li>;
    })}</ol>
    {!messages.length && <p>{t('No messages with this entity as a recorded subject at this cut.')}</p>}
  </details>;
}
