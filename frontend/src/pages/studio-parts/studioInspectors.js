// Inspector panels for the workflow studio (agent / workflow / graph node).
import React from 'react';
import {
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';

import TemplateEditor from './studioEditors';
import {
  buildDefinitionRef,
  buildWorkflowProperties,
  filterCompatibleComponents,
  mergeTemplateDefaults,
  selectWorkflowOwnerId,
} from './studioModel';

const { Text, Title } = Typography;

function renderInspectorShell(t, sections) {
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {sections}
    </Space>
  );
}

function renderEmptyInspector(t) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectEntityHint')} />;
}

export function renderAgentInspector({
  t,
  selectedAgent,
  selectedAgentDefinition,
  agentOptions,
  catalogComponents,
  updateAgentRow,
  updateDraft,
  setSelectedAgentId,
  setSelectedNodeId,
}) {
  if (!selectedAgent) {
    return renderEmptyInspector(t);
  }

  const applyAgentDefinition = (definition) => {
    if (!definition) {
      return;
    }
    const definitionRef = buildDefinitionRef('agents', definition);
    updateAgentRow(selectedAgent.id, {
      type: definition.id,
      source: definition.source || 'builtin',
      version: definition.version || null,
      definition_ref: definitionRef,
      registry_ref: definitionRef,
      components: filterCompatibleComponents(selectedAgent.components, definition),
      properties: mergeTemplateDefaults(
        definition.state_templates || {},
        selectedAgent.properties || {},
        definition.default_properties || {}
      ),
    });
  };

  const sections = [
    <div className="inspector-section" key="agent-main">
      <Title level={5}>{t('selectedAgent')}</Title>
      <div className="editor-grid">
        <div className="editor-field">
          <Text strong>ID</Text>
          <Input
            value={selectedAgent.id}
            onChange={(event) => {
              const nextId = event.target.value;
              updateDraft((currentDraft) => ({
                ...currentDraft,
                agents: (currentDraft?.agents || []).map((agent) =>
                  agent.id === selectedAgent.id ? { ...agent, id: nextId } : agent
                ),
                workflows: (currentDraft?.workflows || []).map((workflow) =>
                  workflow.agent_id === selectedAgent.id
                    ? { ...workflow, agent_id: nextId }
                    : workflow
                ),
              }));
              setSelectedAgentId(nextId);
              setSelectedNodeId(`agent:${nextId}`);
            }}
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('name')}</Text>
          <Input
            value={selectedAgent.name}
            onChange={(event) => updateAgentRow(selectedAgent.id, { name: event.target.value })}
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('agentType')}</Text>
          <Select
            value={
              selectedAgentDefinition?.id ||
              selectedAgent.definition_ref?.definition_id ||
              selectedAgent.type
            }
            options={agentOptions}
            onChange={applyAgentDefinition}
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('initialBattery')}</Text>
          <InputNumber
            value={Number(selectedAgent.initial_battery ?? 100)}
            style={{ width: '100%' }}
            onChange={(nextValue) =>
              updateAgentRow(selectedAgent.id, { initial_battery: nextValue ?? 0 })
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{`${t('initialPosition')} ${t('xAxis')}`}</Text>
          <InputNumber
            value={Number(selectedAgent.initial_position?.[0] ?? 0)}
            style={{ width: '100%' }}
            onChange={(nextValue) =>
              updateAgentRow(selectedAgent.id, {
                initial_position: [
                  nextValue ?? 0,
                  selectedAgent.initial_position?.[1] ?? 0,
                  selectedAgent.initial_position?.[2] ?? 0,
                ],
              })
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{`${t('initialPosition')} ${t('yAxis')}`}</Text>
          <InputNumber
            value={Number(selectedAgent.initial_position?.[1] ?? 0)}
            style={{ width: '100%' }}
            onChange={(nextValue) =>
              updateAgentRow(selectedAgent.id, {
                initial_position: [
                  selectedAgent.initial_position?.[0] ?? 0,
                  nextValue ?? 0,
                  selectedAgent.initial_position?.[2] ?? 0,
                ],
              })
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{`${t('initialPosition')} ${t('zAxis')}`}</Text>
          <InputNumber
            value={Number(selectedAgent.initial_position?.[2] ?? 0)}
            style={{ width: '100%' }}
            onChange={(nextValue) =>
              updateAgentRow(selectedAgent.id, {
                initial_position: [
                  selectedAgent.initial_position?.[0] ?? 0,
                  selectedAgent.initial_position?.[1] ?? 0,
                  nextValue ?? 0,
                ],
              })
            }
          />
        </div>
        <div className="editor-field editor-field-full">
          <Text strong>{t('components')}</Text>
          <Select
            mode="multiple"
            value={selectedAgent.components || []}
            options={(catalogComponents || []).map((component) => {
              const isCompatible = (selectedAgentDefinition?.compatible_components || []).includes(
                component.name
              );
              return {
                value: component.name,
                label: (
                  <span style={{ opacity: isCompatible ? 1 : 0.45 }}>
                    {component.name}
                    {!isCompatible ? ' (!)' : ''}
                  </span>
                ),
              };
            })}
            onChange={(nextValue) => updateAgentRow(selectedAgent.id, { components: nextValue })}
          />
          {(selectedAgent.components || []).some(
            (c) => !(selectedAgentDefinition?.compatible_components || []).includes(c)
          ) ? (
            <Text type="warning" className="field-help">
              {t('incompatibleComponentWarning')}
            </Text>
          ) : null}
        </div>
      </div>
    </div>,
    <div className="inspector-section" key="agent-binding">
      <Title level={5}>{t('definitionBinding')}</Title>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label={t('source')}>
          <Tag>{selectedAgent.source || 'builtin'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label={t('version')}>{selectedAgent.version || 'builtin'}</Descriptions.Item>
        <Descriptions.Item label={t('description')}>
          {selectedAgentDefinition?.description || '-'}
        </Descriptions.Item>
      </Descriptions>
    </div>,
    <div className="inspector-section" key="agent-properties">
      <Title level={5}>{t('instanceProperties')}</Title>
      <TemplateEditor
        templates={selectedAgentDefinition?.state_templates || {}}
        values={selectedAgent.properties || {}}
        onChange={(key, nextValue) =>
          updateAgentRow(selectedAgent.id, {
            properties: {
              ...(selectedAgent.properties || {}),
              [key]: nextValue,
            },
          })
        }
      />
    </div>,
  ];

  return renderInspectorShell(t, sections);
}

export function renderWorkflowInspector({
  t,
  selectedWorkflow,
  selectedWorkflowDefinition,
  workflowOptions,
  draftConfig,
  updateWorkflowRow,
  updateDraft,
  setSelectedWorkflowId,
  setSelectedNodeId,
}) {
  if (!selectedWorkflow) {
    return renderEmptyInspector(t);
  }

  const applyWorkflowDefinition = (definition) => {
    if (!definition) {
      return;
    }
    const definitionRef = buildDefinitionRef('workflows', definition);
    const workflowIndex = (draftConfig?.workflows || []).findIndex(
      (workflow) => workflow.id === selectedWorkflow.id
    );
    const agentId = selectWorkflowOwnerId(
      definition,
      draftConfig?.agents || [],
      selectedWorkflow.agent_id
    );
    updateWorkflowRow(selectedWorkflow.id, {
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
        selectedWorkflow.properties || {}
      ),
    });
  };

  const sections = [
    <div className="inspector-section" key="workflow-main">
      <Title level={5}>{t('selectedWorkflow')}</Title>
      <div className="editor-grid">
        <div className="editor-field">
          <Text strong>ID</Text>
          <Input
            value={selectedWorkflow.id}
            onChange={(event) => {
              const nextId = event.target.value;
              updateDraft((currentDraft) => ({
                ...currentDraft,
                workflows: (currentDraft?.workflows || []).map((workflow) =>
                  workflow.id === selectedWorkflow.id
                    ? { ...workflow, id: nextId }
                    : workflow
                ),
              }));
              setSelectedWorkflowId(nextId);
              setSelectedNodeId(`workflow:${nextId}`);
            }}
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('name')}</Text>
          <Input
            value={selectedWorkflow.name}
            onChange={(event) =>
              updateWorkflowRow(selectedWorkflow.id, { name: event.target.value })
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('workflowType')}</Text>
          <Select
            value={
              selectedWorkflowDefinition?.id ||
              selectedWorkflow.definition_ref?.definition_id ||
              selectedWorkflow.type
            }
            options={workflowOptions}
            onChange={applyWorkflowDefinition}
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('workflowAgent')}</Text>
          <Select
            value={selectedWorkflow.agent_id}
            options={(draftConfig?.agents || []).map((agent) => ({
              value: agent.id,
              label: agent.name || agent.id,
            }))}
            onChange={(nextValue) =>
              updateWorkflowRow(selectedWorkflow.id, { agent_id: nextValue })
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('enabled')}</Text>
          <Switch
            checked={selectedWorkflow.enabled !== false}
            onChange={(checked) =>
              updateWorkflowRow(selectedWorkflow.id, { enabled: checked })
            }
          />
        </div>
      </div>
    </div>,
    <div className="inspector-section" key="workflow-binding">
      <Title level={5}>{t('definitionBinding')}</Title>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label={t('source')}>
          <Tag>{selectedWorkflow.source || 'builtin'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label={t('version')}>
          {selectedWorkflow.version || 'builtin'}
        </Descriptions.Item>
        <Descriptions.Item label={t('startState')}>
          {selectedWorkflowDefinition?.start_state || '-'}
        </Descriptions.Item>
      </Descriptions>
    </div>,
    <div className="inspector-section" key="workflow-properties">
      <Title level={5}>{t('instanceProperties')}</Title>
      <TemplateEditor
        templates={selectedWorkflowDefinition?.property_templates || {}}
        values={selectedWorkflow.properties || {}}
        onChange={(key, nextValue) =>
          updateWorkflowRow(selectedWorkflow.id, {
            properties: {
              ...(selectedWorkflow.properties || {}),
              [key]: nextValue,
            },
          })
        }
      />
    </div>,
    <div className="inspector-section" key="workflow-bindings">
      <Title level={5}>{t('taskBindings')}</Title>
      {(selectedWorkflowDefinition?.task_bindings || []).length ? (
        <Table
          size="small"
          pagination={false}
          scroll={{ x: 'max-content' }}
          rowKey={(_row, index) => `binding_${index}`}
          dataSource={selectedWorkflowDefinition?.task_bindings || []}
          columns={[
            { title: t('stateMachine'), dataIndex: 'workflow_state', width: 120 },
            { title: t('component'), dataIndex: 'component', width: 160 },
            {
              title: t('classType'),
              render: (_value, row) =>
                row.task_ref?.definition_id || row.task_class || row.task_name || '-',
            },
          ]}
        />
      ) : (
        <Text type="secondary">{t('noData')}</Text>
      )}
    </div>,
    <div className="inspector-section" key="workflow-transitions">
      <Title level={5}>{t('transitions')}</Title>
      {(selectedWorkflowDefinition?.trigger_conditions || []).length ? (
        <Table
          size="small"
          pagination={false}
          scroll={{ x: 'max-content' }}
          rowKey={(_row, index) => `transition_${index}`}
          dataSource={selectedWorkflowDefinition?.trigger_conditions || []}
          columns={[
            { title: 'from', dataIndex: 'source_state', width: 110 },
            { title: 'to', dataIndex: 'target_state', width: 110 },
            { title: t('triggerSummary'), dataIndex: 'trigger_type', width: 120 },
            {
              title: t('description'),
              render: (_value, row) => row.description || row.source_ref || '-',
            },
          ]}
        />
      ) : (
        <Text type="secondary">{t('noData')}</Text>
      )}
    </div>,
  ];

  return renderInspectorShell(t, sections);
}

export function renderNodeInspector({ t, selectedNode }) {
  if (!selectedNode) {
    return renderEmptyInspector(t);
  }
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div className="inspector-section">
        <Title level={5}>{t('nodeDetails')}</Title>
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="ID">{selectedNode.id}</Descriptions.Item>
          <Descriptions.Item label={t('classType')}>{selectedNode.kind}</Descriptions.Item>
          <Descriptions.Item label={t('name')}>{selectedNode.label}</Descriptions.Item>
          <Descriptions.Item label={t('bindingStatus')}>
            {selectedNode.metadata?.binding_status || '-'}
          </Descriptions.Item>
        </Descriptions>
      </div>
      <div className="inspector-section">
        <Title level={5}>{t('nodeMetadata')}</Title>
        <pre className="inspector-json">{JSON.stringify(selectedNode.metadata || {}, null, 2)}</pre>
      </div>
    </Space>
  );
}
