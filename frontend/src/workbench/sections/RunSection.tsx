import { useState } from 'react';
import { KeyTrees, keysPresent } from './KeyTrees';
import { operation } from './operations';
import type { SectionProps } from './types';
import type { Draft } from './tasks/model';
import { nsToS, sToNs } from './tasks/model';
import { engineOf } from './tasks/model';
import { OpApply, StagedNumber } from './tasks/op-ui';

/** Run & recording: friendly Duration (s) / step (s) / seed fields; the raw tree stays under "Advanced".
 *  Duration goes through C11 set_duration when the low-altitude pack is present (it also resyncs the
 *  ingress watermarks); otherwise it writes run.until_ns directly. */
export function RunSection({ api, project, draft, edit, focus, zh, L, onProject, saveDraft }: SectionProps) {
  const [staged, setStaged] = useState<{ seconds: string; step: string; seed: string } | undefined>();
  const run = (draft.run ?? {}) as Draft;
  const duration_s = nsToS(run.until_ns), step_s = nsToS(run.advance_ns ?? run.step_ns);
  const lowalt = engineOf(draft, 'lowalt_tasks') !== undefined;
  const problems = staged && {
    seconds: staged.seconds.trim() === '' ? undefined : stagedNumberCheck(staged.seconds, zh) ?? (Number(staged.seconds) <= 0 ? zh ? '时长必须大于 0 秒。' : 'Duration must be above 0 s.' : undefined),
    step: staged.step.trim() === '' ? undefined : stagedNumberCheck(staged.step, zh) ?? (Number(staged.step) <= 0 ? zh ? '步长必须大于 0 秒。' : 'Step must be above 0 s.' : undefined),
    seed: staged.seed.trim() === '' || (Number.isInteger(Number(staged.seed)) && Number(staged.seed) >= 0) ? undefined : zh ? '种子必须是不小于 0 的整数。' : 'Seed must be an integer of 0 or more.',
  };
  const anyProblem = staged ? problems && Object.values(problems).find(Boolean) : undefined;
  const dirty = staged && (staged.seconds !== (duration_s !== undefined ? String(duration_s) : '')
    || staged.step !== (step_s !== undefined ? String(step_s) : '') || staged.seed !== (run.seed !== undefined ? String(run.seed) : ''));
  return <>
    <p className="hint">{L('How long the world runs, how big each step is and the random seed. Values are seconds; the raw tree stays under Advanced.', '世界运行多长、每步多大、随机种子是什么。数值以秒计；原始树保留在“高级”中。')}</p>
    <div style={{ display: 'grid', gap: 6, maxWidth: 320 }}>
      <StagedNumber label={`${L('Duration', '运行时长')} (s)`} value={staged ? staged.seconds : duration_s !== undefined ? String(duration_s) : ''} min={0} allowEmpty
        onChange={text => setStaged(previous => ({ seconds: text, step: previous?.step ?? (step_s !== undefined ? String(step_s) : ''), seed: previous?.seed ?? (run.seed !== undefined ? String(run.seed) : '') }))} />
      <StagedNumber label={`${L('Step', '步长')} (s)`} value={staged ? staged.step : step_s !== undefined ? String(step_s) : ''} min={0} allowEmpty
        onChange={text => setStaged(previous => ({ seconds: previous?.seconds ?? (duration_s !== undefined ? String(duration_s) : ''), step: text, seed: previous?.seed ?? (run.seed !== undefined ? String(run.seed) : '') }))} />
      <StagedNumber label={L('Random seed', '随机种子')} value={staged ? staged.seed : run.seed !== undefined ? String(run.seed) : ''} allowEmpty
        onChange={text => setStaged(previous => ({ seconds: previous?.seconds ?? (duration_s !== undefined ? String(duration_s) : ''), step: previous?.step ?? (step_s !== undefined ? String(step_s) : ''), seed: text }))} />
      {anyProblem && <p role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{anyProblem}</p>}
      {lowalt && <p className="hint">{L('Duration applies through set_duration, which also resyncs the operator input watermark.', '时长通过 set_duration 应用，同时会重新同步操作输入水位。')}</p>}
    </div>
    {staged && !anyProblem && <>
      {staged.seconds.trim() !== '' && <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
        operations={lowalt ? [operation('set_duration', { seconds: Number(staged.seconds) })] : [{ id: 'form-run-duration', op: 'set_duration', params: { seconds: Number(staged.seconds) } }]}
        label={L('Set duration', '设置时长')} labelZh="设置时长" primary small onApplied={() => undefined} />}
      <p className="hint" style={{ margin: '4px 0' }}>{L('Step and seed edit the run tree directly (the operations API does not own them):', '步长与种子直接编辑 run 树（操作 API 不管理它们）：')}</p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {staged.step.trim() !== '' && !problems?.step && <button className="wb-btn small" onClick={() => edit('/run/advance_ns', sToNs(Number(staged.step)))}>{L('Apply step to draft', '将步长写入草稿')}</button>}
        {staged.seed.trim() !== '' && !problems?.seed && <button className="wb-btn small" onClick={() => edit('/run/seed', Math.trunc(Number(staged.seed)))}>{L('Apply seed to draft', '将种子写入草稿')}</button>}
        <button className="wb-btn small ghost" onClick={() => setStaged(undefined)}>{L('Discard staged values', '放弃暂存值')}</button>
      </div>
      {dirty === false && <p className="hint">{L('Staged values match the draft.', '暂存值与草稿一致。')}</p>}
    </>}
    <details style={{ marginTop: 12 }}><summary>{L('Advanced (raw tree)', '高级（原始树）')}</summary>
      <KeyTrees keys={keysPresent(draft, ['run', 'outputs', 'ingress_streams'])} draft={draft} edit={edit} focus={focus} />
    </details>
  </>;
}

function stagedNumberCheck(text: string, zh: boolean): string | undefined {
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return zh ? '必须是有限数字。' : 'Must be a finite number.';
  return undefined;
}
