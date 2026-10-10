import { Alert, Button } from 'antd';
import type { WaitingContext } from '../feeds/http';
import { displayTime } from './display-time';
import { useI18n } from '../i18n/I18nProvider';

// Waiting on live operator watermark progress is normal for a live run, not an error.
export function WaitingBanner({ waiting, terminal, injectionHref, onInject }: { waiting?: WaitingContext; terminal?: boolean; injectionHref?: string; onInject?: () => void }) {
  const { t } = useI18n();
  if (!waiting) return null;
  const streams = waiting.stream_ids.join(', ');
  const at = displayTime(waiting.at_ns);
  const message = terminal
    ? t('The declared wait budget expired while awaiting {streams} at {at}.', {streams,at})
    : waiting.stream_ids.includes('operator') ? t('The scenario is waiting for you at {at}. Choose an event to continue.', {at}) : t('Waiting for {streams} at {at}.', {streams,at});
  return <Alert type={terminal ? 'error' : 'info'} showIcon role="status" data-testid={terminal ? 'input-timeout-banner' : 'waiting-banner'}
    message={terminal ? t('Input wait timed out') : waiting.stream_ids.includes('operator') ? t('Ready for your event') : t('Waiting for an external update')} description={message}
    action={!terminal && <Button href={onInject ? undefined : injectionHref} onClick={onInject}>{t('Introduce an event')}</Button>} />;
}
