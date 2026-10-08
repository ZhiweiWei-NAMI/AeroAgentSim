// Header bar, alerts, and run-control toolbar for the run console page.
import React from 'react';
import { Alert, Button, Select, Space, Tag, Tooltip, Typography } from 'antd';
import {
  DeleteOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';

import { runStatusColor } from './consoleModel';

const { Title } = Typography;

export function renderPageHead({
  t,
  restAvailable,
  socketState,
  health,
  loadingAction,
  authoritativeActionsEnabled,
  onRefreshRuns,
  onResetRuntime,
  onStartRun,
}) {
  return (
    <div className="workbench-page-head">
      <Title level={4}>{t('pageConsole')}</Title>
      <Space wrap>
        <Tag color={restAvailable ? 'green' : 'error'}>
          {restAvailable ? t('restHealthy') : t('restUnavailable')}
        </Tag>
        <Tag color={socketState === 'CONNECTED' ? 'green' : 'default'}>{`WS ${socketState}`}</Tag>
        {health ? <Tag color="blue">{`${t('status')}: ${health.simulation_status}`}</Tag> : null}
        <Button icon={<ReloadOutlined />} onClick={onRefreshRuns}>
          {t('refreshRuns')}
        </Button>
        <Button
          icon={<StopOutlined />}
          loading={loadingAction}
          disabled={!restAvailable}
          onClick={onResetRuntime}
        >
          {t('resetRuntime')}
        </Button>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={loadingAction}
          disabled={!restAvailable || !authoritativeActionsEnabled}
          onClick={onStartRun}
        >
          {t('startSimulation')}
        </Button>
      </Space>
    </div>
  );
}

export function renderAlerts({ t, healthError, error, startupFailure }) {
  return (
    <>
      {healthError ? (
        <Alert
          type="error"
          showIcon
          message={t('backendUnavailable')}
          description={healthError}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
      {startupFailure ? (
        <Alert
          type="error"
          showIcon
          message={t('startupFailure')}
          description={
            <div>
              <div>{startupFailure.message}</div>
              {startupFailure.runId ? <div>{`${t('runId')}: ${startupFailure.runId}`}</div> : null}
              {(startupFailure.errors || []).length ? (
                <div>{startupFailure.errors.join(' | ')}</div>
              ) : null}
            </div>
          }
          style={{ marginBottom: 12 }}
        />
      ) : null}
    </>
  );
}

export function renderRunToolbar({
  t,
  runs,
  activeRun,
  selectedRunId,
  onSelectRun,
  isActiveRun,
  selectedRunStatus,
  restAvailable,
  loadingAction,
  onPauseRun,
  onResumeRun,
  onResetRun,
  deleteRunTooltip,
  canDeleteSelectedRun,
  onDeleteRun,
}) {
  return (
    <Space wrap>
      <Select
        value={selectedRunId}
        placeholder={t('selectRun')}
        style={{ width: 320 }}
        options={runs.map((run) => {
          const isActive = activeRun && run.run_id === activeRun.run_id;
          return {
            value: run.run_id,
            label: (
              <span>
                {isActive ? (
                  <Tag color="green" style={{ marginRight: 4 }}>
                    {t('activeRunLabel')}
                  </Tag>
                ) : null}
                <span style={{ opacity: isActive ? 1 : 0.6 }}>{run.run_id}</span>{' '}
                <Tag color={runStatusColor(run.status)} style={{ marginLeft: 4 }}>
                  {run.status}
                </Tag>
              </span>
            ),
          };
        })}
        onChange={onSelectRun}
      />
      <Tooltip title={!isActiveRun || selectedRunStatus !== 'RUNNING' ? t('onlyActiveRunHint') : ''}>
        <Button
          icon={<PauseCircleOutlined />}
          loading={loadingAction}
          disabled={!restAvailable || !isActiveRun || selectedRunStatus !== 'RUNNING'}
          onClick={onPauseRun}
        >
          {t('pause')}
        </Button>
      </Tooltip>
      <Tooltip title={!isActiveRun || selectedRunStatus !== 'PAUSED' ? t('onlyActiveRunHint') : ''}>
        <Button
          icon={<PlayCircleOutlined />}
          loading={loadingAction}
          disabled={!restAvailable || !isActiveRun || selectedRunStatus !== 'PAUSED'}
          onClick={onResumeRun}
        >
          {t('resume')}
        </Button>
      </Tooltip>
      <Tooltip title={!isActiveRun ? t('onlyActiveRunHint') : ''}>
        <Button
          icon={<StopOutlined />}
          loading={loadingAction}
          disabled={!restAvailable || !isActiveRun}
          onClick={onResetRun}
        >
          {t('stopAndReset')}
        </Button>
      </Tooltip>
      <Tooltip title={deleteRunTooltip}>
        <Button
          danger
          icon={<DeleteOutlined />}
          loading={loadingAction}
          disabled={!restAvailable || !selectedRunId || !canDeleteSelectedRun}
          onClick={onDeleteRun}
        >
          {t('deleteRun')}
        </Button>
      </Tooltip>
    </Space>
  );
}
