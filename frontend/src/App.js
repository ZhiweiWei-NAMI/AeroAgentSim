import React, { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Button, Layout, Menu, Select, Space, Tag, Typography } from 'antd';
import {
  AppstoreOutlined,
  BranchesOutlined,
  FundProjectionScreenOutlined,
  PlayCircleOutlined,
  ProfileOutlined,
  RadarChartOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';

import ReviewDrawer from './components/workbench/ReviewDrawer';
import { WorkbenchProvider, useWorkbench } from './context/WorkbenchContext';
import { I18nProvider, useI18n } from './i18n/I18nProvider';
import ClassCatalogPage from './pages/ClassCatalogPage';
import OverviewPage from './pages/OverviewPage';
import RunConsolePage from './pages/RunConsolePage';
import TrajectoriesLogsPage from './pages/TrajectoriesLogsPage';
import WorkflowStudioPage from './pages/WorkflowStudioPage';
import { systemApi } from './services/workbenchApi';
import './App.css';

const { Header, Content, Footer, Sider } = Layout;
const { Title, Text } = Typography;

function resolveSelectedKey(pathname, items) {
  const exact = items.find((item) => item.key === pathname);
  if (exact) {
    return exact.key;
  }
  const matched = items.find((item) => item.key !== '/' && pathname.startsWith(item.key));
  return matched ? matched.key : '/';
}

function AppShell() {
  const location = useLocation();
  const { locale, setLocale, t } = useI18n();
  const { draftConfig, reviewResult, reviewGraph, reviewError, reviewing, runReview } = useWorkbench();
  const [collapsed, setCollapsed] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');

  const navItems = useMemo(
    () => [
      { key: '/', icon: <FundProjectionScreenOutlined />, label: t('navOverview') },
      { key: '/catalog', icon: <AppstoreOutlined />, label: t('navCatalog') },
      { key: '/studio', icon: <BranchesOutlined />, label: t('navStudio') },
      { key: '/console', icon: <PlayCircleOutlined />, label: t('navConsole') },
      { key: '/runs', icon: <ProfileOutlined />, label: t('navRuns') },
    ],
    [t]
  );

  const selectedKey = useMemo(
    () => resolveSelectedKey(location.pathname, navItems),
    [location.pathname, navItems]
  );

  useEffect(() => {
    let active = true;
    const loadHealth = async () => {
      try {
        const nextHealth = await systemApi.getHealth();
        if (!active) {
          return;
        }
        setHealth(nextHealth);
        setHealthError('');
      } catch (error) {
        if (!active) {
          return;
        }
        setHealth(null);
        setHealthError(error?.message || 'Backend unavailable');
      }
    };
    loadHealth();
    const timer = window.setInterval(() => {
      loadHealth().catch(() => {});
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <>
      <Layout className="workbench-layout">
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={(value) => setCollapsed(value)}
          className="workbench-sider"
          width={252}
        >
          <div className="workbench-brand">
            <RadarChartOutlined className="workbench-brand-icon" />
            {!collapsed ? (
              <div>
                <Title level={4} className="workbench-brand-title">
                  AeroAgentSim
                </Title>
                <Text className="workbench-brand-subtitle">{t('appSubtitle')}</Text>
              </div>
            ) : null}
          </div>

          <Menu mode="inline" selectedKeys={[selectedKey]} className="workbench-menu">
            {navItems.map((item) => (
              <Menu.Item key={item.key} icon={item.icon}>
                <Link to={item.key}>{item.label}</Link>
              </Menu.Item>
            ))}
          </Menu>

          <div className="workbench-sider-footer">
            <Text className="workbench-sider-footnote">{t('techPackage')}</Text>
          </div>
        </Sider>

        <Layout>
          <Header className="workbench-header">
            <div>
              <Title level={3} className="workbench-header-title">
                {t('appTitle')}
              </Title>
              <Text type="secondary">{draftConfig?.metadata?.name || draftConfig?.name || 'AeroAgentSim Config'}</Text>
            </div>
            <Space wrap>
              <Tag color={healthError ? 'error' : 'green'}>
                {healthError ? t('restUnavailable') : t('restHealthy')}
              </Tag>
              {health ? (
                <Tag color="blue">{`${t('status')}: ${health.simulation_status}`}</Tag>
              ) : null}
              {reviewResult ? (
                <Tag color={reviewResult.valid ? 'success' : 'error'}>
                  {reviewResult.valid ? t('ok') : t('blocked')}
                </Tag>
              ) : null}
              <Select
                value={locale}
                style={{ width: 132 }}
                onChange={setLocale}
                options={[
                  { value: 'zh-CN', label: t('localeZh') },
                  { value: 'en-US', label: t('localeEn') },
                ]}
              />
              <Button
                type="primary"
                icon={<SafetyCertificateOutlined />}
                loading={reviewing}
                onClick={async () => {
                  setReviewOpen(true);
                  await runReview().catch(() => {});
                }}
              >
                {t('review')}
              </Button>
            </Space>
          </Header>

          <Content className="workbench-content">
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/catalog" element={<ClassCatalogPage />} />
              <Route path="/studio" element={<WorkflowStudioPage />} />
              <Route path="/console" element={<RunConsolePage />} />
              <Route path="/runs" element={<TrajectoriesLogsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Content>

          <Footer className="workbench-footer">
            {t('footer')} ©{new Date().getFullYear()}
          </Footer>
        </Layout>
      </Layout>

      <ReviewDrawer
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        reviewResult={reviewResult}
        reviewGraph={reviewGraph}
        reviewing={reviewing}
        error={reviewError}
      />
    </>
  );
}

function App() {
  return (
    <I18nProvider>
      <WorkbenchProvider>
        <AppShell />
      </WorkbenchProvider>
    </I18nProvider>
  );
}

export default App;
