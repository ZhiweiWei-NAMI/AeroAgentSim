import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { configApi, MOCK_CONFIG_ID, systemApi } from '../services/workbenchApi';

const WorkbenchContext = createContext(null);

export function WorkbenchProvider({ children }) {
  const [draftConfig, setDraftConfig] = useState(null);
  const [reviewResult, setReviewResult] = useState(null);
  const [reviewGraph, setReviewGraph] = useState(null);
  const [loadingDraft, setLoadingDraft] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState('');
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');
  const draftRef = useRef(null);

  useEffect(() => {
    draftRef.current = draftConfig;
  }, [draftConfig]);

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

  const authoritativeActionsEnabled = !healthError && Boolean(health?.backend_available);
  const displayOnlyFallbackMode = Boolean(healthError) || health?.backend_available === false;

  const createOfflineModeError = useCallback(() => {
    const offlineError = new Error('Backend-authoritative actions are unavailable in offline display mode.');
    offlineError.name = 'WorkbenchOfflineModeError';
    offlineError.code = 'OFFLINE_DISPLAY_MODE';
    return offlineError;
  }, []);

  const refreshDraft = useCallback(async () => {
    const config = await configApi.getConfig(MOCK_CONFIG_ID);
    setDraftConfig(config);
    return config;
  }, []);

  const saveDraft = useCallback(async (nextDraft) => {
    if (!authoritativeActionsEnabled) {
      throw createOfflineModeError();
    }
    const saved = await configApi.saveConfig(MOCK_CONFIG_ID, nextDraft);
    setDraftConfig(saved);
    draftRef.current = saved;
    return saved;
  }, [authoritativeActionsEnabled, createOfflineModeError]);

  const runReview = useCallback(async (nextDraft = null) => {
    if (!authoritativeActionsEnabled) {
      throw createOfflineModeError();
    }
    const targetDraft = nextDraft || draftRef.current;
    if (!targetDraft) {
      return null;
    }
    setReviewing(true);
    setError('');
    try {
      const [validation, graph, preflight] = await Promise.all([
        configApi.validate(targetDraft.config_id || MOCK_CONFIG_ID, targetDraft),
        configApi.getGraph(targetDraft.config_id || MOCK_CONFIG_ID, targetDraft),
        configApi.preflight(targetDraft.config_id || MOCK_CONFIG_ID, targetDraft),
      ]);

      const runtimeIssues = [
        ...(preflight.errors || []).map((message) => ({
          level: 'error',
          category: 'runtime_preflight',
          message,
        })),
        ...(preflight.warnings || []).map((message) => ({
          level: 'warning',
          category: 'runtime_preflight',
          message,
        })),
      ];

      const combined = {
        ...validation,
        valid: Boolean(validation.valid) && Boolean(preflight.is_ready),
        issues: [...(validation.issues || []), ...runtimeIssues],
        blockers: [...(validation.blockers || []), ...(preflight.errors || [])],
        graph_warnings: [...(validation.graph_warnings || []), ...(graph?.warnings || [])],
        preflight_ready: Boolean(preflight.is_ready),
        preflight_checks: preflight.checks || [],
        preflight_errors: preflight.errors || [],
        preflight_warnings: preflight.warnings || [],
      };

      setReviewResult(combined);
      setReviewGraph(graph);
      return { validation: combined, graph, preflight };
    } catch (reviewError) {
      setError(reviewError?.message || 'Failed to review current draft');
      throw reviewError;
    } finally {
      setReviewing(false);
    }
  }, [authoritativeActionsEnabled, createOfflineModeError]);

  useEffect(() => {
    let active = true;
    const loadDraft = async () => {
      setLoadingDraft(true);
      setError('');
      try {
        const [config] = await Promise.all([
          refreshDraft(),
          refreshHealth().catch(() => null),
        ]);
        if (active) {
          setDraftConfig(config);
        }
      } catch (loadError) {
        if (active) {
          setError(loadError?.message || 'Failed to load draft config');
        }
      } finally {
        if (active) {
          setLoadingDraft(false);
        }
      }
    };
    loadDraft();
    return () => {
      active = false;
    };
  }, [refreshDraft, refreshHealth]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshHealth().catch(() => {});
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  const value = useMemo(
    () => ({
      authoritativeActionsEnabled,
      backendAvailable: authoritativeActionsEnabled,
      draftConfig,
      displayOnlyFallbackMode,
      health,
      healthError,
      setDraftConfig,
      loadingDraft,
      reviewResult,
      reviewGraph,
      reviewError: error,
      reviewing,
      refreshDraft,
      refreshHealth,
      saveDraft,
      runReview,
      createOfflineModeError,
    }),
    [
      authoritativeActionsEnabled,
      createOfflineModeError,
      displayOnlyFallbackMode,
      draftConfig,
      error,
      health,
      healthError,
      loadingDraft,
      refreshDraft,
      refreshHealth,
      reviewGraph,
      reviewResult,
      reviewing,
      runReview,
      saveDraft,
    ]
  );

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench() {
  return useContext(WorkbenchContext);
}
