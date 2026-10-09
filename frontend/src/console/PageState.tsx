import type { ReactNode } from 'react';

export interface PageHeaderProps {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}

/** Console page header (`.console-page-header`): optional eyebrow, title, description, action slot. */
export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <header className="console-page-header">
      {eyebrow && <p className="console-page-header-eyebrow">{eyebrow}</p>}
      <div className="console-page-header-body">
        <h1>{title}</h1>
        {description && <p className="console-page-header-description">{description}</p>}
      </div>
      {actions && <div className="console-page-header-actions">{actions}</div>}
    </header>
  );
}

export type PageStateKind = 'loading' | 'empty' | 'error';

export interface PageStateProps {
  kind: PageStateKind;
  title: string;
  description?: string;
  action?: ReactNode;
}

/** Inline SVG status glyphs — no extra dependencies. */
function StatusGlyph({ kind }: { kind: PageStateKind }) {
  if (kind === 'loading') {
    return (
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false" className="console-state-glyph">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
        <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    );
  }
  if (kind === 'error') {
    return (
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false" className="console-state-glyph">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M12 7v6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <circle cx="12" cy="16.5" r="1.25" fill="currentColor" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false" className="console-state-glyph">
      <rect x="4" y="5" width="16" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M8 10h8M8 14h5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Console page state (`.console-state`): loading / empty / error.
 * `role` maps to the correct accessibility semantics per kind.
 */
export function PageState({ kind, title, description, action }: PageStateProps) {
  return (
    <section
      className={`console-state console-state-${kind}`}
      role={kind === 'error' ? 'alert' : 'status'}
      aria-busy={kind === 'loading' ? true : undefined}
    >
      <StatusGlyph kind={kind} />
      <h2 className="console-state-title">{title}</h2>
      {description && <p className="console-state-description">{description}</p>}
      {action && <div className="console-state-action">{action}</div>}
    </section>
  );
}
