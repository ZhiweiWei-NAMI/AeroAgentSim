import type { EntityKey } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { seconds } from '../feeds/format';

/** Uses exact recorded subjects/receipts; never infers mission completion from position. */
export function MissionTimeline({ store, selected }: { store: FeedStore; selected?: EntityKey }) {
  const messages = selected ? store.messages.filter(message => message.subjects?.some(key => entityId(key) === entityId(selected))) : [];
  const receipts = new Map(store.receipts.map(receipt => [receipt.commandId, receipt]));
  return <details className="mission-timeline" open={false}>
    <summary>{selected?.id ?? 'Select a unit'} <span> · {messages.length} recorded commands / events</span></summary>
    <ol>{messages.slice(-12).map(message => <li key={message.id} data-kind={message.kind}>
      <time title={`${message.at.ns} ns`}>{seconds(message.at.ns, store.header.start.ns)}</time>
      <span>{message.schemaId}</span><b>{receipts.get(message.id)?.status ?? message.kind}</b>
    </li>)}</ol>
    {!messages.length && <p>No messages with this entity as a recorded subject at this cut.</p>}
  </details>;
}
