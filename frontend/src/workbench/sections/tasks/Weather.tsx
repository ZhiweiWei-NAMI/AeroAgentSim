import { useState } from 'react';
import type { SectionProps } from '../../../workbench/sections/types';
import { operation } from '../../../workbench/sections/operations';
import type { Draft } from './model';
import { nsToS, windSpeed } from './model';
import { OpApply, StagedNumber } from './op-ui';
import { gustOp, gustProblem } from './ops';

interface Props {
  environmentName: string; environment: Draft; draft: Draft; edit: SectionProps['edit']; focus?: string; L: SectionProps['L'];
  api: SectionProps['api']; project: SectionProps['project']; onProject: SectionProps['onProject']; saveDraft: SectionProps['saveDraft']; zh: boolean;
}

/** Wind & gusts through C11 set_wind / add_gust (preview → apply). set_wind carries speed only —
 *  the operation schema has no direction parameter, so none is shown or invented (T1-014). */
export function WeatherBlock({ environmentName, environment, draft, edit, focus, L, api, project, onProject, saveDraft, zh }: Props) {
  const profiles = (environment.config?.profiles ?? {}) as Record<string, Draft>;
  const slots = Object.entries(profiles).filter(([, profile]) => 'lowalt.wind' in (profile ?? {}));
  const [start, setStart] = useState('60'), [end, setEnd] = useState('90'), [speed, setSpeed] = useState('9');
  const [bgSpeed, setBgSpeed] = useState(''), [gust, setGust] = useState<{ start_s: number; end_s: number; speed_mps: number } | undefined>();
  const duration_s = nsToS((draft.run as Draft | undefined)?.until_ns);
  const gustErrors = gustProblem({ start_s: start, end_s: end, speed_mps: speed, duration_s }, zh);
  const bgErrors = bgSpeed.trim() !== '' && (!Number.isFinite(Number(bgSpeed)) || Number(bgSpeed) < 0)
    ? L('Enter a wind speed of 0 m/s or more.', '请输入不小于 0 m/s 的风速。') : undefined;
  const canGust = !gustErrors, canWind = bgSpeed.trim() !== '' && !bgErrors;
  return <div className="wb-weather" data-testid="wb-weather">
    <p className="hint">{L('Wind and gusts go through the operations API (set_wind / add_gust); the server writes the environment profile. Gust times are seconds and must stay inside the run duration.', '风与阵风通过操作 API（set_wind / add_gust）设置；环境配置由服务器写入。阵风时间以秒计，且必须处于运行时长之内。')}</p>
    {!slots.length && <div className="wb-empty">{L('No lowalt wind profile in this scenario; the server rejects wind operations without one. Add a lowalt.wind profile via YAML or the engine tree.', '该场景没有低空风场配置；没有它服务器会拒绝风操作。请通过 YAML 或引擎树添加 lowalt.wind profile。')}</div>}
    {slots.map(([entity, profile]) => {
      const wind = profile['lowalt.wind'] as Draft;
      const gusts = Array.isArray(wind.gusts) ? wind.gusts as Draft[] : [];
      const shownSpeed = windSpeed(wind.value);
      const base = `engines/${environmentName}/config/profiles/${entity}/lowalt.wind`;
      return <div key={entity} className="wb-weather-entity">
        <details open><summary><span className="wb-mono">{entity}</span> · lowalt.wind</summary>
          <div className="wb-wind-row">
            <StagedNumber label={`${L('Background speed', '背景风速')} (m/s)`} value={bgSpeed} min={0} onChange={setBgSpeed} />
            {shownSpeed !== undefined && <p className="hint">{L(`Currently ${shownSpeed} m/s`, `当前 ${shownSpeed} m/s`)}</p>}
          </div>
          <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
            operations={canWind ? [operation('set_wind', { speed_mps: Number(bgSpeed) })] : undefined}
            missing={bgErrors} label={L('Set background wind', '设置背景风')} labelZh="设置背景风" small />
          <div><p className="hint">{L('Gusts', '阵风')}</p>
            {!gusts.length && <div className="wb-empty">{L('No gusts.', '没有阵风。')}</div>}
            {gusts.map((entry, index) => <div key={index} className="wb-gust-row" data-path={`${base}/gusts/${index}`}>
              <span className="wb-tag wb-mono">{nsToS(entry.start_ns)}–{nsToS(entry.end_ns)} s</span>
              <span className="wb-tag">{windSpeed(entry.value) ?? '?'} m/s</span>
              <button className="wb-btn small danger" aria-label={L(`Remove gust ${index + 1}`, `移除阵风 ${index + 1}`)} onClick={() => edit(`/${base}/gusts`, gusts.filter((_, i) => i !== index))}>{L('Remove', '移除')}</button>
            </div>)}
          </div>
          <div className="wb-gust-add" style={{ display: 'grid', gap: 6 }}>
            <StagedNumber label={`${L('Start', '开始')} (s)`} value={start} min={0} onChange={setStart} />
            <StagedNumber label={`${L('End', '结束')} (s)`} value={end} min={0} onChange={setEnd} />
            <StagedNumber label={`${L('Speed', '风速')} (m/s)`} value={speed} min={0} onChange={setSpeed} />
            <button className="wb-btn small" disabled={!canGust || !!gust} onClick={() => setGust({ start_s: Number(start), end_s: Number(end), speed_mps: Number(speed) })}>{L('Stage gust', '暂存阵风')}</button>
            {gustErrors && Object.entries(gustErrors).map(([field, message]) => <p key={field} role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{message}</p>)}
          </div>
          {gust && <OpApply api={api} project={project} onProject={onProject} saveDraft={saveDraft}
            operations={[gustOp(gust)]}
            label={L('Add gust', '添加阵风')} labelZh="添加阵风" primary small
            summary={<p className="hint" role="status">{L(`Staged gust ${gust.start_s}–${gust.end_s} s at ${gust.speed_mps} m/s.`, `已暂存阵风：${gust.start_s}–${gust.end_s} 秒，${gust.speed_mps} m/s。`)}</p>}
            onApplied={() => setGust(undefined)} />}
          {focus && <p className="hint wb-mono" style={{ fontSize: 11 }}>{base}</p>}
        </details>
      </div>;
    })}
  </div>;
}
