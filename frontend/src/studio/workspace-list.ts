import type { Workspace } from './api';
import { translate } from '../i18n/translate';

/** One card per saved workspace, ordered by the server's actual last edit. */
export function recentWorkspaces(workspaces: Workspace[]): Workspace[] {
  const unique = new Map<string, Workspace>();
  for (const workspace of workspaces) {
    const existing = unique.get(workspace.id);
    if (!existing || (workspace.updated_at && (!existing.updated_at || Date.parse(workspace.updated_at) > Date.parse(existing.updated_at)))) unique.set(workspace.id, workspace);
  }
  return [...unique.values()].sort((left, right) => {
    if (!left.updated_at) return right.updated_at ? 1 : 0;
    if (!right.updated_at) return -1;
    return Date.parse(right.updated_at) - Date.parse(left.updated_at);
  });
}
export function workspaceUpdated(workspace: Workspace, locale = 'en-US'): string {
  const date = workspace.updated_at ? new Date(workspace.updated_at) : undefined;
  return date && Number.isFinite(date.getTime())
    ? translate(locale, 'Updated {time}', {time: date.toLocaleString(locale, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})})
    : translate(locale, 'Update time unavailable');
}
