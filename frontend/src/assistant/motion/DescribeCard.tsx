import { useEffect, useRef, useState } from 'react';
import { NeuralGlyph } from './NeuralGlyph';
import { useI18n } from '../../i18n/I18nProvider';
import './showcase.css';

type Phase = 'typing' | 'holding' | 'deleting' | 'pausing';

const PROMPTS = {
  'en-US': [
    'A logistics drone notices someone has fallen, hands its parcel to a nearby courier and photographs the person.',
    'Two inspection drones share a power line; one hands over when its battery runs low.',
    'Make the lunch rush twice as busy, keep everything else the same.',
  ],
  'zh-CN': [
    '物流无人机发现有人跌倒，把包裹交给附近的外卖配送员，然后给跌倒的人拍照。',
    '两架巡检无人机共同巡检一条电力线，电量不足时交接给另一架。',
    '午餐高峰订单翻倍，其他保持不变。',
  ],
} as const;

const TYPE_MS = 28;
const DELETE_MS = 12;
const HOLD_MS = 2200;
const PAUSE_MS = 350;

const STEPS = {
  'en-US': ['Describe', 'Review the plan', 'Confirm once'],
  'zh-CN': ['描述', '审阅方案', '确认一次'],
} as const;

/** Gallery hero card: a dark console that types example scenarios on loop. Whole card is one button. */
export function DescribeCard({ onStart, imageSrc }: { onStart: () => void; imageSrc: string }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const prompts = PROMPTS[zh ? 'zh-CN' : 'en-US'];
  const steps = STEPS[zh ? 'zh-CN' : 'en-US'];

  const reduced = useRef(
    typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches,
  ).current;
  const [shown, setShown] = useState(reduced ? prompts[0] : '');
  const [promptIndex, setPromptIndex] = useState(0);
  const [phase, setPhase] = useState<Phase>(reduced ? 'holding' : 'typing');
  const [visible, setVisible] = useState(false);
  const timer = useRef<number>(), root = useRef<HTMLButtonElement>(null);

  // Single timer chain; fully cleaned up on unmount.
  useEffect(() => {
    if (reduced) return;
    const schedule = (fn: () => void, ms: number) => {
      timer.current = window.setTimeout(fn, ms);
    };
    const tick = (currentPhase: Phase, currentPrompt: number, chars: number) => {
      const text = prompts[currentPrompt];
      if (currentPhase === 'typing') {
        if (chars < text.length) {
          setShown(text.slice(0, chars + 1));
          schedule(() => tick('typing', currentPrompt, chars + 1), TYPE_MS);
        } else {
          setPhase('holding');
          schedule(() => tick('holding', currentPrompt, chars), HOLD_MS);
        }
      } else if (currentPhase === 'holding') {
        setPhase('deleting');
        schedule(() => tick('deleting', currentPrompt, chars), PAUSE_MS);
      } else if (currentPhase === 'deleting') {
        if (chars > 0) {
          setShown(text.slice(0, chars - 1));
          schedule(() => tick('deleting', currentPrompt, chars - 1), DELETE_MS);
        } else {
          setPhase('pausing');
          const next = (currentPrompt + 1) % prompts.length;
          schedule(() => {
            setPromptIndex(next);
            setPhase('typing');
            tick('typing', next, 0);
          }, PAUSE_MS);
        }
      }
    };
    if (!visible) return; // typing pauses when the card is hidden or off-screen
    schedule(() => tick(phase, promptIndex, shown.length), 0);
    return () => window.clearTimeout(timer.current);
    // phase/promptIndex/shown are the chain's own carried state; only visibility should restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, reduced, prompts]);

  // Off-screen or hidden tab → freeze the chain (visible drives the effect above).
  useEffect(() => {
    let onScreen = true;
    const sync = () => setVisible(onScreen && !document.hidden);
    const io = new IntersectionObserver(entries => {
      onScreen = entries[0].isIntersecting;
      sync();
    }, { threshold: 0.15 });
    if (root.current) io.observe(root.current);
    document.addEventListener('visibilitychange', sync);
    return () => {
      io.disconnect();
      document.removeEventListener('visibilitychange', sync);
    };
  }, []);

  const activeStep = phase === 'typing' || phase === 'pausing' ? 0 : phase === 'holding' ? 1 : 2;

  return (
    <button
      type="button"
      ref={root}
      className="as-show-describe"
      onClick={onStart}
      data-testid="wb-describe"
    >
      <img className="as-show-describe-img" alt="" src={imageSrc} />
      <div className="as-show-describe-console">
        <div className="as-show-describe-seam" aria-hidden="true" />
        <div className="as-show-describe-head">
          <NeuralGlyph state="idle" size={28} />
          <span className="as-show-describe-eyebrow">{zh ? '助手 · 智能体原生' : 'ASSISTANT · AGENT-NATIVE'}</span>
        </div>
        <h3 className="as-show-describe-title">{zh ? '用你自己的话描述一个场景' : 'Describe a scenario in your own words'}</h3>
        <div className="as-show-describe-prompt" aria-hidden="true">
          <span className="as-show-describe-caret-glyph">›</span>
          <span className="as-show-describe-text">
            {shown}
            {!reduced && <span className="as-show-describe-caret" />}
          </span>
        </div>
        <div className="as-show-describe-flow" aria-hidden="true">
          {steps.map((label, i) => (
            <span key={label} className="as-show-describe-step">
              <span className={`as-show-describe-num${i === activeStep ? ' is-active' : ''}`}>{i + 1}</span>
              <span className={`as-show-describe-step-label${i === activeStep ? ' is-active' : ''}`}>{label}</span>
              {i < steps.length - 1 && <span className="as-show-describe-arrow">→</span>}
            </span>
          ))}
        </div>
        <span className="as-show-describe-cta">
          <span>{zh ? '开始对话' : 'Start a conversation'}</span>
          <span className="as-show-describe-cta-arrow" aria-hidden="true">→</span>
        </span>
      </div>
    </button>
  );
}
