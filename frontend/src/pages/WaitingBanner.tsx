import { Alert, Button } from 'antd';
import type { WaitingContext } from '../feeds/http';
import { displayTime } from './display-time';

// Waiting on live operator watermark progress is normal for a live run, not an error.
export function WaitingBanner({ waiting, terminal, injectionHref, onInject }: { waiting?: WaitingContext; terminal?: boolean; injectionHref?: string; onInject?: () => void }) {
  if (!waiting) return null;
  const streams = waiting.stream_ids.join(', ');
  const at = displayTime(waiting.at_ns);
  const message = terminal
    ? `The declared wait budget expired while awaiting ${streams} at ${at}.`
    : waiting.stream_ids.includes('operator') ? `Ready for an operator event at ${at}. Inject event sends the prepared event. Edit its payload in Run controls.` : `Waiting for ${streams} at ${at}.`;
  return <Alert type={terminal ? 'error' : 'info'} showIcon role="status" data-testid={terminal ? 'input-timeout-banner' : 'waiting-banner'}
    message={terminal ? 'Input wait timed out' : waiting.stream_ids.includes('operator') ? 'Waiting for operator event' : 'Waiting for external input'} description={message}
    action={!terminal && <Button href={injectionHref} onClick={onInject}>Inject event</Button>} />;
}
