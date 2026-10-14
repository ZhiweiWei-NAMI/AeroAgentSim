import { useMemo } from 'react';
import type { SectionProps } from '../types';
import { canonicalJson, operation } from '../operations';
import { useStagedForm } from '../useStagedForm';
import { COMPASS_POINTS, nsToS, pointDeg, pointOfDeg, weatherOf, windFromDeg, windSpeed, type Draft } from './model';
import { OpApply, Prerequisite, StagedFormNote, StagedNumber, parseStagedNumber, stagedNumberProblem, type FormNavigation } from './op-ui';
import { gustProblem, rainPresetOf, visibilityPresetOf, weatherParams } from './ops';

interface Props extends FormNavigation {
  environmentName: string; environment: Draft; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
  api: SectionProps['api']; project: SectionProps['project']; onProject: SectionProps['onProject']; saveDraft: SectionProps['saveDraft']; zh: boolean;
}

const VISUAL_ONLY_NOTE = 'Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don\'t use it.';
const VISUAL_ONLY_NOTE_ZH = '仅为视觉呈现——显示在三维视图和无人机拍摄中；飞行、感知和任务结果不使用它。';
const RAIN_PRESETS = ['none', 'light', 'moderate', 'heavy', 'custom'] as const;
const VISIBILITY_PRESETS = ['clear', 'haze', 'mist', 'fog', 'dense', 'custom'] as const;

