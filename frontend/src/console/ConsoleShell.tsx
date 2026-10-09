import React, { useMemo } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  HomeIcon, StudioIcon, GraphIcon, RunsIcon, InspectIcon, HelpIcon, LogoIcon,
} from './icons';
import './console.css';
import { SHORTCUTS_HELP_EVENT } from './shortcuts';

/* ConsoleShell — the product shell for the open-source research console.
 * Root-owned routing: it renders chrome only and never mounts the city viewer,
 * so no city styles are touched. All class names are `.console-*`. */

export interface Crumb { label: string; to?: string }
export interface NavItem { to: string; label: string; icon: React.ReactNode; testid: string; match: (pathname: string) => boolean }

const navItems: NavItem[] = [
  { to: '/', label: 'Home', icon: <HomeIcon />, testid: 'nav-home', match: p => p === '/' },
  { to: '/studio', label: 'Studio', icon: <StudioIcon />, testid: 'nav-studio', match: p => p.startsWith('/studio') },
  { to: '/aerograph', label: 'AeroGraph', icon: <GraphIcon />, testid: 'nav-aerograph', match: p => p.startsWith('/aerograph') },
  { to: '/runs', label: 'Runs', icon: <RunsIcon />, testid: 'nav-runs', match: p => p.startsWith('/runs') },
  { to: '/inspect', label: 'Inspect', icon: <InspectIcon />, testid: 'nav-inspect', match: p => p.startsWith('/inspect') },
];

export function breadcrumbs(pathname: string): Crumb[] {
  const root = navItems.find(item => item.match(pathname));
  if (!root) return [{ label: pathname.startsWith('/agents/') ? 'Agent decisions' : 'Console' }];
  const crumbs: Crumb[] = [{ label: root.label, to: root.to }];
  const rest = pathname.slice(root.to.length).replace(/^\/+|\/+$/g, '');
  if (rest) {
    const segments = rest.split('/').map(decodeURIComponent);
    segments.forEach((segment, index) => {
      const to = `${root.to}/${segments.slice(0, index + 1).map(encodeURIComponent).join('/')}`;
      crumbs.push(index === segments.length - 1
        ? { label: index === segments.length - 1 ? (pathname.startsWith('/agents/') ? 'Agent decisions' : 'Selected run') : segment }
        : { label: index === segments.length - 1 ? (pathname.startsWith('/agents/') ? 'Agent decisions' : 'Selected run') : segment, to });
    });
  }
  return crumbs;
}

export interface ConsoleShellProps {
  children: React.ReactNode;
  /** Extra actions rendered at the right edge of the topbar (before the help button). */
  actions?: React.ReactNode;
  activeRun?: { pathname: string; search: string };
}

export default function ConsoleShell({ children, actions, activeRun }: ConsoleShellProps) {
  const { pathname } = useLocation();
  const crumbs = useMemo(() => breadcrumbs(pathname), [pathname]);

  const { search } = useLocation();
  const api = new URLSearchParams(search).get('api');
  const apiSuffix = api ? `?api=${encodeURIComponent(api)}` : '';
  const destination = (to: string) => to === '/inspect' && activeRun ? `${activeRun.pathname.replace(/^\/runs\//, '/inspect/')}${activeRun.search}` : `${to}${apiSuffix}`;
  const help = () => document.dispatchEvent(new CustomEvent(SHORTCUTS_HELP_EVENT));

  return (
    <div className="console-shell" data-testid="console-shell">
      <aside className="console-sidebar">
        <Link to={destination("/")} className="console-brand">
          <LogoIcon size={22} />
          <span>
            AeroAgentSim
            <span className="console-brand-sub">research console</span>
          </span>
        </Link>
        <nav className="console-nav" aria-label="Console sections">
          <div className="console-nav-section">Navigate</div>
          {navItems.map(item => (
            <Link
              key={item.to}
              to={destination(item.to)}
              className={`console-nav-link${item.match(pathname) ? ' is-active' : ''}`}
              data-testid={item.testid}
              aria-current={item.match(pathname) ? 'page' : undefined}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="console-sidebar-foot">
          <div>Open-source research console</div>
          <div>for shared-runtime agent simulation</div>
        </div>
      </aside>
      <div className="console-main">
        <header className="console-topbar">
          <nav className="console-crumbs" aria-label="Breadcrumb">
            {crumbs.map((crumb, index) => (
              <React.Fragment key={`${crumb.label}-${index}`}>
                {index > 0 && <span className="console-crumb-sep" aria-hidden>/</span>}
                {crumb.to
                  ? <Link to={destination(crumb.to)}>{crumb.label}</Link>
                  : <span className="console-crumb-current">{crumb.label}</span>}
              </React.Fragment>
            ))}
          </nav>
          {actions}
          <button type="button" className="console-help-btn" onClick={help} aria-label="Keyboard shortcuts">
            <HelpIcon size={14} />
            Shortcuts
            <kbd>?</kbd>
          </button>
        </header>
        <main className="console-content">{children}</main>
        <footer className="console-footer">
          <span>AeroAgentSim — open-source research console</span>
          <a href="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" target="_blank" rel="noreferrer">Source</a>
          <a href="https://github.com/ZhiweiWei-NAMI/AeroAgentSim/tree/main/docs" target="_blank" rel="noreferrer">Docs</a>
          <span>Single-writer domain plugins · versioned runs</span>
        </footer>
      </div>
    </div>
  );
}
