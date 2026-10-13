import { useMemo, useRef, useState } from 'react';
import { KeyTrees, keysPresent } from './KeyTrees';
import { canonicalJson, operation, type OperationPreview } from './operations';
import type { SectionProps } from './types';
import type { Project } from '../api';
import { nsToS, scalarNumber, sToNs, type Draft } from './tasks/model';
import { DiffPreview, OpApply, StagedNumber, parseStagedNumber, problemMessage, stagedNumberProblem } from './tasks/op-ui';

/** Duration uses C11. Step and seed are run settings, reviewed and saved together to the server. */
export function RunSection({ api, project, draft, edit, focus, zh, L, onProject, saveDraft }: SectionProps) {
  const run = (draft.run ?? {}) as Draft;
  const duration = nsToS(run.until_ns), step = nsToS(run.advance_ns ?? run.step_ns), seed = scalarNumber(run.seed);
  const [seconds, setSeconds] = useState<string>();
  const durationText = seconds ?? (duration === undefined ? '' : String(duration));
  const durationProblem = stagedNumberProblem(durationText, { exclusiveMin: 0, zh })
    ?? (parseStagedNumber(durationText) !== undefined && (!Number.isSafeInteger(sToNs(parseStagedNumber(durationText)!)) || sToNs(parseStagedNumber(durationText)!) < 1)
      ? L('Duration must be representable as a positive whole number of nanoseconds.', '时长必须可精确表示为正整数纳秒。') : undefined);
  const durationOperations = useMemo(() => !durationProblem ? [operation('set_duration', { seconds: parseStagedNumber(durationText) })] : undefined, [durationProblem, durationText]);
  const [settings, setSettings] = useState<Partial<{ step: string; seed: string }>>({});
  const stepText = settings.step ?? (step === undefined ? '' : String(step));
  const seedText = settings.seed ?? (seed === undefined ? run.seed === undefined ? '' : JSON.stringify(run.seed) : String(seed));
  const stepProblem = stagedNumberProblem(stepText, { exclusiveMin: 0, allowEmpty: step === undefined && settings.step === undefined, zh })
    ?? (parseStagedNumber(stepText) !== undefined && (!Number.isSafeInteger(sToNs(parseStagedNumber(stepText)!)) || sToNs(parseStagedNumber(stepText)!) < 1)
      ? L('Step must be representable as a positive whole number of nanoseconds.', '步长必须可精确表示为正整数纳秒。') : undefined);
  const seedProblem = stagedNumberProblem(seedText, { min: 0, integer: true, allowEmpty: run.seed === undefined && settings.seed === undefined, zh });
  const stepNs = parseStagedNumber(stepText) === undefined ? undefined : sToNs(parseStagedNumber(stepText)!);
  const seedValue = parseStagedNumber(seedText);
  const settingsKey = canonicalJson({ stepNs, seed: seedValue });
  const [review, setReview] = useState<{ base: Project; key: string; scenario: SectionProps['draft']; data: OperationPreview }>();
  const [busy, setBusy] = useState(false), [error, setError] = useState<unknown>();
  const pending = useRef(false);
  const settingsValid = !stepProblem && !seedProblem && (stepNs !== undefined || seedValue !== undefined);
  const fresh = !!review && settingsValid && review.base.id === project.id && review.base.revision === project.revision && review.key === settingsKey;
  const perform = async (body: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(undefined);
    try { await body(); } catch (problem) { setError(problem); }
    finally { pending.current = false; setBusy(false); }
  };
  const previewSettings = () => {
    if (!settingsValid) return;
    const key = settingsKey, reviewedStep = stepNs, reviewedSeed = seedValue;
    void perform(async () => {
      setReview(undefined);
      const saved = await saveDraft();
      if (!saved) throw new Error(L('Fix the highlighted input before previewing.', '请先修正标记的输入后再预览。'));
      const savedRun = (saved.scenario.run ?? {}) as Draft;
      const stepKey = 'advance_ns' in savedRun || !('step_ns' in savedRun) ? 'advance_ns' : 'step_ns';
      const updatedRun = { ...savedRun, ...(reviewedStep !== undefined ? { [stepKey]: reviewedStep } : {}), ...(reviewedSeed !== undefined ? { seed: reviewedSeed } : {}) };
      const fields = [
        ...(reviewedStep !== undefined && nsToS(savedRun[stepKey]) !== reviewedStep / 1e9 ? [{ name: `${L('Step', '步长')} (s)`, before: nsToS(savedRun[stepKey]), after: reviewedStep / 1e9 }] : []),
        ...(reviewedSeed !== undefined && scalarNumber(savedRun.seed) !== reviewedSeed ? [{ name: L('Random seed', '随机种子'), before: scalarNumber(savedRun.seed), after: reviewedSeed }] : []),
      ];
      setReview({ base: saved, key, scenario: { ...saved.scenario, run: updatedRun }, data: { diff: { added: [], changed: fields.length ? [{ kind: 'run', id: 'run', fields }] : [], removed: [] }, notes: [] } });
    });
  };
  const applySettings = () => {
    if (!review || !fresh) return;
    const reviewed = review;
    void perform(async () => {
      setReview(undefined);
      const saved = await api.save(reviewed.base.id, reviewed.scenario, reviewed.base.revision);
      onProject(saved);
      setSettings({});
    });
  };
  return <div className="wb-form-section">
    <p className="hint">{L('Set the run duration, time step and random seed.', '设置运行时长、时间步长与随机种子。')}</p>
    <fieldset className="wb-form-card"><legend className="wb-form-card-title">{L('Run timing', '运行时长')}</legend>
      <div className="wb-form-grid">
        <StagedNumber label={`${L('Duration', '运行时长')} (s)`} value={durationText} exclusiveMin={0} problem={durationProblem} onChange={setSeconds} />
        <StagedNumber label={`${L('Step', '步长')} (s)`} value={stepText} exclusiveMin={0} problem={stepProblem} allowEmpty={step === undefined && settings.step === undefined} onChange={value => setSettings(previous => ({ ...previous, step: value }))} />
        <StagedNumber label={L('Random seed', '随机种子')} value={seedText} min={0} integer problem={seedProblem} allowEmpty={run.seed === undefined && settings.seed === undefined} onChange={value => setSettings(previous => ({ ...previous, seed: value }))} />
      </div>
      <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft} operations={durationOperations}
        label={L('Preview duration', '预览时长')} primary small onApplied={() => setSeconds(undefined)} />
      {!review && <button type="button" className="wb-btn small primary" disabled={busy || !settingsValid} onClick={previewSettings}>{busy ? L('Checking…', '正在预览…') : L('Preview step & seed', '预览步长与种子')}</button>}
      {error !== undefined && <p className="hint wb-field-error" role="alert">{problemMessage(error, zh)}</p>}
      {review && <div className="wb-operation-review" role="group" aria-label={L('Preview of run settings', '运行设置预览')}>
        <DiffPreview preview={review.data} L={L} zh={zh} />
        <div className="wb-form-actions">
          {fresh ? <button type="button" className="wb-btn small primary" disabled={busy} onClick={applySettings}>{L('Confirm', '确认')}</button>
            : <><p className="hint wb-form-prerequisite" role="status">{L('Out of date — preview again.', '已过期——请重新预览。')}</p><button type="button" className="wb-btn small" disabled={busy || !settingsValid} onClick={previewSettings}>{L('Preview again', '重新预览')}</button></>}
          <button type="button" className="wb-btn small ghost" disabled={busy} onClick={() => setReview(undefined)}>{L('Cancel', '取消')}</button>
        </div>
      </div>}
    </fieldset>
    <details><summary>{L('Advanced (raw tree)', '高级（原始树）')}</summary>
      <KeyTrees keys={keysPresent(draft, ['run', 'outputs', 'ingress_streams'])} draft={draft} edit={edit} focus={focus} />
    </details>
  </div>;
}
