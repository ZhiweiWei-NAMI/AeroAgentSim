import type { EntityKey, FeedCommit } from '../contracts/viewer-feed';
import { entityId } from './bindings';

/** Subjects are supplied by the projector; payload strings are never searched. */
export function messagesForEntity(messages: FeedCommit['messages'], selected?: EntityKey) {
  if (!selected) return [];
  const id = entityId(selected);
  return messages.filter(message => message.subjects?.some(subject => entityId(subject) === id));
}
