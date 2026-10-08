// Shared helpers and data-layer hooks for the run console page.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { configApi, createWorkbenchSocket, runApi, systemApi } from '../../services/workbenchApi';

export const ACTIVE_RUN_STATUSES = ['RUNNING', 'STARTING', 'PAUSED'];
export const EMPTY_RUN_GRAPH = { critical_path: [], warnings: [] };

export function formatTimestamp(value) {
  if (!value) {
    return '-';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString();
}

export function runStatusColor(status) {
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

export function isSuccessfulTaskLog(log) {
  return Boolean(
    log?.event &&
      String(log.event).toLowerCase().endsWith('task_completed') &&
      (log.status === 'completed' || log.result?.status === 'completed')
  );
}

function pickMarker(agentList, previous) {
  if (previous && agentList?.some((agent) => agent.id === previous.id)) {
    return agentList.find((agent) => agent.id === previous.id) || null;
  }
  return agentList?.[0] || null;
}

export function useConsoleRuns() {
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState(null);

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

  return { runs, setRuns, selectedRunId, setSelectedRunId, refreshRuns };
}

export function useConsoleRunDetail() {
  const [status, setStatus] = useState(null);
  const [spatial, setSpatial] = useState(null);
  const [logs, setLogs] = useState([]);
  const [graph, setGraph] = useState(null);
  const [graphError, setGraphError] = useState('');
  const detailRun = useRef(null);
  const [selectedMarker, setSelectedMarker] = useState(null);

  const clearRunData = useCallback(() => {
    setStatus(null);
    setSpatial(null);
    setLogs([]);
    setGraph(null); setGraphError(''); detailRun.current = null;
    setSelectedMarker(null);
  }, []);

  const refreshRunData = useCallback(async (runId) => {
    if (!runId) {
      clearRunData();
      return;
    }
    if (detailRun.current !== runId) { setGraph(null); setGraphError(''); detailRun.current = runId; }
    const [nextStatus, nextLogs, nextSpatial] = await Promise.all([
      runApi.getStatus(runId),
      runApi.getLogs(runId),
      runApi.getSpatial(runId),
    ]);
    setStatus(nextStatus);
    setLogs(nextLogs || []);
    setSpatial(nextSpatial);
    setSelectedMarker((previous) => pickMarker(nextSpatial?.agents, previous));

    if (nextStatus?.config_id) {
      try {
        const nextGraph = await configApi.getGraph(nextStatus.config_id);
        if (!nextGraph) throw Error('Graph service returned no graph');
        setGraph(nextGraph); setGraphError('');
      } catch (error) {
        setGraphError(error?.message || 'Graph service unavailable');
      }
    }
  }, [clearRunData]);

  return {
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
  };
}

export function useConsoleHealth() {
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');

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
    refreshHealth().catch(() => {});
    const timer = window.setInterval(() => {
      refreshHealth().catch(() => {});
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  return { health, healthError, refreshHealth };
}

export function useConsoleSocket({ selectedRunId, refreshHealth, onStatus, onLog, onSpatial }) {
  const [socketState, setSocketState] = useState('DISCONNECTED');
  const handlersRef = useRef({ onStatus, onLog, onSpatial });
  handlersRef.current = { onStatus, onLog, onSpatial };

  useEffect(() => {
    const socketClient = createWorkbenchSocket({
      onOpen: () => setSocketState('CONNECTED'),
      onClose: () => setSocketState('DISCONNECTED'),
      onStatus: (event) => {
        if (selectedRunId && event.run_id && event.run_id !== selectedRunId) {
          return;
        }
        handlersRef.current.onStatus(event);
        refreshHealth().catch(() => {});
      },
      onLog: (event) => {
        if (selectedRunId && event.run_id && event.run_id !== selectedRunId) {
          return;
        }
        handlersRef.current.onLog(event);
      },
      onSpatial: (event) => {
        if (selectedRunId && event.run_id && event.run_id !== selectedRunId) {
          return;
        }
        handlersRef.current.onSpatial(event);
      },
    });
    return () => socketClient.close();
  }, [refreshHealth, selectedRunId]);

  return socketState;
}

export function useRunSelection(runs, selectedRunId) {
  return useMemo(() => {
    const selectedRun = runs.find((run) => run.run_id === selectedRunId) || null;
    const activeRun =
      runs.find((run) =>
        ACTIVE_RUN_STATUSES.includes(String(run.status || '').toUpperCase())
      ) || null;
    const isActiveRun = Boolean(selectedRunId && activeRun && selectedRunId === activeRun.run_id);
    return {
      selectedRun,
      activeRun,
      isActiveRun,
      canDeleteSelectedRun: Boolean(selectedRunId) && !isActiveRun,
    };
  }, [runs, selectedRunId]);
}
