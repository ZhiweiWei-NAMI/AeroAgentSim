import React, { lazy, Suspense } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { I18nProvider, useI18n } from './i18n/I18nProvider';

const RunsPage = lazy(() => import('./pages/RunsPage'));
const AgentConsole = lazy(() => import('./pages/AgentConsole'));
const ViewerDemoPage = lazy(() => import('./viewport/ViewerDemoPage'));
const CityStudioPage = lazy(() => import('./studio/StudioPage'));

function RoutedContent() {
  const { pathname, search } = useLocation();
  const { t } = useI18n();
  return <Suspense fallback={<div style={{ padding: 32 }}>{t('viewerLoading')}</div>}>
    {pathname.startsWith('/agents/') ? <AgentConsole /> : pathname === '/runs' || pathname.startsWith('/runs/') ? <RunsPage /> : pathname === '/viewer-demo' ? <ViewerDemoPage /> : pathname === '/studio' ? <CityStudioPage /> : <Navigate to={`/studio${search}`} replace />}
  </Suspense>;
}

export default function App() {
  return <I18nProvider><RoutedContent /></I18nProvider>;
}
