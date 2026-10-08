import React, { useCallback, useEffect, useState } from 'react';
import { message, Modal } from 'antd';

import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import { runApi, systemApi } from '../services/workbenchApi';

import {
  useConsoleHealth,
  useConsoleRunDetail,
  useConsoleRuns,
  useConsoleSocket,
  useRunSelection,
} from './console-parts/consoleModel';
import { renderAlerts, renderPageHead, renderRunToolbar } from './console-parts/consoleHeader';
import {
  renderCriticalPathPanel,
  renderLogsPanel,
  renderMarkerPanel,
  renderRegistryRefsPanel,
  renderSpatialPanel,
  renderStatusPanel,
} from './console-parts/consolePanels';

function RunConsolePage() {
  const { t } = useI18n();
  const { authoritativeActionsEnabled, draftConfig, saveDraft } = useWorkbench();

  const [loadingAction, setLoadingAction] = useState(false);
  const [error, setError] = useState('');
  const [startupFailure, setStartupFailure] = useState(null);

  const { runs, selectedRunId, setSelectedRunId, refreshRuns } = useConsoleRuns();
  const {
    status,
    setStatus,
    spatial,
    setSpatial,
    logs,
    setLogs,
    graph,
    graphError,
    selectedMarker,
    setSelectedMarker,
    refreshRunData,
  } = useConsoleRunDetail();
  const { health, healthError, refreshHealth } = useConsoleHealth();

  const { selectedRun, activeRun, isActiveRun, canDeleteSelectedRun } = useRunSelection(
    runs,
    selectedRunId
  );
  const selectedRunStatus = String(status?.status || selectedRun?.status || '').toUpperCase();
  const restAvailable = !healthError && Boolean(health?.backend_available);

  const refreshRunDataFor = useCallback(
    async (runId) => {
      try {
        await refreshRunData(runId);
      } catch (loadError) {
        setError(loadError?.message || 'Failed to load run data');
      }
    },
    [refreshRunData]
  );

  useEffect(() => {
    refreshRunDataFor(selectedRunId);
  }, [refreshRunDataFor, selectedRunId]);

  useEffect(() => {
    refreshRuns().catch((loadError) => {
      setError(loadError?.message || 'Failed to load runs');
    });
  }, [refreshRuns]);

  const socketState = useConsoleSocket({
    selectedRunId,
    refreshHealth,
    onStatus: (event) => setStatus((previous) => ({ ...(previous || {}), ...event })),
    onLog: (event) => setLogs((previous) => [event, ...previous].slice(0, 200)),
    onSpatial: (event) => {
      setSpatial(event);
      setSelectedMarker((previous) => {
        if (previous && event?.agents?.some((agent) => agent.id === previous.id)) {
          return event.agents.find((agent) => agent.id === previous.id) || null;
        }
        return event?.agents?.[0] || null;
      });
    },
  });

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
        nextFailure.runId ||
        nextRuns.find((item) => String(item.status || '').toUpperCase() === 'ERROR')?.run_id;
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

  const deleteRunTooltip = !selectedRunId
    ? ''
    : canDeleteSelectedRun
      ? t('deleteRunHint')
      : t('cannotDeleteActiveRun');

  return (
    <div className="workbench-page">
      {renderPageHead({
        t,
        restAvailable,
        socketState,
        health,
        loadingAction,
        authoritativeActionsEnabled,
        onRefreshRuns: () => refreshRuns().catch(() => {}),
        onResetRuntime: () => withRunAction(() => systemApi.resetRuntime()),
        onStartRun: startRun,
      })}

      {renderAlerts({ t, healthError, error, startupFailure })}

      <div className="studio-grid">
        {renderSpatialPanel({
          t,
          toolbar: renderRunToolbar({
            t,
            runs,
            activeRun,
            selectedRunId,
            onSelectRun: setSelectedRunId,
            isActiveRun,
            selectedRunStatus,
            restAvailable,
            loadingAction,
            onPauseRun: () => withRunAction(() => runApi.pauseRun(selectedRunId)),
            onResumeRun: () => withRunAction(() => runApi.resumeRun(selectedRunId)),
            onResetRun: () => withRunAction(() => runApi.resetRun(selectedRunId)),
            deleteRunTooltip,
            canDeleteSelectedRun,
            onDeleteRun: deleteRun,
          }),
          selectedRun,
          spatial,
          selectedRunId,
          selectedMarker,
          onSelectMarker: setSelectedMarker,
        })}

        <div className="panel-stack">
          {renderStatusPanel({ t, selectedRun, status, spatial })}
          {renderCriticalPathPanel({ t, graph, graphError })}
          {renderRegistryRefsPanel({ t, selectedRun })}
        </div>

        <div className="panel-stack">
          {renderMarkerPanel({ t, selectedMarker })}
          {renderLogsPanel({ t, logs })}
        </div>
      </div>
    </div>
  );
}

export default RunConsolePage;
