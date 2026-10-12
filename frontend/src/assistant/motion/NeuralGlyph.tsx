import { useEffect, useRef, useState, type CSSProperties } from 'react';

export type GlyphState = 'idle' | 'thinking' | 'error' | 'off';

const RING = 7.2;
const RING_PTS = [-90, -30, 30, 90, 150, 210].map(angle => ({ x: 12 + Math.cos(angle * Math.PI / 180) * RING, y: 12 + Math.sin(angle * Math.PI / 180) * RING }));
const CENTRE = { x: 12, y: 12 };
// 0–5 centre → ring, 6–11 ring → next ring.
const EDGES: Array<[number, number]> = [...RING_PTS.map((_, i) => [-1, i] as [number, number]), ...RING_PTS.map((_, i) => [i, (i + 1) % 6] as [number, number])];
const point = (index: number) => index < 0 ? CENTRE : RING_PTS[index];

const STATE_COLOR: Record<GlyphState, string> = { idle: '#8a7dff', thinking: '#8a7dff', error: '#e0453a', off: '#9aa6b2' };

/** The assistant's living mark: breathes when idle, fires sparks while thinking, bursts on each tool step. */
export function NeuralGlyph({ state, pulseKey, size = 22, title }: { state: GlyphState; pulseKey?: string | number; size?: number; title?: string }) {
  const ring = useRef<SVGGElement>(null);
  const [burst, setBurst] = useState(0);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    setBurst(value => value + 1);
  }, [pulseKey]);

  useEffect(() => {
    const group = ring.current;
    const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (state !== 'thinking' || reduced || !group) return;
    const sparks: Array<{ edge: number; at: number; el: SVGCircleElement }> = [];
    let raf = 0, timer = 0;
    const spawn = () => {
      if (sparks.length < 4) {
        const el = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        el.setAttribute('class', 'as-glyph-spark'); el.setAttribute('r', '1.1'); el.setAttribute('fill', '#38c6f4');
        group.appendChild(el);
        sparks.push({ edge: Math.floor(Math.random() * EDGES.length), at: performance.now(), el });
      }
      timer = window.setTimeout(spawn, 160 + Math.random() * 260);
    };
    const step = (now: number) => {
      for (let i = sparks.length - 1; i >= 0; i--) {
        const spark = sparks[i], k = Math.min(1, (now - spark.at) / 340), [a, b] = EDGES[spark.edge].map(point);
        spark.el.setAttribute('cx', String(a.x + (b.x - a.x) * k)); spark.el.setAttribute('cy', String(a.y + (b.y - a.y) * k));
        spark.el.setAttribute('opacity', String(k < 0.8 ? 1 : (1 - k) / 0.2));
        if (k >= 1) { spark.el.remove(); sparks.splice(i, 1); }
      }
      raf = requestAnimationFrame(step);
    };
    spawn(); raf = requestAnimationFrame(step);
    return () => { clearTimeout(timer); cancelAnimationFrame(raf); for (const spark of sparks) spark.el.remove(); };
  }, [state]);

  const classes = ['as-glyph', `is-${state}`, burst ? 'is-burst' : ''].filter(Boolean).join(' ');
  return <svg key={burst} className={classes} width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={title ?? 'Assistant'} style={{ color: STATE_COLOR[state] }}>
    <g ref={ring} className="as-glyph-ring">
      {EDGES.map(([a, b], i) => <line key={i} className="as-glyph-edge" x1={point(a).x} y1={point(a).y} x2={point(b).x} y2={point(b).y} stroke="currentColor" strokeWidth={0.7} style={{ '--i': i } as CSSProperties} />)}
      {RING_PTS.map((p, i) => <circle key={i} className="as-glyph-node" cx={p.x} cy={p.y} r={1.35} fill="currentColor" style={{ animationDelay: `${(i + 1) * 0.35}s` }} />)}
    </g>
    <circle className="as-glyph-node as-glyph-core" cx={12} cy={12} r={2.1} fill="currentColor" />
  </svg>;
}
