import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Drawer } from 'antd';

export interface DetailsProps {
  /** Panel title; defaults to 'Details'. */
  title?: string;
  /** Trigger button label; defaults to 'Details'. */
  buttonLabel?: string;
  children: ReactNode;
}

/**
 * Reusable collapsible details panel backed by an antd Drawer.
 *
 * Children are lazily rendered on first open and then retained to preserve editing buffers, so heavy or raw
 * JSON content never enters the default DOM. Focus returns to the trigger on
 * close (button or Escape). Styling hook: `.console-details`.
 */
export function Details({ title = 'Details', buttonLabel = 'Details', children }: DetailsProps) {
  const [open, setOpen] = useState(false);
  const [visited, setVisited] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    // Return focus to the trigger whether closed by button, mask or Escape.
    requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  // antd Drawer closes on Escape already; make sure focus returns too.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) close();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, close]);

  return (
    <span className="console-details">
      <Button
        ref={triggerRef}
        htmlType="button"
        data-testid="details-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => { setVisited(true); open ? close() : setOpen(true); }}
      >
        {buttonLabel}
      </Button>
      <Drawer
        title={`${title} · details`}
        open={open}
        onClose={close}
        width={560}
      >
        {visited ? children : null}
      </Drawer>
    </span>
  );
}
