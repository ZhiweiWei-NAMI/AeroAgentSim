import { useMemo } from 'react';
import type { SectionProps } from '../types';
import { canonicalJson, operation } from '../operations';
import { useStagedForm } from '../useStagedForm';
import { COMPASS_POINTS, nsToS, pointDeg, pointOfDeg, weatherOf, windFromDeg, windSpeed, type Draft } from './model';
import { formatMmH, formatVisibility } from '../../../viewport/conditions';
import { OpApply, Prerequisite, StagedFormNote, StagedNumber, parseStagedNumber, stagedNumberProblem, type FormNavigation } from './op-ui';
import { gustProblem, rainPresetOf, visibilityPresetOf, weatherParams } from './ops';

interface Props extends FormNavigation {
  environmentName: string; environment: Draft; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
  api: SectionProps['api']; project: SectionProps['project']; onProject: SectionProps['onProject']; saveDraft: SectionProps['saveDraft']; zh: boolean;
}

const RAIN_PRESETS = ['none', 'light', 'moderate', 'heavy', 'custom'] as const;
const VISIBILITY_PRESETS = ['clear', 'haze', 'mist', 'fog', 'dense', 'custom'] as const;
/** 3×3 rose layout: NW N NE / W • E / SW S SE. */
const ROSE_ROWS: Array<Array<string | null>> = [['NW', 'N', 'NE'], ['W', null, 'E'], ['SW', 'S', 'SE']];
const COMPASS_ARIA: Record<string, [string, string]> = {
  N: ['From north', '北方向来风'], NE: ['From north-east', '东北方向来风'], E: ['From east', '东方向来风'], SE: ['From south-east', '东南方向来风'],
  S: ['From south', '南方向来风'], SW: ['From south-west', '西南方向来风'], W: ['From west', '西方向来风'], NW: ['From north-west', '西北方向来风'],
};
/** The compass buttons keep the letters in en and the Chinese names in zh (copy decisions). */
const COMPASS_ZH: Record<string, string> = { N: '北', NE: '东北', E: '东', SE: '东南', S: '南', SW: '西南', W: '西', NW: '西北' };

const rainLabel = (preset: string, mmH: number, L: Props['L']) => preset === 'none' ? L('None', '无') : preset === 'light' ? `Light rain · ${formatMmH(mmH)} mm/h`
  : preset === 'moderate' ? `Moderate rain · ${formatMmH(mmH)} mm/h` : preset === 'heavy' ? `Heavy rain · ${formatMmH(mmH)} mm/h` : L('Custom', '自定义');
const rainLabelZh = (preset: string, mmH: number) => preset === 'none' ? '无' : preset === 'light' ? `小雨 · ${formatMmH(mmH)} mm/h`
  : preset === 'moderate' ? `中雨 · ${formatMmH(mmH)} mm/h` : preset === 'heavy' ? `大雨 · ${formatMmH(mmH)} mm/h` : '自定义';
const visibilityLabel = (preset: string, m: number | null, L: Props['L']) => preset === 'clear' ? L('Clear', '无雾')
  : preset === 'haze' ? L(`Haze · ${formatVisibility(5000)}`, `霾 · ${formatVisibility(5000)}`) : preset === 'mist' ? L(`Mist · ${formatVisibility(2000)}`, `轻雾 · ${formatVisibility(2000)}`)
    : preset === 'fog' ? L(`Fog · ${formatVisibility(500)}`, `雾 · ${formatVisibility(500)}`) : preset === 'dense' ? L(`Dense fog · ${formatVisibility(150)}`, `浓雾 · ${formatVisibility(150)}`) : L('Custom', '自定义');

/** One-line summary of what the staged conditions will show in the 3D view. */
function stagedSummary(rain: string, mmH: number, visibility: string, m: number | null, light: string, zh: boolean): string {
  const parts: string[] = [];
  if (rain === 'none') parts.push(zh ? '无雨' : 'no rain');
  else parts.push(zh ? `${rain === 'light' ? '小雨' : rain === 'moderate' ? '中雨' : '大雨'} ${formatMmH(mmH)} mm/h` : `${rain} rain ${formatMmH(mmH)} mm/h`);
  if (visibility === 'clear') parts.push(zh ? '无雾' : 'a clear sky');
  else parts.push(zh ? `${formatVisibility(m ?? 0)}能见度` : `fog limiting view to ${formatVisibility(m ?? 0)}`);
  parts.push(light === 'dusk' ? zh ? '黄昏光照' : 'dusk light' : zh ? '白天光照' : 'day light');
  return zh ? `将显示：${parts.join('、')}。` : `Will show: ${parts.join(', ')}.`;
}

