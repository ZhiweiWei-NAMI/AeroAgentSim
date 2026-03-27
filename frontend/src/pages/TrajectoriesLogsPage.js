import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Input,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { DeleteOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';

import Map2D from '../components/workbench/Map2D';
import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import { runApi } from '../services/workbenchApi';

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

function matchesLogAgent(log, agentId) {
  if (agentId === 'all') {
    return true;
  }
  return [
    log?.source_id,
    log?.source,
    log?.details?.agent_id,
    log?.result?.agent_id,
    log?.message,
  ].some((value) => String(value || '').includes(agentId));
}

function TrajectoriesLogsPage() {
  const { t } = useI18n();
  const { authoritativeActionsEnabled, displayOnlyFallbackMode } = useWorkbench();
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [trajectoriesPayload, setTrajectoriesPayload] = useState({
    trajectories: [],
    coordinate_mode: 'simulation_plane',
  });
  const [logs, setLogs] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState('all');
  const [selectedLevel, setSelectedLevel] = useState('all');
  const [keyword, setKeyword] = useState('');
  const [selectedMarker, setSelectedMarker] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) || null,
    [runs, selectedRunId]
  );

  const activeRun = useMemo(
    () => runs.find((run) => ACTIVE_RUN_STATUSES.includes(String(run.status || '').toUpperCase())) || null,
    [runs]
  );

  const canDeleteSelectedRun = useMemo(() => {
    if (!selectedRunId) {
      return false;
    }
    if (!authoritativeActionsEnabled) {
      return false;
    }
    return !(activeRun && selectedRunId === activeRun.run_id);
  }, [activeRun, authoritativeActionsEnabled, selectedRunId]);

  const deleteRunTooltip = useMemo(() => {
    if (!selectedRunId) {
      return '';
    }
    if (!authoritativeActionsEnabled) {
      return t('authoritativeActionsDisabled');
    }
    return canDeleteSelectedRun ? t('deleteRunHint') : t('cannotDeleteActiveRun');
  }, [authoritativeActionsEnabled, canDeleteSelectedRun, selectedRunId, t]);

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

  const loadRunArtifacts = useCallback(async (runId) => {
    if (!runId) {
      setTrajectoriesPayload({ trajectories: [], coordinate_mode: 'simulation_plane' });
      setLogs([]);
      setSelectedMarker(null);
      return null;
    }

    const [nextStatus, nextTrajectories, nextLogs] = await Promise.all([
      runApi.getStatus(runId),
      runApi.getTrajectories(runId),
      runApi.getLogs(runId),
    ]);

    setRuns((previous) => {
      const existing = Array.isArray(previous) ? previous : [];
      let replaced = false;
      const nextRuns = existing.map((run) => {
        if (run.run_id !== nextStatus.run_id) {
          return run;
        }
        replaced = true;
        return { ...run, ...nextStatus };
      });
      return replaced ? nextRuns : [nextStatus, ...nextRuns];
    });

    const payload = nextTrajectories || { trajectories: [], coordinate_mode: 'simulation_plane' };
    setTrajectoriesPayload(payload);
    setLogs(nextLogs || []);
    const firstTrajectory = payload.trajectories?.[0];
    if (firstTrajectory?.points?.length) {
      setSelectedMarker({
        id: firstTrajectory.agent_id,
        type: firstTrajectory.agent_type,
        status: 'idle',
        position: firstTrajectory.points[firstTrajectory.points.length - 1],
      });
    } else {
      setSelectedMarker(null);
    }

    return { status: nextStatus, trajectories: payload, logs: nextLogs || [] };
  }, []);

  const refreshSelectedRun = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      await refreshRuns();
      await loadRunArtifacts(selectedRunId);
    } catch (loadError) {
      setError(loadError?.message || 'Failed to refresh trajectory or log data');
    } finally {
      setLoading(false);
    }
  }, [loadRunArtifacts, refreshRuns, selectedRunId]);

  useEffect(() => {
    refreshRuns().catch((loadError) => setError(loadError?.message || 'Failed to load runs'));
  }, [refreshRuns]);

  useEffect(() => {
    loadRunArtifacts(selectedRunId).catch((loadError) =>
      setError(loadError?.message || 'Failed to load trajectory or log data')
    );
  }, [loadRunArtifacts, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      return undefined;
    }

    const timer = window.setInterval(() => {
      loadRunArtifacts(selectedRunId).catch(() => {});
      refreshRuns().catch(() => {});
    }, 4000);

    return () => window.clearInterval(timer);
  }, [loadRunArtifacts, refreshRuns, selectedRunId]);

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
        setError('');
        try {
          await runApi.deleteRun(selectedRunId);
          message.success(t('runDeleted'));
          await refreshRuns();
        } catch (actionError) {
          setError(actionError?.message || 'Run delete request failed');
        }
      },
    });
  };

  const agentOptions = useMemo(
    () =>
      (trajectoriesPayload.trajectories || []).map((trajectory) => ({
        value: trajectory.agent_id,
        label: trajectory.agent_id,
      })),
    [trajectoriesPayload.trajectories]
  );

  const filteredTrajectories = useMemo(() => {
    if (selectedAgentId === 'all') {
      return trajectoriesPayload.trajectories || [];
    }
    return (trajectoriesPayload.trajectories || []).filter(
      (trajectory) => trajectory.agent_id === selectedAgentId
    );
  }, [selectedAgentId, trajectoriesPayload.trajectories]);

  const markers = useMemo(
    () =>
      filteredTrajectories.flatMap((trajectory) => {
        const points = trajectory.points || [];
        if (!points.length) {
          return [];
        }
        return [
          {
            id: `${trajectory.agent_id}.start`,
            type: `${trajectory.agent_type || 'agent'}:start`,
            status: 'idle',
            position: points[0],
          },
          {
            id: trajectory.agent_id,
            type: trajectory.agent_type || 'agent',
            status: ACTIVE_RUN_STATUSES.includes(String(selectedRun?.status || '').toUpperCase()) ? 'running' : 'idle',
            position: points[points.length - 1],
          },
        ];
      }),
    [filteredTrajectories, selectedRun]
  );

  const hasTrajectoryData = useMemo(
    () => filteredTrajectories.some((trajectory) => (trajectory.points || []).length > 0),
    [filteredTrajectories]
  );

  const filteredLogs = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return logs.filter((log) => {
      const matchesLevel = selectedLevel === 'all' || log.level === selectedLevel;
      const matchesAgent = matchesLogAgent(log, selectedAgentId);
      const matchesKeyword =
        !normalizedKeyword || JSON.stringify(log).toLowerCase().includes(normalizedKeyword);
      return matchesLevel && matchesAgent && matchesKeyword;
    });
  }, [keyword, logs, selectedAgentId, selectedLevel]);

  return (
    <div className="workbench-page">
      <div className="workbench-page-head">
        <Title level={4}>{t('pageRuns')}</Title>
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={refreshSelectedRun}>
            {t('refreshRuns')}
          </Button>
          <Select
            value={selectedRunId}
            placeholder={t('selectRun')}
            style={{ width: 300 }}
            options={runs.map((run) => ({
              value: run.run_id,
              label: `${run.run_id} (${run.status})`,
            }))}
            onChange={setSelectedRunId}
          />
          <Select
            value={selectedAgentId}
            style={{ width: 220 }}
            options={[{ value: 'all', label: t('allAgents') }, ...agentOptions]}
            onChange={setSelectedAgentId}
          />
          <Select
            value={selectedLevel}
            style={{ width: 160 }}
            options={[
              { value: 'all', label: t('allLevels') },
              { value: 'info', label: 'info' },
              { value: 'warning', label: 'warning' },
              { value: 'error', label: 'error' },
            ]}
            onChange={setSelectedLevel}
          />
          <Input
            allowClear
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder={t('searchLogs')}
            prefix={<SearchOutlined />}
            style={{ width: 220 }}
          />
          <Tooltip title={deleteRunTooltip}>
            <Button
              danger
              icon={<DeleteOutlined />}
              disabled={!selectedRunId || !canDeleteSelectedRun}
              onClick={deleteRun}
            >
              {t('deleteRun')}
            </Button>
          </Tooltip>
        </Space>
      </div>

      {displayOnlyFallbackMode ? (
        <Alert
          type="warning"
          showIcon
          message={t('offlineDisplayMode')}
          description={t('authoritativeActionsDisabled')}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}

      <div className="studio-grid">
        <section className="panel-section">
          <div className="section-title-row">
            <Title level={5}>{t('trajectoryReplay')}</Title>
            {selectedRun ? (
              <Tag>{`${selectedRun.run_id} / ${selectedRun.status}`}</Tag>
            ) : null}
          </div>
          <Alert
            type="info"
            showIcon
            message={t('trajectoryHint')}
            style={{ marginBottom: 12 }}
          />
          {selectedRunId ? (
            hasTrajectoryData || markers.length ? (
              <Map2D
                mode={trajectoriesPayload.coordinate_mode || 'simulation_plane'}
                markers={markers}
                trajectories={filteredTrajectories}
                height={520}
                testId="trajectories-map"
                selectedMarkerId={selectedMarker?.id}
                onSelectMarker={setSelectedMarker}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRunData')} />
            )
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRunHistory')} />
          )}
        </section>

        <div className="panel-stack">
          <section className="panel-section">
            <Title level={5}>{t('runStatus')}</Title>
            {selectedRun ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label={t('runId')}>{selectedRun.run_id}</Descriptions.Item>
                <Descriptions.Item label={t('status')}>
                  <Tag color={selectedRun.status === 'ERROR' ? 'error' : 'blue'}>
                    {selectedRun.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('simulationTime')}>
                  {selectedRun.simulation_time ?? 0}
                </Descriptions.Item>
                <Descriptions.Item label={t('updated')}>
                  {formatTimestamp(selectedRun.updated_at)}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRun')} />
            )}
          </section>

          <section className="panel-section map-sidecard">
            <Title level={5}>{t('liveMarkerDetail')}</Title>
            {selectedMarker ? (
              <>
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label="agent_id">{selectedMarker.id}</Descriptions.Item>
                  <Descriptions.Item label={t('classType')}>
                    {selectedMarker.type || '-'}
                  </Descriptions.Item>
                </Descriptions>
                <div className="map-meta">
                  <Tag>{`x/lng: ${selectedMarker.position?.x ?? selectedMarker.position?.lng ?? 0}`}</Tag>
                  <Tag>{`y/lat: ${selectedMarker.position?.y ?? selectedMarker.position?.lat ?? 0}`}</Tag>
                  <Tag>{`z: ${selectedMarker.position?.z ?? 0}`}</Tag>
                </div>
              </>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectMarker')} />
            )}
          </section>
        </div>

        <section className="panel-section">
          <Title level={5}>{t('runLogs')}</Title>
          <Text type="secondary" className="log-count">
            {t('showingLogs', { shown: filteredLogs.length, total: logs.length })}
          </Text>
          <Table
            size="small"
            rowKey={(row, index) => row.id || `${row.timestamp}_${index}`}
            dataSource={filteredLogs}
            pagination={{ pageSize: 10 }}
            columns={[
              {
                title: t('status'),
                dataIndex: 'level',
                width: 100,
                render: (value) => (
                  <Tag color={value === 'error' ? 'error' : value === 'warning' ? 'warning' : 'blue'}>
                    {value}
                  </Tag>
                ),
              },
              { title: 'Event', dataIndex: 'event', width: 200, render: (value) => value || '-' },
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
  );
}

export default TrajectoriesLogsPage;
