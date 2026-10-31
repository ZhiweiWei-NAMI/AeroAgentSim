import React, { Suspense } from 'react';
import { lazyWithRetry, retryFailedLazyImports } from './lazyRetry';
import { Contained } from './assistant/Contained';
import { ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { Navigate, useLocation } from 'react-router-dom';
import { I18nProvider, useI18n } from './i18n/I18nProvider';
import ConsoleShell from './console/ConsoleShell';
import { ConsoleNotifications } from './console/Notifications';
import { PageState } from './console/PageState';
import { ApiConnection } from './console/ApiConnection';

const Workbench = lazyWithRetry(() => import('./workbench/Workbench'));
const AgentConsole = lazyWithRetry(() => import('./pages/AgentConsole'));
const ConsoleGuide = lazyWithRetry(() => import('./console/ConsoleGuide'));
const CaptureConsole = lazyWithRetry(() => import('./observations/CaptureConsole'), module => module.CaptureConsole);

/** Earlier addresses open the same place in the one workbench, keeping their context. */
function legacyTarget(pathname, search) {
  const query = new URLSearchParams(search), next = new URLSearchParams();
  if (query.get('api')) next.set('api', query.get('api'));
  const run = /^\/(?:runs|inspect)\/([^/]+)$/.exec(pathname)?.[1];
  if (run) {
    next.set('run', decodeURIComponent(run));
    next.set('mode', query.get('mode') === 'live' ? 'live' : pathname.startsWith('/inspect') ? 'replay' : query.get('mode') ?? 'live');
    for (const key of ['cut', 'entity']) if (query.get(key)) next.set(key === 'entity' ? 'sel' : key, query.get(key));
    return `/?${next}`;
  }
  if (pathname === '/runs' || pathname === '/inspect') { next.set('records', '1'); if (query.get('compare')) next.set('compare', query.get('compare')); return `/?${next}`; }
  if (pathname === '/studio') {
    const workspace = query.get('workspace');
    if (workspace) { next.set('panel', 'controls'); return `/w/${encodeURIComponent(workspace)}?${next}`; }
    next.set('gallery', '1'); return `/?${next}`;
  }
  if (pathname === '/aerograph') { next.set('contract', '1'); for (const key of ['type', 'workspace']) if (query.get(key)) next.set(key, query.get(key)); return `/?${next}`; }
  return undefined;
}

function RoutedContent() {
  const { t, locale } = useI18n();
  const L = (en, zh) => locale === 'zh-CN' ? zh : en;
  const { pathname, search } = useLocation();
  const loading = <PageState kind="loading" title={t('Loading console')} />;
  const query = new URLSearchParams(search);
  // The in-simulation camera renders through this page (server-side Chromium).
  if (query.has('capture')) return <Suspense fallback={loading}><CaptureConsole apiBase={query.get('api') ?? window.location.origin} assetManifestUrl={query.get('capture_assets')} /></Suspense>;
  if (pathname === '/' || pathname.startsWith('/w/')) return <Contained label="Workbench" resetKey={pathname} fallback={retry => <PageState kind="error"
    title={L('Could not display the workbench', '无法显示工作台')}
    description={L('Try again, or reload the page to load the latest console.', '请重试，或刷新页面以加载最新版控制台。')}
    action={<><button type="button" className="console-btn" onClick={() => { retryFailedLazyImports(); retry(); }}>{L('Retry', '重试')}</button>
      <button type="button" className="console-btn" onClick={() => window.location.reload()}>{L('Reload page', '刷新页面')}</button></>} />}>
    <Suspense fallback={loading}><Workbench /></Suspense>
  </Contained>;
  const target = legacyTarget(pathname, search);
  if (target) return <Navigate to={target} replace />;
  if (pathname.startsWith('/agents/') || pathname.startsWith('/docs/platform/')) {
    return <ConsoleShell><Suspense fallback={loading}>{pathname.startsWith('/agents/') ? <AgentConsole /> : <ConsoleGuide />}</Suspense></ConsoleShell>;
  }
  return <Navigate to={`/${search}`} replace />;
}

function LocalizedConsole() {
  const { locale } = useI18n();
  return <ConfigProvider locale={locale === 'zh-CN' ? zhCN : enUS} button={{ autoInsertSpace: false }} theme={{ token: { colorPrimary: '#1f7ae0', colorText: '#14202e', colorBorder: '#d8dee6', borderRadius: 7, fontFamily: "Inter, 'Segoe UI', system-ui, 'PingFang SC', 'Microsoft YaHei', sans-serif" } }}>
    <ConsoleNotifications><ApiConnection /><RoutedContent /></ConsoleNotifications>
  </ConfigProvider>;
}

export default function App() {
  return <I18nProvider><LocalizedConsole /></I18nProvider>;
}
