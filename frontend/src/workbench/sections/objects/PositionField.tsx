import { useContext, useEffect, useRef } from 'react';
import { num, strictVector3 } from './model';
import { StagedFormWorkspace, useStagedForm } from '../useStagedForm';
import { StagedFormNote } from '../tasks/op-ui';

/** Atomic three-number editing: malformed text can never enter the scene draft. */
export function PositionField({ value, path, onChange, reportValidity, focus, L }: {
  value: unknown; path: string; onChange: (path: string, value: unknown) => void;
  reportValidity: (path: string, valid: boolean) => void; focus?: string; L: (en: string, zh: string) => string;
}) {
  const initial = () => strictVector3(value) ? value.map(String) : ['', '', ''];
  const workspace = useContext(StagedFormWorkspace);
  const staged = useStagedForm(workspace?.id, `position:${path}`, { raw: initial(), touched: false });
  const { raw, touched } = staged.values;
  const root = useRef<HTMLFieldSetElement>(null);
  const vector = raw.map(num), valid = vector.every(part => part !== undefined);
  useEffect(() => { reportValidity(path, valid); return () => reportValidity(path, true); }, [path, reportValidity, valid]);
  useEffect(() => { if (focus === path || focus?.startsWith(path + '/')) root.current?.scrollIntoView({ block: 'nearest' }); }, [path, focus]);
  const commit = () => {
    staged.setValues(previous => ({ ...previous, touched: true }));
    if (valid) { onChange(path, vector); staged.clear(); }
  };
  return <fieldset ref={root} className="wb-position-field" data-path={path}>
    <legend>{L('Position ENU (m)', '位置 ENU（米）')}</legend>
    <StagedFormNote form={staged} L={L} />
    <div className="wb-form-grid">
      {[L('east (m)', '东向 (m)'), L('north (m)', '北向 (m)'), L('up (m)', '天向 (m)')].map((label, index) => <label className="wb-form-field" key={index}>
        <span className="wb-label">{label}</span><input inputMode="decimal" value={raw[index]} aria-invalid={(touched || !strictVector3(value)) && vector[index] === undefined}
          onChange={event => staged.setValues(previous => ({ raw: previous.raw.map((text, axis) => axis === index ? event.target.value : text), touched: true }))}
          onBlur={commit} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.blur(); } }} />
      </label>)}
    </div>
    {!valid && (touched || !strictVector3(value)) && <p className="wb-field-error" role="alert">{L('Enter three finite numbers for east, north and up.', '请为东、北、天方向输入三个有限数值。')}</p>}
  </fieldset>;
}
