import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Space, Typography } from 'antd';
import { SaveOutlined } from '@ant-design/icons';

import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import { MOCK_CONFIG_ID } from '../services/workbenchApi';

import { buildAgentInstance, buildWorkflowInstance, resolveDefinitionName } from './studio-parts/studioModel';
import {
  EMPTY_GRAPH,
  useStudioCatalog,
  useStudioSelection,
  useDefinitionFinders,
} from './studio-parts/studioData';
import {
  buildAgentColumns,
  buildWorkflowColumns,
} from './studio-parts/studioColumns';
import {
  renderAgentInspector,
  renderNodeInspector,
  renderWorkflowInspector,
} from './studio-parts/studioInspectors';
import {
  renderDraftConfigSection,
  renderEntitySection,
  renderGraphSection,
  renderGraphWarningsSection,
  renderInspectorSection,
  stampDraft,
} from './studio-parts/studioPageParts';

const { Title } = Typography;

function WorkflowStudioPage() {
  const { locale, t } = useI18n();
  const {
    authoritativeActionsEnabled,
    draftConfig,
    displayOnlyFallbackMode,
    setDraftConfig,
    saveDraft,
    runReview,
    reviewResult,
    reviewGraph,
  } = useWorkbench();

  const [graph, setGraph] = useState(EMPTY_GRAPH);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const {
    catalog,
    catalogLoading,
    catalogError,
    nextAgentType,
    setNextAgentType,
    nextWorkflowType,
    setNextWorkflowType,
  } = useStudioCatalog();

  const { graphError, selectedAgentId, setSelectedAgentId, selectedWorkflowId, setSelectedWorkflowId, selectedNodeId, setSelectedNodeId } =
    useStudioSelection(draftConfig, reviewGraph, setGraph);

  const { findAgentDefinition, findWorkflowDefinition } = useDefinitionFinders(catalog);

  const agentOptions = useMemo(
    () =>
      catalog.agents.map((definition) => ({
        value: definition.id,
        label: `${resolveDefinitionName(definition, locale)} [${definition.source}]`,
      })),
    [catalog.agents, locale]
  );

  const workflowOptions = useMemo(
    () =>
      catalog.workflows.map((definition) => ({
        value: definition.id,
        label: `${resolveDefinitionName(definition, locale)} [${definition.source}]`,
      })),
    [catalog.workflows, locale]
  );

  const selectedAgent = useMemo(
    () => (draftConfig?.agents || []).find((item) => item.id === selectedAgentId) || null,
    [draftConfig?.agents, selectedAgentId]
  );

  const selectedWorkflow = useMemo(
    () =>
      (draftConfig?.workflows || []).find((item) => item.id === selectedWorkflowId) || null,
    [draftConfig?.workflows, selectedWorkflowId]
  );

  const selectedNode = useMemo(
    () => (graph.nodes || []).find((node) => node.id === selectedNodeId) || null,
    [graph.nodes, selectedNodeId]
  );

  const selectedAgentDefinition = useMemo(
    () => findAgentDefinition(selectedAgent),
    [findAgentDefinition, selectedAgent]
  );

  const selectedWorkflowDefinition = useMemo(
    () => findWorkflowDefinition(selectedWorkflow),
    [findWorkflowDefinition, selectedWorkflow]
  );

  const showWorkflowInspector = selectedNodeId
    ? selectedNodeId.startsWith('workflow:')
    : Boolean(selectedWorkflow);
  const showAgentInspector = selectedNodeId
    ? selectedNodeId.startsWith('agent:')
    : !selectedWorkflow && Boolean(selectedAgent);
  const showNodeInspector =
    Boolean(selectedNodeId) &&
    !selectedNodeId.startsWith('agent:') &&
    !selectedNodeId.startsWith('workflow:');

  const updateDraft = (updater) => {
    setDraftConfig((previous) =>
      stampDraft(typeof updater === 'function' ? updater(previous) : updater)
    );
    setMessage('');
  };

  const updateAgentRow = (agentId, patch) => {
    updateDraft((currentDraft) => ({
      ...currentDraft,
      agents: (currentDraft?.agents || []).map((agent) =>
        agent.id === agentId ? { ...agent, ...patch } : agent
      ),
    }));
  };

  const updateWorkflowRow = (workflowId, patch) => {
    updateDraft((currentDraft) => ({
      ...currentDraft,
      workflows: (currentDraft?.workflows || []).map((workflow) =>
        workflow.id === workflowId ? { ...workflow, ...patch } : workflow
      ),
    }));
  };

  const addAgent = () => {
    const definition = catalog.agents.find((item) => item.id === nextAgentType) || catalog.agents[0];
    if (!definition) {
      return;
    }
    const nextId = `agent_${(draftConfig?.agents || []).length + 1}`;
    updateDraft((currentDraft) => ({
      ...currentDraft,
      agents: [
        ...(currentDraft?.agents || []),
        buildAgentInstance(definition, (currentDraft?.agents || []).length),
      ],
    }));
    setSelectedAgentId(nextId);
    setSelectedNodeId(`agent:${nextId}`);
  };

  const addWorkflow = () => {
    const definition =
      catalog.workflows.find((item) => item.id === nextWorkflowType) || catalog.workflows[0];
    if (!definition) {
      return;
    }
    const nextId = `workflow_${(draftConfig?.workflows || []).length + 1}`;
    updateDraft((currentDraft) => ({
      ...currentDraft,
      workflows: [
        ...(currentDraft?.workflows || []),
        buildWorkflowInstance(
          definition,
          currentDraft?.agents || [],
          (currentDraft?.workflows || []).length
        ),
      ],
    }));
    setSelectedWorkflowId(nextId);
    setSelectedNodeId(`workflow:${nextId}`);
  };

  const saveConfig = async () => {
    if (!draftConfig) {
      return;
    }
    setSaving(true);
    setError('');
    try {
      const saved = await saveDraft(draftConfig);
      await runReview(saved);
      setMessage(t('saveSuccess'));
    } catch (saveError) {
      setError(saveError?.message || 'Failed to save current draft');
    } finally {
      setSaving(false);
    }
  };

  const validateDraft = async () => {
    if (!draftConfig) {
      return;
    }
    setValidating(true);
    setError('');
    try {
      const result = await runReview(draftConfig);
      setGraph(result?.graph || EMPTY_GRAPH);
      setMessage(result?.validation?.valid ? t('reviewUpdated') : t('reviewFailedInline'));
    } catch (validationError) {
      setError(validationError?.message || 'Failed to validate current draft');
    } finally {
      setValidating(false);
    }
  };

  const agentColumns = buildAgentColumns({
    t,
    agentOptions,
    catalogAgents: catalog.agents,
    findAgentDefinition,
    updateAgentRow,
    updateDraft,
  });

  const workflowColumns = buildWorkflowColumns({
    t,
    workflowOptions,
    catalogWorkflows: catalog.workflows,
    draftConfig,
    findWorkflowDefinition,
    updateWorkflowRow,
    updateDraft,
  });

  if (!draftConfig) {
    return null;
  }

  return (
    <div className="workbench-page">
      <div className="workbench-page-head">
        <Title level={4}>{t('pageStudio')}</Title>
        <Space wrap>
          <Button loading={validating} disabled={!authoritativeActionsEnabled} onClick={validateDraft}>
            {t('validate')}
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={!authoritativeActionsEnabled}
            onClick={saveConfig}
          >
            {t('saveConfig')}
          </Button>
        </Space>
      </div>

      {error || catalogError || graphError ? <Alert type="error" showIcon message={error || catalogError || graphError} style={{ marginBottom: 12 }} /> : null}
      {message ? <Alert type="info" showIcon message={message} style={{ marginBottom: 12 }} /> : null}
      {displayOnlyFallbackMode ? (
        <Alert
          type="warning"
          showIcon
          message={t('authoritativeActionsDisabled')}
          description={t('graphPreviewOfflineHint')}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <div className="studio-grid">
        <div className="panel-stack">
          {renderDraftConfigSection({ t, draftConfig, reviewResult, updateDraft })}
          {renderEntitySection({
            t,
            title: t('studioAgents'),
            typeValue: nextAgentType,
            typeOptions: agentOptions,
            onTypeChange: setNextAgentType,
            addLabel: t('addAgent'),
            onAdd: addAgent,
            loading: catalogLoading,
            rowKeySelectedId: selectedAgentId,
            dataSource: draftConfig.agents || [],
            columns: agentColumns,
            rowPrefix: 'agent',
            setSelectedId: setSelectedAgentId,
            setSelectedNodeId,
          })}
          {renderEntitySection({
            t,
            title: t('studioWorkflows'),
            typeValue: nextWorkflowType,
            typeOptions: workflowOptions,
            onTypeChange: setNextWorkflowType,
            addLabel: t('addWorkflow'),
            onAdd: addWorkflow,
            loading: catalogLoading,
            rowKeySelectedId: selectedWorkflowId,
            dataSource: draftConfig.workflows || [],
            columns: workflowColumns,
            rowPrefix: 'workflow',
            setSelectedId: setSelectedWorkflowId,
            setSelectedNodeId,
          })}
        </div>

        <div className="panel-stack">
          {renderGraphSection({
            t,
            reviewResult,
            graph,
            selectedNodeId,
            onNodeSelect: (nodeId) => {
              setSelectedNodeId(nodeId);
              if (String(nodeId).startsWith('agent:')) {
                setSelectedAgentId(String(nodeId).replace('agent:', ''));
              }
              if (String(nodeId).startsWith('workflow:')) {
                setSelectedWorkflowId(String(nodeId).replace('workflow:', ''));
              }
            },
          })}
          {renderGraphWarningsSection({ t, graph })}
        </div>

        {renderInspectorSection({
          t,
          showAgentInspector,
          showWorkflowInspector,
          showNodeInspector,
          selectedNode,
          renderWorkflowInspector: () =>
            renderWorkflowInspector({
              t,
              selectedWorkflow,
              selectedWorkflowDefinition,
              workflowOptions,
              draftConfig,
              updateWorkflowRow,
              updateDraft,
              setSelectedWorkflowId,
              setSelectedNodeId,
            }),
          renderAgentInspector: () =>
            renderAgentInspector({
              t,
              selectedAgent,
              selectedAgentDefinition,
              agentOptions,
              catalogComponents: catalog.components,
              updateAgentRow,
              updateDraft,
              setSelectedAgentId,
              setSelectedNodeId,
            }),
          renderNodeInspector: () => renderNodeInspector({ t, selectedNode }),
          metadataJson: JSON.stringify(
            {
              config_id: draftConfig.config_id || MOCK_CONFIG_ID,
              registry_references: draftConfig.registry_references || [],
              review: reviewResult
                ? {
                    valid: reviewResult.valid,
                    issues: reviewResult.issues?.length || 0,
                  }
                : null,
            },
            null,
            2
          ),
        })}
      </div>
    </div>
  );
}

export default WorkflowStudioPage;
export { buildWorkflowInstance };