/** The weather operations affect all configured wind profiles; the review lists their actual changes. */
export function WeatherBlock({ environmentName: _name, environment, draft, edit: _edit, focus: _focus, L, api, project, onProject, saveDraft, zh = false, goTo }: Props) {
  const slots = useMemo(() => Object.entries((environment.config?.profiles ?? {}) as Record<string, Draft>).flatMap(([entity, profile]) =>
    Object.entries(profile).filter(([key]) => key === 'lowalt.wind' || key === 'oo:digital_twin.wind.horizontalVelocity')
      .map(([key, wind]) => ({ entity, key, wind: wind as Draft }))), [canonicalJson(environment.config?.profiles)]);
  const speeds = slots.map(({ wind }) => windSpeed(Array.isArray(wind.value?.[0]) ? wind.value[0] : wind.value));
  const commonSpeed = speeds.length && speeds.every(speed => speed !== undefined && speed === speeds[0]) ? speeds[0] : undefined;
  const fromDegs = slots.map(({ wind }) => windFromDeg(Array.isArray(wind.value?.[0]) ? wind.value[0] : wind.value));
  const commonFrom = fromDegs.length && fromDegs.every(deg => deg !== undefined && deg === fromDegs[0]) ? fromDegs[0] : undefined;
  const currentWeather = useMemo(() => weatherOf(environment), [canonicalJson(environment.config?.profiles)]);
  const backgroundForm = useStagedForm<Partial<{ speed: string; from: string; degrees: string }>>(project.id, `weather.background:${_name}`, {}, project.revision);
  const background = backgroundForm.values;
  const setBackground = (change: Partial<{ speed: string; from: string; degrees: string }>) => backgroundForm.setValues(previous => ({ ...previous, ...change }));
  const backgroundText = background.speed ?? (commonSpeed !== undefined ? String(commonSpeed) : '');
  const fromPoint = background.from ?? (commonFrom !== undefined ? pointOfDeg(commonFrom) : 'N');
  const fromDegrees = background.degrees ?? (commonFrom !== undefined ? String(commonFrom) : '');
  // from_deg is sent only when the user actually changed the direction (spec: keep the current direction otherwise).
  const fromChanged = background.from !== undefined || background.degrees !== undefined;
  const fromDegreeProblem = stagedNumberProblem(fromDegrees, { min: 0, max: 360, zh });
  const duration_s = nsToS(draft.run?.until_ns), step_s = nsToS(draft.run?.advance_ns ?? draft.run?.step_ns);
  const gustForm = useStagedForm<Partial<{ start: string; end: string; speed: string }>>(project.id, `weather.gust:${_name}`, {}, project.revision);
  const { values: gust, setValues: setGust } = gustForm;
  const start = gust.start ?? (duration_s !== undefined && duration_s > 0 ? String(duration_s * 0.25) : '');
  const end = gust.end ?? (duration_s !== undefined && duration_s > 0 ? String(duration_s * 0.5) : '');
  const speed = gust.speed ?? '9';
  const bgProblem = stagedNumberProblem(backgroundText, { min: 0, zh });
  const fromDegreeProblem = stagedNumberProblem(fromDegrees, { min: 0, max: 360, zh });
  const errors = gustProblem({ start_s: start, end_s: end, speed_mps: speed, duration_s }, zh);  const gustStart = parseStagedNumber(start), gustEnd = parseStagedNumber(end);
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
  const windOperations = useMemo(() => slots.length && !bgProblem && !fromDegreeProblem
    ? [operation('set_wind', { speed_mps: parseStagedNumber(backgroundText), ...(fromChanged ? { from_deg: ((parseStagedNumber(fromDegrees) ?? pointDeg(fromPoint)) % 360 + 360) % 360 } : {}) })] : undefined,
    [slots.length, bgProblem, fromDegreeProblem, backgroundText, fromChanged, fromDegrees, fromPoint]);
  const conditionsForm = useStagedForm<Partial<{ rain: string; visibility: string; light: string; rainCustom: string; visibilityCustom: string }>>(project.id, `weather.conditions:${_name}`, {}, project.revision);
  const conditions = conditionsForm.values;
  const setConditions = (change: Partial<{ rain: string; visibility: string; light: string; rainCustom: string; visibilityCustom: string }>) =>
    conditionsForm.setValues(previous => ({ ...previous, ...change }));
  const stagedWeather = weatherParams({
    rain: conditions.rain ?? rainPresetOf(currentWeather.precipitationMmH),
    visibility: conditions.visibility ?? visibilityPresetOf(currentWeather.visibilityM),
    light: conditions.light ?? currentWeather.light,
    rainCustom: conditions.rainCustom ?? String(currentWeather.precipitationMmH),
    visibilityCustom: conditions.visibilityCustom ?? String(currentWeather.visibilityM ?? ''),
  });
  const conditionsOperations = useMemo(() => slots.length && stagedWeather !== undefined ? [operation('set_weather', stagedWeather)] : undefined,
    [slots.length, canonicalJson(stagedWeather)]);
  const gustOperations = useMemo(() => slots.length && !missingDuration && !errors && !intervalProblem ? [operation('add_gust', {
    start_s: parseStagedNumber(start), end_s: parseStagedNumber(end), speed_mps: parseStagedNumber(speed),
  })] : undefined, [slots.length, missingDuration, !!errors, intervalProblem, start, end, speed]);
  const opProps = { api, project, onProject, saveDraft };
  if (!slots.length) return <div className="wb-empty">{L('No wind profile is configured. Add the standard low-altitude components in Objects.', '尚未配置风场，请在“对象”中添加标准低空组件。')}</div>;
  return <div className="wb-weather wb-form-section" data-testid="wb-weather">
    <p className="hint">{zh ? VISUAL_ONLY_NOTE_ZH : VISUAL_ONLY_NOTE}</p>
    <fieldset className="wb-form-card"><legend className="wb-form-card-title">{L('Background wind', '背景风')}</legend>
      <StagedFormNote form={backgroundForm} L={L} />
      <div className="wb-form-grid">
        <StagedNumber label={`${L('Background speed', '背景风速')} (m/s)`} value={backgroundText} min={0} onChange={speed => setBackground({ speed })} />
        <label className="wb-form-field"><span className="wb-label">{L('From', '风向')}</span>
          <div className="wb-choice-group" role="group" aria-label={L('Wind from direction', '风向（来向）')} data-testid="wb-wind-from">
            {COMPASS_POINTS.map(point => <button key={point} type="button" className="wb-choice" aria-pressed={fromPoint === point}
              onClick={() => setBackground({ from: point, degrees: String(pointDeg(point)) })}>{point}</button>)}
          </div>
        </label>
        <StagedNumber label={`${L('Degrees', '角度')} (°)`} value={fromDegrees} min={0} max={360} problem={fromDegreeProblem}
          showError={fromChanged} onChange={degrees => setBackground({ degrees, from: pointOfDeg(parseStagedNumber(degrees) ?? 0) })} />
        <OpApply {...opProps} operations={windOperations} label={L('Preview', '预览')} primary small onApplied={() => backgroundForm.clear(backgroundForm.values)} />
      </div>
      {commonSpeed === undefined && <p className="hint">{L('Enter a speed to apply to all configured wind profiles.', '请输入要应用到所有风场的风速。')}</p>}
    </fieldset>
    <fieldset className="wb-form-card wb-conditions"><legend className="wb-form-card-title">{L('Conditions', '天气条件')}</legend>
      <StagedFormNote form={conditionsForm} L={L} />
      <div className="wb-form-grid">
        <label className="wb-form-field"><span className="wb-label">{L('Rain', '降雨')}</span>
          <div className="wb-choice-group" role="group" aria-label={L('Rain preset', '降雨预设')} data-testid="wb-rain-presets">
            {RAIN_PRESETS.map(preset => <button key={preset} type="button" className="wb-choice" aria-pressed={(conditions.rain ?? rainPresetOf(currentWeather.precipitationMmH)) === preset}
              onClick={() => setConditions({ rain: preset })}>
              {preset === 'none' ? L('None', '无') : preset === 'light' ? L('Light', '小雨') : preset === 'moderate' ? L('Moderate', '中雨')
                : preset === 'heavy' ? L('Heavy', '大雨') : L('Custom', '自定义')}
            </button>)}
          </div>
        </label>
        {(conditions.rain ?? rainPresetOf(currentWeather.precipitationMmH)) === 'custom' && <StagedNumber label={`${L('Rain rate', '降雨强度')} (mm/h)`} value={conditions.rainCustom ?? String(currentWeather.precipitationMmH)}
          min={0} max={100} onChange={rainCustom => setConditions({ rainCustom })} />}
        <label className="wb-form-field"><span className="wb-label">{L('Visibility', '能见度')}</span>
          <div className="wb-choice-group" role="group" aria-label={L('Visibility preset', '能见度预设')} data-testid="wb-visibility-presets">
            {VISIBILITY_PRESETS.map(preset => <button key={preset} type="button" className="wb-choice" aria-pressed={(conditions.visibility ?? visibilityPresetOf(currentWeather.visibilityM)) === preset}
              onClick={() => setConditions({ visibility: preset })}>
              {preset === 'clear' ? L('Clear', '晴') : preset === 'haze' ? L('Haze 5 km', '霾 5 公里') : preset === 'mist' ? L('Mist 2 km', '轻雾 2 公里')
                : preset === 'fog' ? L('Fog 500 m', '雾 500 米') : preset === 'dense' ? L('Dense fog 150 m', '浓雾 150 米') : L('Custom', '自定义')}
            </button>)}
          </div>
        </label>
        {(conditions.visibility ?? visibilityPresetOf(currentWeather.visibilityM)) === 'custom' && <StagedNumber label={`${L('Visibility', '能见度')} (m)`} value={conditions.visibilityCustom ?? String(currentWeather.visibilityM ?? '')}
          min={50} max={50000} onChange={visibilityCustom => setConditions({ visibilityCustom })} />}
        <label className="wb-form-field"><span className="wb-label">{L('Light', '光照')}</span>
          <div className="wb-choice-group" role="group" aria-label={L('Time-of-day lighting', '时段光照')} data-testid="wb-light-presets">
            {['day', 'dusk'].map(preset => <button key={preset} type="button" className="wb-choice" aria-pressed={(conditions.light ?? currentWeather.light) === preset}
              onClick={() => setConditions({ light: preset })}>{preset === 'day' ? L('Day', '白天') : L('Dusk', '黄昏')}</button>)}
          </div>
        </label>
        <OpApply {...opProps} operations={conditionsOperations} label={L('Preview', '预览')} primary small onApplied={() => conditionsForm.clear(conditionsForm.values)} />
      </div>
    </fieldset>
    <fieldset className="wb-form-card wb-gust-add"><legend className="wb-form-card-title">{L('Add gust', '添加阵风')}</legend>
      <StagedFormNote form={gustForm} L={L} />
      {(missingDuration || shortDuration) && <Prerequisite goTo={goTo} section="run" action={L('Run & recording', '运行与记录')}>
        {missingDuration ? L('Set a run duration in Run & recording.', '请在“运行与记录”中设置运行时长。')
          : L(`Run duration is ${duration_s} s — lengthen it on Run & recording.`, `运行时长为 ${duration_s} 秒——请在“运行与记录”中延长。`)}
      </Prerequisite>}
      <div className="wb-form-grid">
        <StagedNumber label={`${L('Start', '开始')} (s)`} value={start} min={0} problem={errors?.start_s} onChange={text => setGust(previous => ({ ...previous, start: text }))} />
        <StagedNumber label={`${L('End', '结束')} (s)`} value={end} min={0} problem={endProblem} showError={gust.start !== undefined || gust.end !== undefined} onChange={text => setGust(previous => ({ ...previous, end: text }))} />
        <StagedNumber label={`${L('Speed', '风速')} (m/s)`} value={speed} min={0} problem={errors?.speed_mps} onChange={text => setGust(previous => ({ ...previous, speed: text }))} />
        <OpApply {...opProps} operations={gustOperations} label={L('Preview', '预览')} primary small onApplied={() => gustForm.clear(gustForm.values)} />
      </div>
      {intervalProblem && !Object.keys(gust).length && <p className="hint wb-form-prerequisite">{intervalProblem}</p>}
    </fieldset>
    <details open={slots.some(({ wind }) => Array.isArray(wind.gusts) && wind.gusts.length)}><summary>{L('Configured gusts', '已配置阵风')}</summary>
      {slots.map(({ entity, key, wind }) => <div key={`${entity}:${key}`}>
        <span className="wb-mono">{entity}</span>
        {!Array.isArray(wind.gusts) || !wind.gusts.length ? <p className="hint">{L('No gusts.', '没有阵风。')}</p>
          : wind.gusts.map((entry: Draft, index: number) => <div key={index} className="wb-gust-row">
            <span className="wb-tag wb-mono">{nsToS(entry.start_ns) ?? '—'}–{nsToS(entry.end_ns) ?? '—'} s</span>
            <span className="wb-tag">{windSpeed(Array.isArray(entry.value?.[0]) ? entry.value[0] : entry.value) ?? '—'} m/s</span>
            <OpApply {...opProps} operations={nsToS(entry.start_ns) === undefined ? undefined : [operation('remove_gust', { start_s: nsToS(entry.start_ns) })]}
              label={L('Remove', '移除')} danger small
              summary={<p className="hint">{L(`Remove the gust starting at ${nsToS(entry.start_ns)} s from matching wind profiles.`, `从匹配的风场中移除 ${nsToS(entry.start_ns)} 秒开始的阵风。`)}</p>} />
          </div>)}
      </div>)}
    </details>
  </div>;
}
