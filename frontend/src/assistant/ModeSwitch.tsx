import { useI18n } from '../i18n/I18nProvider';
import { Icon } from './icons';

export type EditMode = 'form' | 'chat';
export interface Origin { x: number; y: number }

/** Form ↔ Conversation. The same control sits in both panels so the thumb is continuous across the rewire. */
export function ModeSwitch({ mode, onChange, switching }: { mode: EditMode; onChange: (mode: EditMode, origin: Origin) => void; switching?: boolean }) {
  const { locale } = useI18n(), L = (en: string, cn: string) => locale === 'zh-CN' ? cn : en;
  const pick = (next: EditMode, target: HTMLElement) => {
    if (next === mode || switching) return;
    const rect = target.closest('.as-switch')!.getBoundingClientRect();
    onChange(next, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  };
  return <div className={`as-switch${switching ? ' is-switching' : ''}`} data-mode={mode} role="tablist" aria-label={L('How to edit', '编辑方式')} data-testid="as-mode-switch">
    <span className="as-switch-thumb" aria-hidden />
    <button role="tab" aria-selected={mode === 'form'} className={mode === 'form' ? 'is-on' : ''} onClick={event => pick('form', event.currentTarget)} data-testid="as-mode-form"><Icon name="form" size={12} />{L('Form', '表单')}</button>
    <button role="tab" aria-selected={mode === 'chat'} className={mode === 'chat' ? 'is-on' : ''} onClick={event => pick('chat', event.currentTarget)} data-testid="as-mode-chat"><Icon name="chat" size={12} />{L('Conversation', '对话')}</button>
  </div>;
}
