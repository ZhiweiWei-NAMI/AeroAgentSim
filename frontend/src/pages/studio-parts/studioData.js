// Data-layer hooks for the workflow studio page (catalog, selection, graph sync).
import { useEffect, useMemo, useState } from 'react';

import {
  configApi,
  MOCK_CONFIG_ID,
  normalizeAgentTypeToken,
  normalizeWorkflowTypeToken,
} from '../../services/workbenchApi';

export const EMPTY_GRAPH = {
  nodes: [],
  edges: [],
  trigger_conditions: [],
  task_bindings: [],
  critical_path: [],
  warnings: [],
};

export function useStudioCatalog() {
  const [catalog, setCatalog] = useState({
    agents: [],
    components: [],
    tasks: [],
    workflows: [],
  });
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [error, setError] = useState('');
  const [nextAgentType, setNextAgentType] = useState(null);
  const [nextWorkflowType, setNextWorkflowType] = useState(null);

  useEffect(() => {
    let active = true;
    const loadCatalog = async () => {
      setCatalogLoading(true);
      setError('');
      try {
        const [agents, components, tasks, workflows] = await Promise.all([
          catalogApi.getAgents('all'),
          catalogApi.getComponents(),
          catalogApi.getTasks('all'),
          catalogApi.getWorkflows('all'),
        ]);
        if (!active) {
          return;
        }
        setCatalog({ agents, components, tasks, workflows });
        if (!nextAgentType && agents[0]) {
          setNextAgentType(agents[0].id);
        }
        if (!nextWorkflowType && workflows[0]) {
          setNextWorkflowType(workflows[0].id);
        }
      } catch (loadError) {
        if (active) {
          setError(loadError?.message || 'Failed to load workbench catalog');
        }
      } finally {
        if (active) {
          setCatalogLoading(false);
        }
      }
    };
    loadCatalog();
    return () => {
      active = false;
    };
  }, []);

  return { catalog, catalogLoading, catalogError: error, setCatalogError: setError, nextAgentType, setNextAgentType, nextWorkflowType, setNextWorkflowType };
}

export function useStudioSelection(draftConfig, reviewGraph, setGraph) {
  const [graphError, setGraphError] = useState('');
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState('');

  useEffect(() => {
    if (!draftConfig?.agents?.length) {
      setSelectedAgentId('');
      return;
    }
    if (!selectedAgentId || !draftConfig.agents.some((item) => item.id === selectedAgentId)) {
      setSelectedAgentId(draftConfig.agents[0].id);
    }
  }, [draftConfig?.agents, selectedAgentId]);

  useEffect(() => {
    if (!draftConfig?.workflows?.length) {
      setSelectedWorkflowId('');
      return;
    }
    if (
      !selectedWorkflowId ||
      !draftConfig.workflows.some((item) => item.id === selectedWorkflowId)
    ) {
      setSelectedWorkflowId(draftConfig.workflows[0].id);
    }
  }, [draftConfig?.workflows, selectedWorkflowId]);

  useEffect(() => {
    if (reviewGraph?.nodes?.length || reviewGraph?.edges?.length) {
      setGraph(reviewGraph);
    }
  }, [reviewGraph, setGraph]);

  useEffect(() => {
    if (!draftConfig) {
      return undefined;
    }
    let active = true;
    const handle = window.setTimeout(async () => {
      try {
        const nextGraph = await configApi.getGraph(
          draftConfig.config_id || MOCK_CONFIG_ID,
          draftConfig
        );
        if (active) {
          if (!nextGraph) throw Error('Graph service returned no graph');
          setGraph(nextGraph);
          setGraphError('');
        }
      } catch (error) {
        if (active) {
          setGraphError(error?.message || 'Graph service unavailable');
        }
      }
    }, 180);

    return () => {
      active = false;
      window.clearTimeout(handle);
    };
  }, [draftConfig, reviewGraph, setGraph]);

  return {
    graphError,
    selectedAgentId,
    setSelectedAgentId,
    selectedWorkflowId,
    setSelectedWorkflowId,
    selectedNodeId,
    setSelectedNodeId,
  };
}

export function useDefinitionFinders(catalog) {
  const findAgentDefinition = useMemo(
    () => (agent) =>
      catalog.agents.find(
        (definition) =>
          normalizeAgentTypeToken(definition.id) ===
            normalizeAgentTypeToken(agent?.definition_ref?.definition_id || agent?.type) &&
          definition.source === (agent?.definition_ref?.source || agent?.source || 'builtin') &&
          (!agent?.definition_ref?.version ||
            definition.version === agent.definition_ref.version)
      ) ||
      catalog.agents.find(
        (definition) =>
          normalizeAgentTypeToken(definition.id) === normalizeAgentTypeToken(agent?.type)
      ),
    [catalog.agents]
  );

  const findWorkflowDefinition = useMemo(
    () => (workflow) =>
      catalog.workflows.find(
        (definition) =>
          normalizeWorkflowTypeToken(definition.id) ===
            normalizeWorkflowTypeToken(
              workflow?.definition_ref?.definition_id || workflow?.type
            ) &&
          definition.source ===
            (workflow?.definition_ref?.source || workflow?.source || 'builtin') &&
          (!workflow?.definition_ref?.version ||
            definition.version === workflow.definition_ref.version)
      ) ||
      catalog.workflows.find(
        (definition) =>
          normalizeWorkflowTypeToken(definition.id) ===
            normalizeWorkflowTypeToken(workflow?.type)
      ),
    [catalog.workflows]
  );

  return { findAgentDefinition, findWorkflowDefinition };
}
