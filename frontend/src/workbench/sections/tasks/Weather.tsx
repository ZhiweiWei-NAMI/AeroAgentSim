import { useMemo, useState } from 'react';
import type { SectionProps } from '../types';
import { canonicalJson, operation } from '../operations';
import { nsToS, windSpeed, type Draft } from './model';
import { OpApply, Prerequisite, StagedNumber, parseStagedNumber, stagedNumberProblem, type FormNavigation } from './op-ui';
import { gustProblem } from './ops';

interface Props extends FormNavigation {
  environmentName: string; environment: Draft; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
  api: SectionProps['api']; project: SectionProps['project']; onProject: SectionProps['onProject']; saveDraft: SectionProps['saveDraft']; zh: boolean;
}

/** The weather operations affect all configured wind profiles; the review lists their actual changes. */
export function WeatherBlock({ environmentName: _name, environment, draft, edit: _edit, focus: _focus, L, api, project, onProject, saveDraft, zh, onSection }: Props) {
  const slots = useMemo(() => Object.entries((environment.config?.profiles ?? {}) as Record<string, Draft>).flatMap(([entity, profile]) =>
    Object.entries(profile).filter(([key]) => key === 'lowalt.wind' || key === 'oo:digital_twin.wind.horizontalVelocity')
      .map(([key, wind]) => ({ entity, key, wind: wind as Draft }))), [canonicalJson(environment.config?.profiles)]);
  const speeds = slots.map(({ wind }) => windSpeed(Array.isArray(wind.value?.[0]) ? wind.value[0] : wind.value));
  const commonSpeed = speeds.length && speeds.every(speed => speed !== undefined && speed === speeds[0]) ? speeds[0] : undefined;
  const [background, setBackground] = useState<string>();
  const backgroundText = background ?? (commonSpeed !== undefined ? String(commonSpeed) : '');
  const duration_s = nsToS(draft.run?.until_ns), step_s = nsToS(draft.run?.advance_ns ?? draft.run?.step_ns);
  const [gust, setGust] = useState<Partial<{ start: string; end: string; speed: string }>>({});
  const start = gust.start ?? (duration_s !== undefined && duration_s > 0 ? String(duration_s * 0.25) : '');
  const end = gust.end ?? (duration_s !== undefined && duration_s > 0 ? String(duration_s * 0.5) : '');
  const speed = gust.speed ?? '9';
  const bgProblem = stagedNumberProblem(backgroundText, { min: 0, zh });
  const errors = gustProblem({ start_s: start, end_s: end, speed_mps: speed, duration_s }, zh);
  const gustStart = parseStagedNumber(start), gustEnd = parseStagedNumber(end);
  const overlaps = gustStart !== undefined && gustEnd !== undefined && slots.some(({ wind }) => Array.isArray(wind.gusts) && wind.gusts.some((entry: Draft) => {
    const previousStart = nsToS(entry.start_ns), previousEnd = nsToS(entry.end_ns);
    return previousStart !== undefined && previousEnd !== undefined && gustStart < previousEnd && gustEnd > previousStart;
  }));
  const intervalProblem = overlaps ? L('This gust overlaps an existing interval. Choose an unused time interval.', '此阵风与已有时段重叠，请选择未使用的时段。')
    : gustStart !== undefined && gustEnd !== undefined && gustEnd > gustStart && Math.round(gustEnd * 1e9) <= Math.round(gustStart * 1e9)
      ? L('The gust must span at least one nanosecond.', '阵风时段必须至少持续一纳秒。') : undefined;
  const endProblem = errors?.end_s ?? intervalProblem;
  const missingDuration = duration_s === undefined || duration_s <= 0;
  const shortDuration = duration_s !== undefined && step_s !== undefined && duration_s * 0.25 < step_s;
  const windOperations = useMemo(() => slots.length && !bgProblem ? [operation('set_wind', { speed_mps: parseStagedNumber(backgroundText) })] : undefined, [slots.length, bgProblem, backgroundText]);
  const gustOperations = useMemo(() => slots.length && !missingDuration && !errors && !intervalProblem ? [operation('add_gust', {
    start_s: parseStagedNumber(start), end_s: parseStagedNumber(end), speed_mps: parseStagedNumber(speed),
  })] : undefined, [slots.length, missingDuration, !!errors, intervalProblem, start, end, speed]);
  const opProps = { api, project, onProject, saveDraft };
  if (!slots.length) return <div className="wb-empty">{L('No wind profile is configured. Add the standard low-altitude components in Objects.', '尚未配置风场，请在“对象”中添加标准低空组件。')}</div>;
  return <div className="wb-weather wb-form-section" data-testid="wb-weather">
    <p className="hint">{L('Background wind and short gusts. Gust times must fall inside the run duration.', '设置背景风与短时阵风，阵风时段必须处于运行时长之内。')}</p>
    <fieldset className="wb-form-stack"><legend>{L('Background wind', '背景风')}</legend>
      <StagedNumber label={`${L('Background speed', '背景风速')} (m/s)`} value={backgroundText} min={0} onChange={setBackground} />
      {commonSpeed === undefined && <p className="hint">{L('Enter a speed to apply to all configured wind profiles.', '请输入要应用到所有风场的风速。')}</p>}
      <OpApply {...opProps} operations={windOperations} label={L('Preview', '预览')} primary small onApplied={() => setBackground(undefined)} />
    </fieldset>
    <fieldset className="wb-form-stack wb-gust-add"><legend>{L('Add gust', '添加阵风')}</legend>
      {(missingDuration || shortDuration) && <Prerequisite onSection={onSection} section="run" action={L('Run & recording', '运行与记录')}>
        {missingDuration ? L('Set a run duration in Run & recording.', '请在“运行与记录”中设置运行时长。')
          : L(`Run duration is ${duration_s} s — lengthen it on Run & recording.`, `运行时长为 ${duration_s} 秒——请在“运行与记录”中延长。`)}
      </Prerequisite>}
      <StagedNumber label={`${L('Start', '开始')} (s)`} value={start} min={0} problem={errors?.start_s} onChange={text => setGust(previous => ({ ...previous, start: text }))} />
      <StagedNumber label={`${L('End', '结束')} (s)`} value={end} min={0} problem={endProblem} showError={gust.start !== undefined || gust.end !== undefined} onChange={text => setGust(previous => ({ ...previous, end: text }))} />
      <StagedNumber label={`${L('Speed', '风速')} (m/s)`} value={speed} min={0} problem={errors?.speed_mps} onChange={text => setGust(previous => ({ ...previous, speed: text }))} />
      {intervalProblem && !Object.keys(gust).length && <p className="hint wb-form-prerequisite">{intervalProblem}</p>}
      <OpApply {...opProps} operations={gustOperations} label={L('Preview', '预览')} primary small onApplied={() => setGust({})} />
    </fieldset>
    <details><summary>{L('Configured gusts', '已配置阵风')}</summary>
      {slots.map(({ entity, key, wind }) => <div key={`${entity}:${key}`}>
        <span className="wb-mono">{entity}</span>
        {!Array.isArray(wind.gusts) || !wind.gusts.length ? <p className="hint">{L('No gusts.', '没有阵风。')}</p>
          : wind.gusts.map((entry: Draft, index: number) => <div key={index} className="wb-gust-row">
            <span className="wb-tag wb-mono">{nsToS(entry.start_ns) ?? '—'}–{nsToS(entry.end_ns) ?? '—'} s</span>
            <span className="wb-tag">{windSpeed(Array.isArray(entry.value?.[0]) ? entry.value[0] : entry.value) ?? '—'} m/s</span>
          </div>)}
      </div>)}
      <Prerequisite onSection={onSection} section="file" action={L('File (YAML)', '文件（YAML）')}>{L('Edit existing gusts in File (YAML).', '请在“文件”（YAML）中编辑已有阵风。')}</Prerequisite>
    </details>
  </div>;
}
