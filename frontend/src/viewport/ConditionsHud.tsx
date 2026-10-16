import { useState } from 'react';
import { compassPoint, compassPointZh, formatMmH, formatVisibility, type Conditions } from './conditions';

/** mm:ss clock text shared with the workbench forms (kept here to avoid importing workbench code into the viewport). */
const clockS = (seconds: number): string => {
  const total = Math.max(0, Math.round(seconds)), m = Math.floor(total / 60), s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};
const INTENSITY = [0, 2, 5, 8, 12] as const;
// Honesty: wind IS simulated (flight holds, energy); the rest is visual only (spec copy decisions).
const NOTE_EN = 'Wind is simulated. Smoke, flames, rain, fog and light are visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don\'t use them.';
const NOTE_ZH = '风为仿真输入。烟雾、火焰、降雨、雾与光照仅为视觉呈现——显示在三维视图和无人机拍摄中；飞行、感知和任务结果不使用它们。';

/** Wind compass dial (36 px, needle points downwind) with a 180 ms ease-out, disabled under prefers-reduced-motion (spec §5). */
function WindDial({ fromDeg }: { fromDeg: number }) {
  return <svg className="wb-cond-dial" width={28} height={28} viewBox="0 0 36 36" aria-hidden="true">
    <circle cx={18} cy={18} r={16} className="wb-cond-dial-face" />
    <line x1={18} y1={18} x2={18} y2={7} className="wb-cond-dial-tick" transform={`rotate(${fromDeg} 18 18)`} />
    <line x1={18} y1={24} x2={18} y2={12} className="wb-cond-dial-needle" style={{ transform: `rotate(${(fromDeg + 180) % 360}deg)` }} />
  </svg>;
}

/** Visibility chip copy: Fog · 800 m / Mist · 2 km / Haze · 5 km — SI symbols in both languages. */
export function visibilityChip(visibilityM: number, zh: boolean): string {
  const kind = visibilityM >= 5000 ? zh ? '霾' : 'Haze' : visibilityM >= 1000 ? zh ? '轻雾' : 'Mist' : zh ? '雾' : 'Fog';
  return `${kind} · ${formatVisibility(visibilityM)}`;
}

/** Run/preview conditions HUD: one compact glass row of chips (spec §3.3, §7 interface). */
export function ConditionsHud({ conditions, zh, mode, atS }: { conditions: Conditions; zh: boolean; mode: 'run' | 'preview'; atS?: number }) {
  const [note, setNote] = useState(false);
  const wind = conditions.wind;
  const chips: Array<{ key: string; text: string; warn?: boolean }> = [];
  if (mode === 'preview') chips.push({ key: 'preview', text: `${zh ? '预览时刻' : 'Preview at'} ${clockS(atS ?? 0)}` });
  const fires = conditions.incidents.filter(row => row.kind === 'fire' && row.phase === 'active');
  if (fires.length) chips.push({ key: 'fire', warn: true, text: fires.length === 1 ? zh ? '火情进行中' : 'Fire active' : zh ? `${fires.length} 处火情` : `${fires.length} fires active` });
  for (const incident of conditions.incidents) {
    if (incident.phase !== 'active') continue;
    if (incident.kind === 'person_fall') chips.push({ key: incident.id, text: zh ? '行人跌倒' : 'Person fell' });
  }
  if (conditions.precipitationMmH > 0) {
    const level = conditions.precipitationMmH < 2.5 ? zh ? '小雨' : 'Light rain' : conditions.precipitationMmH < 7.6 ? zh ? '中雨' : 'Moderate rain' : zh ? '大雨' : 'Heavy rain';
    chips.push({ key: 'rain', text: `${level} · ${formatMmH(conditions.precipitationMmH)} mm/h` });
  }
  if (conditions.visibilityM !== null) chips.push({ key: 'visibility', text: visibilityChip(conditions.visibilityM, zh) });
  if (conditions.light === 'dusk') chips.push({ key: 'dusk', text: zh ? '黄昏' : 'Dusk' });
  return <div className={`wb-cond-hud${mode === 'preview' ? ' preview' : ''}`} data-testid="wb-conditions-hud">
    {wind && <span className="wb-cond-wind" data-testid="wb-conditions-wind">
      <WindDial fromDeg={wind.fromDeg} />
      <span className="wb-cond-wind-text">
        <span className="wb-cond-wind-line">
          {zh ? `${compassPointZh(wind.fromDeg)}风 ${wind.speed.toFixed(1)} m/s` : `Wind ${wind.speed.toFixed(1)} m/s · from ${compassPoint(wind.fromDeg)}`}
          <span className="wb-cond-bar" role="img" aria-label={zh ? '风速强度' : 'Wind intensity'}>
            {[0, 1, 2, 3, 4].map(index => <i key={index} className={wind.speed >= INTENSITY[index] ? 'on' : ''} />)}
          </span>
        </span>
        {wind.gust && <span className="wb-cond-gust">{zh ? `阵风至 ${wind.gustEndS !== undefined ? clockS(wind.gustEndS) : ''}` : `Gust until ${wind.gustEndS !== undefined ? clockS(wind.gustEndS) : ''}`}</span>}
      </span>
    </span>}
    {(chips.length > 0 || wind) && <span className="wb-cond-chips">
      {chips.map(chip => <span key={chip.key} className={`wb-cond-chip${chip.warn ? ' warn' : ''}`}>{chip.text}</span>)}
      <button type="button" className="wb-cond-info" aria-expanded={note} aria-label={zh ? '关于视觉呈现的说明' : 'About the visual-only note'}
        onClick={() => setNote(previous => !previous)}>i</button>
    </span>}
    {note && <p className="wb-cond-note" role="note">{zh ? NOTE_ZH : NOTE_EN}</p>}
  </div>;
}
