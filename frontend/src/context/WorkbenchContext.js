import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { configApi, MOCK_CONFIG_ID } from '../services/workbenchApi';

const WorkbenchContext = createContext(null);

export function WorkbenchProvider({ children }) {
  const [draftConfig, setDraftConfig] = useState(null);
  const [reviewResult, setReviewResult] = useState(null);
  const [reviewGraph, setReviewGraph] = useState(null);
  const [loadingDraft, setLoadingDraft] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState('');
  const draftRef = useRef(null);

  useEffect(() => {
    draftRef.current = draftConfig;
  }, [draftConfig]);

  const refreshDraft = useCallback(async () => {
    const config = await configApi.getConfig(MOCK_CONFIG_ID);
    setDraftConfig(config);
    return config;
  }, []);

  const saveDraft = useCallback(async (nextDraft) => {
    const saved = await configApi.saveConfig(MOCK_CONFIG_ID, nextDraft);
    setDraftConfig(saved);
    draftRef.current = saved;
    return saved;
  }, []);

  const runReview = useCallback(async (nextDraft = null) => {
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
  }, []);

  useEffect(() => {
    let active = true;
    const loadDraft = async () => {
      setLoadingDraft(true);
      setError('');
      try {
        const config = await refreshDraft();
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
  }, [refreshDraft]);

  const value = useMemo(
    () => ({
      draftConfig,
      setDraftConfig,
      loadingDraft,
      reviewResult,
      reviewGraph,
      reviewError: error,
      reviewing,
      refreshDraft,
      saveDraft,
      runReview,
    }),
    [draftConfig, error, loadingDraft, refreshDraft, reviewGraph, reviewResult, reviewing, runReview, saveDraft]
  );

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench() {
  return useContext(WorkbenchContext);
}
