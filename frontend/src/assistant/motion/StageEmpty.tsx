import { useEffect, useState } from 'react';
import './showcase.css';

const PARTICLES = [0, 1, 2, 3, 4, 5, 6, 7];

/** Holographic pedestal shown behind the empty 3D stage: perspective floor, rings, beam, particles. */
export function StageEmpty({ title, body }: { title: string; body: string }) {
  const reduced = useState(
    () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches,
  )[0];
  const [risen, setRisen] = useState(reduced);

  useEffect(() => {
    if (reduced) return;
    const raf = requestAnimationFrame(() => setRisen(true));
    return () => cancelAnimationFrame(raf);
  }, [reduced]);

  return (
    <div className="as-show-stage" aria-live="polite">
      <div className="as-show-stage-floor" aria-hidden="true" />
      <div className="as-show-stage-pedestal" aria-hidden="true">
        <span className="as-show-stage-ring as-show-stage-ring-1" />
        <span className="as-show-stage-ring as-show-stage-ring-2" />
        <span className="as-show-stage-ring as-show-stage-ring-3" />
        <span className="as-show-stage-disc" />
      </div>
      <div className="as-show-stage-beam" aria-hidden="true" />
      {!reduced && (
        <div className="as-show-stage-particles" aria-hidden="true">
          {PARTICLES.map(i => (
            <span key={i} className={`as-show-stage-particle as-show-stage-particle-${i + 1}`} />
          ))}
        </div>
      )}
      <div className={`as-show-stage-text${risen ? ' is-risen' : ''}`}>
        <h2 className="as-show-stage-title">{title}</h2>
        <p className="as-show-stage-body">{body}</p>
      </div>
    </div>
  );
}
