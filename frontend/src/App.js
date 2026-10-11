import React, { lazy, Suspense, useRef } from 'react';
import { ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { Navigate, useLocation } from 'react-router-dom';
import { I18nProvider, useI18n } from './i18n/I18nProvider';
import ConsoleShell from './console/ConsoleShell';
import HomePage from './console/HomePage';
import { ConsoleNotifications } from './console/Notifications';
import { PageState } from './console/PageState';
import { ShortcutsHelp } from './console/shortcuts';
import Welcome from './newcomers/Welcome';

const RunsPage = lazy(() => import('./pages/RunsPage'));
const AgentConsole = lazy(() => import('./pages/AgentConsole'));
const AeroGraphPage = lazy(() => import('./pages/AeroGraphPage'));
const ViewerDemoPage = lazy(() => import('./viewport/ViewerDemoPage'));
const CityStudioPage = lazy(() => import('./studio/StudioPage'));
const ConsoleGuide = lazy(() => import('./console/ConsoleGuide'));

function RoutedContent() {
  const { t } = useI18n();
  const location = useLocation();
  const { pathname, search } = location;
  const selectedRun = /^\/(runs|inspect)\/[^/]+$/.test(pathname);
  const lastRun = useRef();
  if (selectedRun) lastRun.current = { pathname, search };
  const activeRun = lastRun.current;
  const loading = <PageState kind="loading" title={t('Loading console')} />;
  if (new URLSearchParams(search).has('capture')) return <Suspense fallback={loading}><RunsPage /></Suspense>;
  return <ConsoleShell activeRun={activeRun}>
    <ShortcutsHelp />
    <Welcome />
    <Suspense fallback={loading}>
      {/* Keep the operator source and shared temporal store alive across navigation. */}
      {activeRun && <div hidden={!selectedRun}><RunsPage route={activeRun} interactive={selectedRun} /></div>}
      {!selectedRun && (pathname === '/' ? <HomePage />
        : pathname === '/studio' ? (new URLSearchParams(search).has('guide') ? <ConsoleGuide /> : <CityStudioPage />)
        : pathname === '/runs' ? <RunsPage />
        : pathname === '/inspect' ? <RunsPage inspectList />
        : pathname.startsWith('/agents/') ? <AgentConsole />
        : pathname === '/viewer-demo' ? <ViewerDemoPage />
        : pathname === '/aerograph' ? <AeroGraphPage />
        : pathname.startsWith('/docs/platform/') ? <ConsoleGuide />
        : <Navigate to={`/${search}`} replace />)}
    </Suspense>
  </ConsoleShell>;
}

function LocalizedConsole() {
  const { locale } = useI18n();
  return <ConfigProvider locale={locale === 'zh-CN' ? zhCN : enUS} button={{ autoInsertSpace: false }} theme={{ token: { colorPrimary: '#356b9e', colorText: '#233044', colorBorder: '#d8dee6', borderRadius: 6, fontFamily: "'Segoe UI', system-ui, 'Microsoft YaHei', sans-serif" } }}>
    <ConsoleNotifications><RoutedContent /></ConsoleNotifications>
  </ConfigProvider>;
}

export default function App() {
  return <I18nProvider><LocalizedConsole /></I18nProvider>;
}
