import { Leaf, pointer } from '../../../workbench/form';
import type { SectionProps } from '../../../workbench/sections/types';
import type { Draft } from './model';
import { addGust, nsToS, removeGust, vehicleIds, windDirection, windSpeed } from './model';
import { useState } from 'react';

interface Props {
  environmentName: string; environment: Draft; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
}


/** Weather & traffic block: background wind + timed gusts in the environment engine profile shape. */
export function WeatherBlock({ environmentName, environment, draft, edit, focus, L }: Props) {
  const profiles = (environment.config?.profiles ?? {}) as Record<string, Draft>;
  const slots = Object.entries(profiles).filter(([, profile]) => 'lowalt.wind' in (profile ?? {}));
  const [start, setStart] = useState('60'), [end, setEnd] = useState('90'), [speed, setSpeed] = useState('9'), [direction, setDirection] = useState('90');
  const vehicles = vehicleIds(draft);
  const addButton = (entity: string) => {
    const s = Number(start), e = Number(end), v = Number(speed), d = Number(direction);
    if (![s, e, v].every(Number.isFinite) || e <= s) return;
    const next = addGust(profiles[entity]['lowalt.wind'] as Draft, { start_s: s, end_s: e, speed_mps: v, direction_deg: Number.isFinite(d) ? d : 90 });
    edit(pointer('engines', environmentName, 'config', 'profiles', entity, 'lowalt.wind'), next);
  };
  return <div className="wb-weather" data-testid="wb-weather">
    <p className="hint">{L('Wind lives in the environment engine profiles; gusts are exact nanosecond intervals over the base value. Changes affect the next run.', '风保存在 environment 引擎的 profiles 中；阵风是基于基础值的精确纳秒时段。修改只影响下一次运行。')}</p>
    {!slots.length && <div className="wb-empty">{L('No lowalt wind profile in this scenario; wind controls cannot be synthesized. Add a lowalt.wind profile via YAML or the engine tree.', '该场景没有低空风场配置；无法凭空生成风控件。请通过 YAML 或引擎树添加 lowalt.wind profile。')}</div>}
    {slots.map(([entity, profile]) => {
      const wind = profile['lowalt.wind'] as Draft;
      const gusts = Array.isArray(wind.gusts) ? wind.gusts as Draft[] : [];
      const shownSpeed = windSpeed(wind.value), shownDirection = windDirection(wind.value);
      return <div key={entity} className="wb-weather-entity">
        <details open><summary><span className="wb-mono">{entity}</span> · lowalt.wind</summary>
          <div className="wb-wind-row">
            <Leaf path={pointer('engines', environmentName, 'config', 'profiles', entity, 'lowalt.wind', 'mode')} label={L('Mode', '模式')} value={wind.mode} onChange={edit} focus={focus} />
            <NumField label={`${L('Background speed', '背景风速')} (m/s)`} value={shownSpeed} path={pointer('engines', environmentName, 'config', 'profiles', entity, 'lowalt.wind', 'value')} onCommit={value => edit(pointer('engines', environmentName, 'config', 'profiles', entity, 'lowalt.wind', 'value'), [value, 0, 0])} />
            {shownSpeed !== undefined && <p className="hint">{shownDirection !== undefined ? L(`Direction ≈ ${shownDirection}°`, `方向约 ${shownDirection}°`) : L('No direction (zero vector)', '无方向（零向量）')}</p>}
          </div>
          <div><p className="hint">{L('Gusts', '阵风')}</p>
            {!gusts.length && <div className="wb-empty">{L('No gusts.', '没有阵风。')}</div>}
            {gusts.map((gust, index) => <div key={index} className="wb-gust-row" data-path={pointer('engines', environmentName, 'config', 'profiles', entity, 'lowalt.wind', 'gusts', index)}>
              <span className="wb-tag wb-mono">{nsToS(gust.start_ns)}–{nsToS(gust.end_ns)} s</span>
              <span className="wb-tag">{windSpeed(gust.value) ?? '?'} m/s</span>
              {windDirection(gust.value) !== undefined && <span className="wb-tag">{windDirection(gust.value)}°</span>}
              <button className="wb-btn small danger" aria-label={L(`Remove gust ${index + 1}`, `移除阵风 ${index + 1}`)} onClick={() => edit(pointer('engines', environmentName, 'config', 'profiles', entity, 'lowalt.wind'), removeGust(wind, index))}>{L('Remove', '移除')}</button>
            </div>)}
          </div>
          <div className="wb-gust-add">
            <label>{L('Start', '开始')} (s) <input aria-label={L('Gust start seconds', '阵风开始秒')} value={start} onChange={event => setStart(event.target.value)} /></label>
            <label>{L('End', '结束')} (s) <input aria-label={L('Gust end seconds', '阵风结束秒')} value={end} onChange={event => setEnd(event.target.value)} /></label>
            <label>{L('Speed', '风速')} (m/s) <input aria-label={L('Gust speed m/s', '阵风速度 m/s')} value={speed} onChange={event => setSpeed(event.target.value)} /></label>
            <label>{L('Direction', '方向')} (°) <input aria-label={L('Gust direction degrees', '阵风方向度')} value={direction} onChange={event => setDirection(event.target.value)} /></label>
            <button className="wb-btn small" onClick={() => addButton(entity)}>Add gust</button>
            {Number(end) <= Number(start) && <p role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{L('End must be after start.', '结束必须晚于开始。')}</p>}
          </div>
          <datalist id="weather-vehicles">{vehicles.map(id => <option key={id} value={id} />)}</datalist>
        </details>
      </div>;
    })}
  </div>;
}

/** Typed numeric input for a derived (vector) value; commits only valid finite numbers. */
function NumField({ label, value, path, onCommit }: { label: string; value: number | undefined; path: string; onCommit: (value: number) => void }) {
  const [text, setText] = useState(value === undefined ? '' : String(value));
  const [bad, setBad] = useState(false);
  return <div className="wb-field" data-path={path}>
    <label>{label}</label>
    <input aria-label={label} value={text} aria-invalid={bad || undefined} inputMode="decimal" onChange={event => {
      setText(event.target.value);
      const parsed = Number(event.target.value);
      if (event.target.value !== '' && Number.isFinite(parsed)) { setBad(false); onCommit(parsed); }
      else setBad(event.target.value !== '');
    }} />
    {bad && <span role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{'NaN'}</span>}
  </div>;
}
