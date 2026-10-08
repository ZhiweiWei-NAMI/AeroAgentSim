// Definition-table columns for the workflow studio (agents and workflows).
import { Button, Input, Select, Switch, Tag } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';

import {
  buildDefinitionRef,
  buildWorkflowProperties,
  filterCompatibleComponents,
  mergeTemplateDefaults,
  selectWorkflowOwnerId,
} from './studioModel';

export function buildAgentColumns({
  t,
  agentOptions,
  catalogAgents,
  findAgentDefinition,
  updateAgentRow,
  updateDraft,
}) {
  return [
    {
      title: t('name'),
      dataIndex: 'name',
      render: (value, row) => (
        <Input
          value={value}
          onChange={(event) => updateAgentRow(row.id, { name: event.target.value })}
        />
      ),
    },
    {
      title: t('agentType'),
      dataIndex: 'type',
      width: 240,
      render: (_value, row) => (
        <Select
          value={findAgentDefinition(row)?.id || row.definition_ref?.definition_id || row.type}
          style={{ width: '100%' }}
          options={agentOptions}
          onChange={(definitionId) => {
            const definition = catalogAgents.find((item) => item.id === definitionId);
            if (!definition) {
              return;
            }
            const definitionRef = buildDefinitionRef('agents', definition);
            updateAgentRow(row.id, {
              type: definition.id,
              source: definition.source || 'builtin',
              version: definition.version || null,
              definition_ref: definitionRef,
              registry_ref: definitionRef,
              components: filterCompatibleComponents(row.components, definition),
              properties: mergeTemplateDefaults(
                definition.state_templates || {},
                row.properties || {},
                definition.default_properties || {}
              ),
            });
          }}
        />
      ),
    },
    {
      title: t('components'),
      dataIndex: 'components',
      width: 100,
      render: (value) => <Tag>{(value || []).length}</Tag>,
    },
    {
      title: t('source'),
      dataIndex: 'source',
      width: 120,
      render: (value) => <Tag>{value || 'builtin'}</Tag>,
    },
    {
      title: t('version'),
      dataIndex: 'version',
      width: 120,
      render: (value) => value || 'builtin',
    },
    {
      title: t('action'),
      key: 'action',
      width: 72,
      render: (_value, row) => (
        <Button
          danger
          type="text"
          icon={<DeleteOutlined />}
          onClick={(event) => {
            event.stopPropagation();
            updateDraft((currentDraft) => ({
              ...currentDraft,
              agents: (currentDraft?.agents || []).filter((agent) => agent.id !== row.id),
            }));
          }}
        />
      ),
    },
  ];
}

export function buildWorkflowColumns({
  t,
  workflowOptions,
  catalogWorkflows,
  draftConfig,
  findWorkflowDefinition,
  updateWorkflowRow,
  updateDraft,
}) {
  return [
    {
      title: t('name'),
      dataIndex: 'name',
      render: (value, row) => (
        <Input
          value={value}
          onChange={(event) => updateWorkflowRow(row.id, { name: event.target.value })}
        />
      ),
    },
    {
      title: t('workflowType'),
      dataIndex: 'type',
      width: 240,
      render: (_value, row) => (
        <Select
          value={findWorkflowDefinition(row)?.id || row.definition_ref?.definition_id || row.type}
          style={{ width: '100%' }}
          options={workflowOptions}
          onChange={(definitionId) => {
            const definition = catalogWorkflows.find((item) => item.id === definitionId);
            if (!definition) {
              return;
            }
            const definitionRef = buildDefinitionRef('workflows', definition);
            const workflowIndex = (draftConfig?.workflows || []).findIndex(
              (workflow) => workflow.id === row.id
            );
            const agentId = selectWorkflowOwnerId(
              definition,
              draftConfig?.agents || [],
              row.agent_id
            );
            updateWorkflowRow(row.id, {
              type: definition.id,
              source: definition.source || 'builtin',
              version: definition.version || null,
              definition_ref: definitionRef,
              registry_ref: definitionRef,
              agent_id: agentId,
              properties: buildWorkflowProperties(
                definition,
                agentId,
                draftConfig?.agents || [],
                workflowIndex >= 0 ? workflowIndex : 0,
                row.properties || {}
              ),
            });
          }}
        />
      ),
    },
    {
      title: t('workflowAgent'),
      dataIndex: 'agent_id',
      width: 180,
      render: (value, row) => (
        <Select
          value={value}
          style={{ width: '100%' }}
          options={(draftConfig?.agents || []).map((agent) => ({
            value: agent.id,
            label: agent.name || agent.id,
          }))}
          onChange={(agentId) => updateWorkflowRow(row.id, { agent_id: agentId })}
        />
      ),
    },
    {
      title: t('enabled'),
      dataIndex: 'enabled',
      width: 100,
      render: (value, row) => (
        <Switch
          checked={value !== false}
          onChange={(checked) => updateWorkflowRow(row.id, { enabled: checked })}
        />
      ),
    },
    {
      title: t('action'),
      key: 'action',
      width: 72,
      render: (_value, row) => (
        <Button
          danger
          type="text"
          icon={<DeleteOutlined />}
          onClick={(event) => {
            event.stopPropagation();
            updateDraft((currentDraft) => ({
              ...currentDraft,
              workflows: (currentDraft?.workflows || []).filter(
                (workflow) => workflow.id !== row.id
              ),
            }));
          }}
        />
      ),
    },
  ];
}
