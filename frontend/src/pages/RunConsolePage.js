import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  DeleteOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';

import Map2D from '../components/workbench/Map2D';
import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import {
  configApi,
  createWorkbenchSocket,
  runApi,
  systemApi,
} from '../services/workbenchApi';

const { Text, Title } = Typography;
const ACTIVE_RUN_STATUSES = ['RUNNING', 'STARTING', 'PAUSED'];

function formatTimestamp(value) {
  if (!value) {
    return '-';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString();
}

function runStatusColor(status) {
  const normalized = String(status || '').toUpperCase();
  if (normalized === 'RUNNING' || normalized === 'STARTING') {
    return 'processing';
  }
  if (normalized === 'PAUSED') {
    return 'warning';
  }
  if (normalized === 'ERROR') {
    return 'error';
  }
  return 'default';
}

function isSuccessfulTaskLog(log) {
  return Boolean(
    log?.event &&
      String(log.event).toLowerCase().endsWith('task_completed') &&
      (log.status === 'completed' || log.result?.status === 'completed')
  );
}

function RunConsolePage() {
  const { t } = useI18n();
  const { authoritativeActionsEnabled, draftConfig, saveDraft } = useWorkbench();
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [status, setStatus] = useState(null);
  const [spatial, setSpatial] = useState(null);
  const [logs, setLogs] = useState([]);
  const [graph, setGraph] = useState({ critical_path: [], warnings: [] });
  const [socketState, setSocketState] = useState('DISCONNECTED');
  const [health, setHealth] = useState(null);
  const [loadingAction, setLoadingAction] = useState(false);
  const [error, setError] = useState('');
  const [healthError, setHealthError] = useState('');
  const [startupFailure, setStartupFailure] = useState(null);
  const [selectedMarker, setSelectedMarker] = useState(null);

  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) || null,
    [runs, selectedRunId]
  );

  const activeRun = useMemo(
    () => runs.find((run) => ACTIVE_RUN_STATUSES.includes(String(run.status || '').toUpperCase())) || null,
    [runs]
  );

  const isActiveRun = useMemo(
    () => selectedRunId && activeRun && selectedRunId === activeRun.run_id,
    [selectedRunId, activeRun]
  );

  const selectedRunStatus = useMemo(
    () => String(status?.status || selectedRun?.status || '').toUpperCase(),
    [status, selectedRun]
  );

  const canDeleteSelectedRun = useMemo(() => {
    if (!selectedRunId) {
      return false;
    }
    return !(activeRun && selectedRunId === activeRun.run_id);
  }, [activeRun, selectedRunId]);

  const deleteRunTooltip = useMemo(() => {
    if (!selectedRunId) {
      return '';
    }
    return canDeleteSelectedRun ? t('deleteRunHint') : t('cannotDeleteActiveRun');
  }, [canDeleteSelectedRun, selectedRunId, t]);

  const restAvailable = !healthError && Boolean(health?.backend_available);

  const refreshRuns = useCallback(async () => {
    const nextRuns = await runApi.listRuns();
    const safeRuns = Array.isArray(nextRuns) ? nextRuns : [];
    setRuns(safeRuns);
    setSelectedRunId((previous) => {
      if (previous && safeRuns.some((run) => run.run_id === previous)) {
        return previous;
      }
      return safeRuns[0]?.run_id || null;
    });
    return safeRuns;
  }, []);

  const refreshRunData = useCallback(async (runId) => {
    if (!runId) {
      setStatus(null);
      setSpatial(null);
      setLogs([]);
      setGraph({ critical_path: [], warnings: [] });
      setSelectedMarker(null);
      return;
    }
    const [nextStatus, nextLogs, nextSpatial] = await Promise.all([
      runApi.getStatus(runId),
      runApi.getLogs(runId),
      runApi.getSpatial(runId),
    ]);
    setStatus(nextStatus);
    setLogs(nextLogs || []);
    setSpatial(nextSpatial);
    setSelectedMarker((previous) => {
      if (previous && nextSpatial?.agents?.some((agent) => agent.id === previous.id)) {
        return nextSpatial.agents.find((agent) => agent.id === previous.id) || null;
      }
      return nextSpatial?.agents?.[0] || null;
    });

    if (nextStatus?.config_id) {
      try {
        const nextGraph = await configApi.getGraph(nextStatus.config_id);
        setGraph(nextGraph || { critical_path: [], warnings: [] });
      } catch (_error) {
        setGraph({ critical_path: [], warnings: [] });
      }
    }
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      const nextHealth = await systemApi.getHealth();
      setHealth(nextHealth);
      setHealthError('');
      return nextHealth;
    } catch (nextError) {
      setHealth(null);
      setHealthError(nextError?.message || 'Backend unavailable');
      throw nextError;
    }
  }, []);

  useEffect(() => {
    refreshRuns().catch((loadError) => {
      setError(loadError?.message || 'Failed to load runs');
    });
    refreshHealth().catch(() => {});
  }, [refreshHealth, refreshRuns]);

  useEffect(() => {
    refreshRunData(selectedRunId).catch((loadError) => {
      setError(loadError?.message || 'Failed to load run data');
    });
  }, [refreshRunData, selectedRunId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshHealth().catch(() => {});
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  useEffect(() => {
    const socketClient = createWorkbenchSocket({
      onOpen: () => setSocketState('CONNECTED'),
      onClose: () => setSocketState('DISCONNECTED'),
      onStatus: (event) => {
        if (selectedRunId && event.run_id && event.run_id !== selectedRunId) {
          return;
        }
        setStatus((previous) => ({ ...(previous || {}), ...event }));
        refreshHealth().catch(() => {});
      },
      onLog: (event) => {
        if (selectedRunId && event.run_id && event.run_id !== selectedRunId) {
          return;
        }
        setLogs((previous) => [event, ...previous].slice(0, 200));
      },
      onSpatial: (event) => {
        if (selectedRunId && event.run_id && event.run_id !== selectedRunId) {
          return;
        }
        setSpatial(event);
        setSelectedMarker((previous) => {
          if (previous && event?.agents?.some((agent) => agent.id === previous.id)) {
            return event.agents.find((agent) => agent.id === previous.id) || null;
          }
          return event?.agents?.[0] || null;
        });
      },
    });
    return () => socketClient.close();
  }, [refreshHealth, selectedRunId]);

  const withRunAction = async (executor) => {
    setLoadingAction(true);
    setError('');
    try {
      const result = await executor();
      const safeRunId = result?.run_id || selectedRunId;
      await refreshRuns();
      if (safeRunId) {
        setSelectedRunId(safeRunId);
        await refreshRunData(safeRunId);
      }
      await refreshHealth().catch(() => {});
      return result;
    } catch (actionError) {
      setError(actionError?.message || 'Run control request failed');
      await refreshHealth().catch(() => {});
      return null;
    } finally {
      setLoadingAction(false);
    }
  };

  const doStartRun = async () => {
    if (!draftConfig) {
      return null;
    }
    setLoadingAction(true);
    setError('');
    setStartupFailure(null);
    try {
      const savedConfig = await saveDraft(draftConfig);
      const result = await runApi.startRun(savedConfig.config_id);
      const runId = result?.run_id || null;
      await refreshRuns();
      if (runId) {
        setSelectedRunId(runId);
        await refreshRunData(runId);
      }
      await refreshHealth().catch(() => {});
      return result;
    } catch (actionError) {
      setError(actionError?.message || 'Simulation startup failed');
      const nextFailure = {
        message: actionError?.message || 'Simulation startup failed',
        errors: actionError?.errors || [],
        checks: actionError?.checks || [],
        runId: actionError?.runId || null,
        recentLogs: actionError?.recentLogs || [],
      };
      setStartupFailure(nextFailure);
      const nextRuns = await refreshRuns().catch(() => []);
      const failedRunId =
        nextFailure.runId || nextRuns.find((item) => String(item.status || '').toUpperCase() === 'ERROR')?.run_id;
      if (failedRunId) {
        setSelectedRunId(failedRunId);
        await refreshRunData(failedRunId).catch(() => {});
      }
      if (nextFailure.recentLogs?.length) {
        setLogs(nextFailure.recentLogs);
      }
      await refreshHealth().catch(() => {});
      return null;
    } finally {
      setLoadingAction(false);
    }
  };

  const startRun = () => {
    if (activeRun) {
      Modal.confirm({
        title: t('confirmResetRunTitle'),
        content: t('confirmResetRun'),
        okText: t('startSimulation'),
        cancelText: t('pause'),
        onOk: doStartRun,
      });
      return;
    }
    doStartRun();
  };

  const deleteRun = () => {
    if (!selectedRunId || !canDeleteSelectedRun) {
      return;
    }
    Modal.confirm({
      title: t('confirmDeleteRunTitle'),
      content: t('confirmDeleteRun', { runId: selectedRunId }),
      okText: t('delete'),
      okButtonProps: { danger: true },
      onOk: async () => {
        setLoadingAction(true);
        setError('');
        try {
          await runApi.deleteRun(selectedRunId);
          message.success(t('runDeleted'));
          await refreshRuns();
        } catch (actionError) {
          setError(actionError?.message || 'Run delete request failed');
        } finally {
          setLoadingAction(false);
        }
      },
    });
  };

  return (
    <div className="workbench-page">
      <div className="workbench-page-head">
        <Title level={4}>{t('pageConsole')}</Title>
        <Space wrap>
          <Tag color={restAvailable ? 'green' : 'error'}>
            {restAvailable ? t('restHealthy') : t('restUnavailable')}
          </Tag>
          <Tag color={socketState === 'CONNECTED' ? 'green' : 'default'}>{`WS ${socketState}`}</Tag>
          {health ? <Tag color="blue">{`${t('status')}: ${health.simulation_status}`}</Tag> : null}
          <Button icon={<ReloadOutlined />} onClick={() => refreshRuns().catch(() => {})}>
            {t('refreshRuns')}
          </Button>
          <Button
            icon={<StopOutlined />}
            loading={loadingAction}
            disabled={!restAvailable}
            onClick={() => withRunAction(() => systemApi.resetRuntime())}
          >
            {t('resetRuntime')}
          </Button>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={loadingAction}
            disabled={!restAvailable || !authoritativeActionsEnabled}
            onClick={startRun}
          >
            {t('startSimulation')}
          </Button>
        </Space>
      </div>

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

      <div className="studio-grid">
        <section className="panel-section">
          <div className="section-title-row">
            <Title level={5}>{t('realtimeSpatial')}</Title>
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
                        {isActive ? <Tag color="green" style={{ marginRight: 4 }}>{t('activeRunLabel')}</Tag> : null}
                        <span style={{ opacity: isActive ? 1 : 0.6 }}>{run.run_id}</span>
                        {' '}
                        <Tag color={runStatusColor(run.status)} style={{ marginLeft: 4 }}>{run.status}</Tag>
                      </span>
                    ),
                  };
                })}
                onChange={setSelectedRunId}
              />
              <Tooltip title={!isActiveRun || selectedRunStatus !== 'RUNNING' ? t('onlyActiveRunHint') : ''}>
                <Button
                  icon={<PauseCircleOutlined />}
                  loading={loadingAction}
                  disabled={!restAvailable || !isActiveRun || selectedRunStatus !== 'RUNNING'}
                  onClick={() => withRunAction(() => runApi.pauseRun(selectedRunId))}
                >
                  {t('pause')}
                </Button>
              </Tooltip>
              <Tooltip title={!isActiveRun || selectedRunStatus !== 'PAUSED' ? t('onlyActiveRunHint') : ''}>
                <Button
                  icon={<PlayCircleOutlined />}
                  loading={loadingAction}
                  disabled={!restAvailable || !isActiveRun || selectedRunStatus !== 'PAUSED'}
                  onClick={() => withRunAction(() => runApi.resumeRun(selectedRunId))}
                >
                  {t('resume')}
                </Button>
              </Tooltip>
              <Tooltip title={!isActiveRun ? t('onlyActiveRunHint') : ''}>
                <Button
                  icon={<StopOutlined />}
                  loading={loadingAction}
                  disabled={!restAvailable || !isActiveRun}
                  onClick={() => withRunAction(() => runApi.resetRun(selectedRunId))}
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
                  onClick={deleteRun}
                >
                  {t('deleteRun')}
                </Button>
              </Tooltip>
            </Space>
          </div>
          <Alert
            type="info"
            showIcon
            message={t('realtimeSpatialHint')}
            style={{ marginBottom: 12 }}
          />
          {selectedRunId && spatial ? (
            <Map2D
              mode={spatial.coordinate_mode || selectedRun?.coordinate_mode || 'simulation_plane'}
              markers={spatial.agents || []}
              trajectories={[]}
              height={520}
              testId="run-console-map"
              selectedMarkerId={selectedMarker?.id}
              onSelectMarker={setSelectedMarker}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRunData')} />
          )}
        </section>

        <div className="panel-stack">
          <section className="panel-section">
            <Title level={5}>{t('runStatus')}</Title>
            {selectedRun ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label={t('runId')}>{selectedRun.run_id}</Descriptions.Item>
                <Descriptions.Item label={t('status')}>
                  <Tag color={runStatusColor(status?.status || selectedRun.status)}>
                    {status?.status || selectedRun.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('simulationTime')}>
                  {status?.simulation_time ?? selectedRun.simulation_time ?? 0}
                </Descriptions.Item>
                <Descriptions.Item label={t('configId')}>
                  {selectedRun.config_id || '-'}
                </Descriptions.Item>
                <Descriptions.Item label={t('coordinateMode')}>
                  {spatial?.coordinate_mode || selectedRun.coordinate_mode || 'simulation_plane'}
                </Descriptions.Item>
                <Descriptions.Item label={t('updated')}>
                  {formatTimestamp(status?.updated_at || selectedRun.updated_at)}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRun')} />
            )}
          </section>

          <section className="panel-section">
            <Title level={5}>{t('criticalPath')}</Title>
            {(graph.critical_path || []).length ? (
              <div className="inline-tags">
                {(graph.critical_path || []).map((item) => (
                  <Tag color="orange" key={item}>
                    {item}
                  </Tag>
                ))}
              </div>
            ) : (
              <Text type="secondary">{t('noData')}</Text>
            )}
          </section>

          <section className="panel-section">
            <Title level={5}>{t('registryReferences')}</Title>
            {(selectedRun?.registry_references || []).length ? (
              <div className="inline-tags">
                {(selectedRun.registry_references || []).map((item, index) => (
                  <Tag key={`${item.kind}_${item.definition_id}_${index}`}>
                    {`${item.kind}:${item.definition_id}@${item.version || 'latest'}`}
                  </Tag>
                ))}
              </div>
            ) : (
              <Text type="secondary">{t('noData')}</Text>
            )}
          </section>
        </div>

        <div className="panel-stack">
          <section className="panel-section map-sidecard">
            <Title level={5}>{t('liveMarkerDetail')}</Title>
            {selectedMarker ? (
              <>
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label="agent_id">{selectedMarker.id}</Descriptions.Item>
                  <Descriptions.Item label={t('classType')}>
                    {selectedMarker.type || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('status')}>
                    <Tag color={selectedMarker.status === 'error' ? 'error' : 'blue'}>
                      {selectedMarker.status || 'idle'}
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label={t('workflowType')}>
                    {selectedMarker.workflow_id || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('taskId')}>
                    {selectedMarker.task_name || selectedMarker.task_id || '-'}
                  </Descriptions.Item>
                </Descriptions>
                <div className="map-meta">
                  <Tag>{`x/lng: ${selectedMarker.position?.x ?? selectedMarker.position?.lng ?? 0}`}</Tag>
                  <Tag>{`y/lat: ${selectedMarker.position?.y ?? selectedMarker.position?.lat ?? 0}`}</Tag>
                  <Tag>{`z: ${selectedMarker.position?.z ?? 0}`}</Tag>
                </div>
                <Text className="field-help">{`${t('recentLog')}: ${selectedMarker.recent_log || '-'}`}</Text>
              </>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectMarker')} />
            )}
          </section>

          <section className="panel-section">
            <Title level={5}>{t('realtimeLogs')}</Title>
            <Table
              size="small"
              scroll={{ x: 'max-content' }}
              rowKey={(row, index) => row.id || `${row.timestamp}_${index}`}
              dataSource={logs}
              pagination={{ pageSize: 8 }}
              columns={[
                {
                  title: t('status'),
                  key: 'level',
                  width: 120,
                  render: (_value, row) => (
                    <Space direction="vertical" size={4}>
                      <Tag color={row.level === 'error' ? 'error' : row.level === 'warning' ? 'warning' : 'blue'}>
                        {row.level}
                      </Tag>
                      {isSuccessfulTaskLog(row) ? <Tag color="success">task ok</Tag> : null}
                    </Space>
                  ),
                },
                {
                  title: 'Event',
                  dataIndex: 'event',
                  width: 200,
                  render: (value) => value || '-',
                },
                { title: t('source'), dataIndex: 'source', width: 120 },
                {
                  title: t('taskId'),
                  key: 'task_meta',
                  width: 220,
                  render: (_value, row) => row.task_name || row.task_id || '-',
                },
                {
                  title: t('workflowType'),
                  dataIndex: 'workflow_id',
                  width: 180,
                  render: (value) => value || '-',
                },
                { title: t('description'), dataIndex: 'message' },
              ]}
            />
          </section>
        </div>
      </div>
    </div>
  );
}

export default RunConsolePage;
