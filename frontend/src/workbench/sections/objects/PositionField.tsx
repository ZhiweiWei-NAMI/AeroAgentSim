import { useEffect, useRef, useState } from 'react';
import { num, strictVector3 } from './model';

/** Atomic three-number editing: malformed text can never enter the scene draft. */
export function PositionField({ value, path, onChange, reportValidity, focus, L }: {
  value: unknown; path: string; onChange: (path: string, value: unknown) => void;
  reportValidity: (path: string, valid: boolean) => void; focus?: string; L: (en: string, zh: string) => string;
}) {
  const initial = () => strictVector3(value) ? value.map(String) : ['', '', ''];
  const [raw, setRaw] = useState(initial), [touched, setTouched] = useState(false);
  const source = JSON.stringify(value), committed = useRef(source);
  const root = useRef<HTMLFieldSetElement>(null);
  useEffect(() => {
    if (committed.current !== source) { committed.current = source; setRaw(initial()); setTouched(false); }
  }, [source]);
  const vector = raw.map(num), valid = vector.every(part => part !== undefined);
  useEffect(() => { reportValidity(path, valid); return () => reportValidity(path, true); }, [path, reportValidity, valid]);
  useEffect(() => { if (focus === path || focus?.startsWith(path + '/')) root.current?.scrollIntoView({ block: 'nearest' }); }, [path, focus]);
  const commit = () => { setTouched(true); if (valid) onChange(path, vector); };
  return <fieldset ref={root} className="wb-position-field" data-path={path}>
    <legend>{L('Position ENU (m)', '位置 ENU（米）')}</legend>
    <div className="wb-add-grid">
      {[L('east (m)', '东向 (m)'), L('north (m)', '北向 (m)'), L('up (m)', '天向 (m)')].map((label, index) => <label className="wb-field" key={index}>
        <span>{label}</span><input inputMode="decimal" value={raw[index]} aria-invalid={(touched || !strictVector3(value)) && vector[index] === undefined}
          onChange={event => { setRaw(previous => previous.map((text, axis) => axis === index ? event.target.value : text)); setTouched(true); }}
          onBlur={commit} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); event.currentTarget.blur(); } }} />
      </label>)}
    </div>
    {!valid && (touched || !strictVector3(value)) && <p className="wb-field-error" role="alert">{L('Enter three finite numbers for east, north and up.', '请为东、北、天方向输入三个有限数值。')}</p>}
  </fieldset>;
}
