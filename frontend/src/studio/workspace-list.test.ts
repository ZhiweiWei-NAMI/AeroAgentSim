import { expect, test } from 'vitest';
import { recentWorkspaces } from './workspace-list';
import type { Workspace } from './api';
test('workspace gallery deduplicates IDs and keeps the most recently edited version first', () => {
 const row = (id:string,time?:string):Workspace => ({id,name:id,scenario:{},updated_at:time});
 const list=recentWorkspaces([row('old','2026-10-01'),row('recent','2026-10-09'),row('old','2026-10-08'),row('legacy')]);
 expect(list.map(item=>item.id)).toEqual(['recent','old','legacy']);
 expect(list[1].updated_at).toBe('2026-10-08');
});
