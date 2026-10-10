import { Dropdown, Select } from 'antd';
import React, { useMemo } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { HomeIcon, StudioIcon, GraphIcon, RunsIcon, InspectIcon, HelpIcon, LogoIcon } from './icons';
import { useI18n } from '../i18n/I18nProvider';
import './console.css';
import { SHORTCUTS_HELP_EVENT } from './shortcuts';

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
      const label = index === segments.length - 1 ? 'Selected run' : segment;
      crumbs.push(index === segments.length - 1 ? { label } : { label, to });
    });
  }
  return crumbs;
}
export interface ConsoleShellProps { children: React.ReactNode; actions?: React.ReactNode; activeRun?: { pathname: string; search: string } }
export default function ConsoleShell({ children, actions, activeRun }: ConsoleShellProps) {
  const { t, locale, setLocale } = useI18n();
  const { pathname, search } = useLocation();
  const crumbs = useMemo(() => breadcrumbs(pathname), [pathname]);
  const api = new URLSearchParams(search).get('api');
  const apiSuffix = api ? `?api=${encodeURIComponent(api)}` : '';
  const destination = (to: string) => {
    if (to !== '/inspect' || !activeRun) return `${to}${apiSuffix}`;
    const query = new URLSearchParams(activeRun.search); query.set('view', 'expert');
    return `${activeRun.pathname.replace(/^\/runs\//, '/inspect/')}?${query}`;
  };
  const help = () => document.dispatchEvent(new CustomEvent(SHORTCUTS_HELP_EVENT));
  return <div className="console-shell" data-testid="console-shell">
    <aside className="console-sidebar">
      <Link to={destination('/')} className="console-brand"><LogoIcon size={22} /><span>AeroAgentSim<span className="console-brand-sub">{t('research console')}</span></span></Link>
      <nav className="console-nav" aria-label={t('Console sections')}><div className="console-nav-section">{t('Navigate')}</div>{navItems.map(item => <Link key={item.to} to={destination(item.to)} className={`console-nav-link${item.match(pathname) ? ' is-active' : ''}`} data-testid={item.testid} aria-current={item.match(pathname) ? 'page' : undefined}>{item.icon}{t(item.label)}</Link>)}</nav>
      <div className="console-sidebar-foot"><div>{t('Open-source research console')}</div><div>{t('Explore, experiment, compare')}</div></div>
    </aside>
    <div className="console-main">
      <header className="console-topbar">
        <nav className="console-crumbs" aria-label={t('Breadcrumb')}>{crumbs.map((crumb, index) => <React.Fragment key={`${crumb.label}-${index}`}>{index > 0 && <span className="console-crumb-sep" aria-hidden>/</span>}{crumb.to ? <Link to={destination(crumb.to)}>{t(crumb.label)}</Link> : <span className="console-crumb-current">{t(crumb.label)}</span>}</React.Fragment>)}</nav>
        {actions}
        <Select className="console-language" data-testid="language-switch" aria-label={t('Language')} value={locale} onChange={setLocale} options={[{ value: 'en-US', label: 'English' }, { value: 'zh-CN', label: '中文' }]} />
        <Dropdown menu={{ items: [
          { key: 'welcome', label: <Link to={`${destination('/')}${apiSuffix ? '&' : '?'}welcome=1`} onClick={() => document.dispatchEvent(new CustomEvent('aas:welcome-help'))}>{t('Getting started')}</Link> },
          { key: 'shortcuts', label: t('Keyboard shortcuts'), onClick: help },
          { key: 'guide', label: <Link to={`/docs/platform/console.md${apiSuffix}`}>{t('Read the guide')}</Link> },
        ] }} trigger={['click']}><button type="button" className="console-help-btn" aria-label={t('Help')}><HelpIcon size={14} />{t('Help')}<kbd>?</kbd></button></Dropdown>
      </header>
      <main className="console-content">{children}</main>
      <footer className="console-footer"><span>{t('AeroAgentSim — open-source research console')}</span><a href="https://github.com/ZhiweiWei-NAMI/AeroAgentSim" target="_blank" rel="noreferrer">{t('Source')}</a><a href="https://github.com/ZhiweiWei-NAMI/AeroAgentSim/tree/main/docs" target="_blank" rel="noreferrer">{t('Docs')}</a><span>{t('Change one setting. Explore the difference.')}</span></footer>
    </div>
  </div>;
}
