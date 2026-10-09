import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

export type ConsoleNoticeKind = 'success' | 'error' | 'info';

export interface ConsoleNotice {
  kind: ConsoleNoticeKind;
  message: string;
}

interface ConsoleNoticeEntry extends ConsoleNotice {
  key: number;
  expiresAt: number;
}

interface ConsoleNotificationsContextValue {
  notify: (notice: ConsoleNotice) => void;
}

const ConsoleNotificationsContext = createContext<ConsoleNotificationsContextValue | undefined>(undefined);

const MAX_VISIBLE = 4;
const NOTICE_TTL_MS = 6000;

/**
 * Console notification provider. Renders a bounded aria-live toast stack
 * (`.console-toasts`, testid `console-notices`) so it can be mounted anywhere,
 * including outside the shell, without coupling to antd message singletons.
 *
 * Notices are NOT deduplicated: a repeat notice is a real later receipt and is
 * shown again. Toasts expire after a bounded delay and the stack is capped.
 */
export function ConsoleNotifications({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ConsoleNoticeEntry[]>([]);
  const nextKey = useRef(0);

  const notify = useCallback((notice: ConsoleNotice) => {
    if (!notice || typeof notice.message !== 'string' || notice.message === '') return;
    const key = ++nextKey.current;
    setEntries(previous => [...previous, { ...notice, key, expiresAt: Date.now() + NOTICE_TTL_MS }].slice(-MAX_VISIBLE));
  }, []);

  // Bounded expiry: remove entries whose TTL elapsed.
  useEffect(() => {
    if (!entries.length) return;
    const timers = entries.map(entry => setTimeout(() => {
      setEntries(previous => previous.filter(candidate => candidate.key !== entry.key));
    }, Math.max(0, entry.expiresAt - Date.now())));
    return () => timers.forEach(clearTimeout);
  }, [entries]);

  const value = useMemo(() => ({ notify }), [notify]);
  return (
    <ConsoleNotificationsContext.Provider value={value}>
      {children}
      <ol className="console-toasts" data-testid="console-notices" aria-live="polite" aria-relevant="additions">
        {entries.map(entry => (
          <li key={entry.key} className={`console-toast console-toast-${entry.kind}`} role={entry.kind === 'error' ? 'alert' : 'status'}>
            {entry.message}
          </li>
        ))}
      </ol>
    </ConsoleNotificationsContext.Provider>
  );
}

export function useConsoleNotice(): (notice: ConsoleNotice) => void {
  const context = useContext(ConsoleNotificationsContext);
  if (!context) throw new Error('useConsoleNotice requires <ConsoleNotifications>');
  return context.notify;
}
