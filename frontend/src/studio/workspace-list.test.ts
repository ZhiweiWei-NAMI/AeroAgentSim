import { expect, test, vi } from 'vitest';
import { recentWorkspaces, workspaceUpdated } from './workspace-list';
vi.mock('../i18n/messages', async () => ({ default: { 'zh-CN': (await import('../i18n/studio-messages')).default } }));
import type { Workspace } from './api';
test('workspace gallery deduplicates IDs and keeps the most recently edited version first', () => {
 const row = (id:string,time?:string):Workspace => ({id,name:id,scenario:{},updated_at:time});
 const list=recentWorkspaces([row('old','2026-10-01'),row('recent','2026-10-09'),row('old','2026-10-08'),row('legacy')]);
 expect(list.map(item=>item.id)).toEqual(['recent','old','legacy']);
 expect(list[1].updated_at).toBe('2026-10-08');
});

test('workspace timestamps use the selected locale and never display Invalid Date', () => {
 const workspace:Workspace = {id:'draft',name:'Draft',scenario:{},updated_at:'2026-10-09T09:10:00Z'};
 expect(workspaceUpdated(workspace)).toMatch(/^Updated /);
 expect(workspaceUpdated(workspace,'zh-CN')).toMatch(/^更新于 /);
 expect(workspaceUpdated({...workspace,updated_at:undefined},'zh-CN')).toBe('暂无更新时间');
 expect(workspaceUpdated({...workspace,updated_at:'not-a-date'},'zh-CN')).toBe('暂无更新时间');
});
