import React, { lazy, Suspense, useRef } from 'react';
import { ConfigProvider } from 'antd';
import { Navigate, useLocation } from 'react-router-dom';
import { I18nProvider } from './i18n/I18nProvider';
import ConsoleShell from './console/ConsoleShell';
import HomePage from './console/HomePage';
import { ConsoleNotifications } from './console/Notifications';
import { PageState } from './console/PageState';
import { ShortcutsHelp } from './console/shortcuts';

const RunsPage = lazy(() => import('./pages/RunsPage'));
const AgentConsole = lazy(() => import('./pages/AgentConsole'));
const AeroGraphPage = lazy(() => import('./pages/AeroGraphPage'));
const ViewerDemoPage = lazy(() => import('./viewport/ViewerDemoPage'));
const CityStudioPage = lazy(() => import('./studio/StudioPage'));
const ConsoleGuide = lazy(() => import('./console/ConsoleGuide'));

function RoutedContent() {
  const location = useLocation();
  const { pathname, search } = location;
  const selectedRun = /^\/(runs|inspect)\/[^/]+$/.test(pathname);
  const lastRun = useRef();
  if (selectedRun) lastRun.current = { pathname, search };
  const activeRun = lastRun.current;
  const loading = <PageState kind="loading" title="Loading console" />;
  if (new URLSearchParams(search).has('capture')) return <Suspense fallback={loading}><RunsPage /></Suspense>;
  return <ConsoleShell activeRun={activeRun}>
    <ShortcutsHelp />
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

export default function App() {
  return <ConfigProvider theme={{ token: { colorPrimary: '#0d7f74', colorText: '#17222e', colorBorder: '#cdd7e0', borderRadius: 6, fontFamily: "'Segoe UI', system-ui, sans-serif" } }}>
    <I18nProvider><ConsoleNotifications><RoutedContent /></ConsoleNotifications></I18nProvider>
  </ConfigProvider>;
}