/** Compact 3×3 compass rose with a downwind arrow in the centre cell. */
function CompassRose({ value, onSelect, zh }: { value: string; onSelect: (point: string) => void; zh: boolean }) {
  return <div className="wb-compass-rose" role="group" aria-label={zh ? '风向（来向）' : 'Wind from direction'} data-testid="wb-wind-from">
    {ROSE_ROWS.flatMap((row, rowIndex) => row.map((point, columnIndex) => point === null
      ? <span key={`${rowIndex}:${columnIndex}`} className="wb-compass-centre" aria-hidden="true">
          <svg width={12} height={12} viewBox="0 0 12 12"><path d="M6 1 L9 9 L6 7 L3 9 Z" transform={`rotate(${(pointDeg(value) + 180) % 360} 6 6)`} className="wb-compass-arrow" /></svg>
        </span>
      : <button key={point} type="button" className={`wb-choice wb-compass-point${value === point ? ' active' : ''}`} aria-pressed={value === point}
          aria-label={COMPASS_ARIA[point][zh ? 1 : 0]}
          onClick={() => onSelect(point)}>{zh ? COMPASS_ZH[point] : point}</button>))}
  </div>;
}

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
  const windOperations = useMemo(() => slots.length && !bgProblem && (!fromChanged || !fromDegreeProblem)
    ? [operation('set_wind', { speed_mps: parseStagedNumber(backgroundText), ...(fromChanged ? { from_deg: ((parseStagedNumber(fromDegrees) ?? pointDeg(fromPoint)) % 360 + 360) % 360 } : {}) })] : undefined,
    [slots.length, bgProblem, fromDegreeProblem, backgroundText, fromChanged, fromDegrees, fromPoint]);
  const conditionsForm = useStagedForm<Partial<{ rain: string; visibility: string; light: string; rainCustom: string; visibilityCustom: string }>>(project.id, `weather.conditions:${_name}`, {}, project.revision);
  const conditions = conditionsForm.values;
  const setConditions = (change: Partial<{ rain: string; visibility: string; light: string; rainCustom: string; visibilityCustom: string }>) =>
    conditionsForm.setValues(previous => ({ ...previous, ...change }));
  const rainPreset = conditions.rain ?? rainPresetOf(currentWeather.precipitationMmH);
  const visibilityPreset = conditions.visibility ?? visibilityPresetOf(currentWeather.visibilityM);
  // Each chip shows its own value: Light · 1 mm/h, Fog · 500 m, …
  const rainValueOf = (preset: string) => preset === 'none' ? 0 : preset === 'light' ? 1 : preset === 'moderate' ? 4 : preset === 'heavy' ? 10
    : parseStagedNumber(conditions.rainCustom ?? String(currentWeather.precipitationMmH)) ?? currentWeather.precipitationMmH;
  const rainValue = rainValueOf(rainPreset);
  // The summary is a staged preview: it must read the staged preset values, never the committed ones.
  const visibilityValueOf = (preset: string) => preset === 'clear' ? null : preset === 'haze' ? 5000 : preset === 'mist' ? 2000 : preset === 'fog' ? 500 : preset === 'dense' ? 150
    : parseStagedNumber(conditions.visibilityCustom ?? String(currentWeather.visibilityM ?? '')) ?? currentWeather.visibilityM;
  const stagedLight = conditions.light ?? currentWeather.light;
  const stagedWeather = weatherParams({
    rain: rainPreset,
    visibility: visibilityPreset,
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
    <fieldset className="wb-form-card wb-form-card-wind"><legend className="wb-form-card-title">{L('Background wind', '背景风')}</legend>
      <StagedFormNote form={backgroundForm} L={L} />
      <div className="wb-form-grid">
        <StagedNumber label={`${L('Background speed', '背景风速')} (m/s)`} value={backgroundText} min={0} onChange={speed => setBackground({ speed })} />
        <div className="wb-form-field"><span className="wb-label">{L('From', '风向')}</span>
          <CompassRose value={fromPoint} zh={zh} onSelect={point => setBackground({ from: point, degrees: String(pointDeg(point)) })} />
        </div>
        <StagedNumber label={`${L('From', '风向')} (°)`} value={fromDegrees} min={0} max={360} problem={fromDegreeProblem}
          showError={fromChanged} onChange={degrees => setBackground({ degrees, from: pointOfDeg(parseStagedNumber(degrees) ?? 0) })} />
        <div className="wb-form-actions wb-card-preview"><OpApply {...opProps} operations={windOperations} label={L('Preview', '预览')} primary small onApplied={() => backgroundForm.clear(backgroundForm.values)} /></div>
      </div>
      {commonSpeed === undefined && <p className="hint">{L('Enter a speed to apply to all configured wind profiles.', '请输入要应用到所有风场的风速。')}</p>}
      {/* The wind card's own honesty line: wind IS a simulation input (copy decisions). */}
      <p className="hint" role="note">{zh ? '风与阵风是仿真输入，会影响飞行等待与能耗。' : 'Wind and gusts are simulation inputs: they affect flight holds and energy.'}</p>
    </fieldset>
    <fieldset className="wb-form-card wb-conditions"><legend className="wb-form-card-title">{L('Conditions', '天气条件')}</legend>
      <p className="hint wb-conditions-summary" data-testid="wb-conditions-summary">{stagedSummary(rainPreset, rainValue, visibilityPreset, visibilityValueOf(visibilityPreset), stagedLight, zh)}</p>
      {/* Scoped honesty note (copy decisions): sits under the Conditions fieldset summary,
          NOT under the card title or the wind controls. */}
      <p className="hint" role="note">{zh
        ? '降雨、能见度与光照：仅为视觉呈现——显示在三维视图和无人机拍摄中；飞行、感知和任务结果不使用它。'
        : 'Rain, visibility and light: visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don\'t use it.'}</p>
      <StagedFormNote form={conditionsForm} L={L} />
      {/* Three equal columns: Rain | Visibility | Light, each a vertical chip group whose
          Custom input appears inline under the column only when Custom is selected. */}
      <div className="wb-conditions-grid">
        <div className="wb-conditions-col">
          <span className="wb-label">{L('Rain', '降雨')}</span>
          <div className="wb-choice-group wb-choice-col" role="group" aria-label={L('Rain preset', '降雨预设')} data-testid="wb-rain-presets">
            {RAIN_PRESETS.map(preset => <button key={preset} type="button" className="wb-choice" aria-pressed={rainPreset === preset}
              onClick={() => setConditions({ rain: preset })}>
              {preset === 'custom' ? L('Custom', '自定义') : zh ? rainLabelZh(preset, rainValueOf(preset)) : rainLabel(preset, rainValueOf(preset), L)}
            </button>)}
          </div>
          {rainPreset === 'custom' && <StagedNumber label={`${L('Rain rate', '降雨强度')} (mm/h)`} value={conditions.rainCustom ?? String(currentWeather.precipitationMmH)}
            min={0} max={100} onChange={rainCustom => setConditions({ rainCustom })} />}
        </div>
        <div className="wb-conditions-col">
          <span className="wb-label">{L('Visibility', '能见度')}</span>
          <div className="wb-choice-group wb-choice-col" role="group" aria-label={L('Visibility preset', '能见度预设')} data-testid="wb-visibility-presets">
            {VISIBILITY_PRESETS.map(preset => <button key={preset} type="button" className="wb-choice" aria-pressed={visibilityPreset === preset}
              onClick={() => setConditions({ visibility: preset })}>
              {preset === 'custom' ? L('Custom', '自定义') : visibilityLabel(preset, currentWeather.visibilityM, L)}
            </button>)}
          </div>
          {visibilityPreset === 'custom' && <StagedNumber label={`${L('Visibility', '能见度')} (m)`} value={conditions.visibilityCustom ?? String(currentWeather.visibilityM ?? '')}
            min={50} max={50000} onChange={visibilityCustom => setConditions({ visibilityCustom })} />}
        </div>
        <div className="wb-conditions-col">
          <span className="wb-label">{L('Light', '光照')}</span>
          <div className="wb-choice-group wb-choice-col" role="group" aria-label={L('Time-of-day lighting', '时段光照')} data-testid="wb-light-presets">
            {['day', 'dusk'].map(preset => <button key={preset} type="button" className="wb-choice" aria-pressed={(conditions.light ?? currentWeather.light) === preset}
              onClick={() => setConditions({ light: preset })}>{preset === 'day' ? L('Day', '白天') : L('Dusk', '黄昏')}</button>)}
          </div>
        </div>
      </div>
      <div className="wb-form-actions wb-card-preview"><OpApply {...opProps} operations={conditionsOperations} label={L('Preview', '预览')} primary small onApplied={() => conditionsForm.clear(conditionsForm.values)} /></div>
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
        <div className="wb-form-actions wb-card-preview"><OpApply {...opProps} operations={gustOperations} label={L('Preview', '预览')} primary small onApplied={() => gustForm.clear(gustForm.values)} /></div>
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
