// Draft handling and view sections for the workflow studio page.
import React from 'react';
import {
  Alert,
  Button,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';

import RelationGraph from '../../components/workbench/RelationGraph';
import { EMPTY_GRAPH } from './studioData';

const { Paragraph, Text, Title } = Typography;

export function stampDraft(candidate) {
  const configName = candidate?.metadata?.name || candidate?.name || 'AeroAgentSim Config';
  return {
    ...candidate,
    name: configName,
    metadata: {
      ...(candidate?.metadata || {}),
      name: configName,
      updated_at: new Date().toISOString(),
    },
  };
}

export function renderDraftConfigSection({ t, draftConfig, reviewResult, updateDraft }) {
  return (
    <section className="panel-section">
      <div className="section-title-row">
        <Title level={5}>{t('draftConfiguration')}</Title>
        <Tag color={reviewResult?.valid ? 'success' : 'default'}>
          {reviewResult?.valid ? t('ok') : t('review')}
        </Tag>
      </div>
      <div className="editor-grid">
        <div className="editor-field">
          <Text strong>{t('configName')}</Text>
          <Input
            value={draftConfig.metadata?.name || draftConfig.name || ''}
            onChange={(event) =>
              updateDraft((currentDraft) => ({
                ...currentDraft,
                name: event.target.value,
                metadata: {
                  ...(currentDraft?.metadata || {}),
                  name: event.target.value,
                },
              }))
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('coordinateMode')}</Text>
          <Select
            value={draftConfig.coordinate_mode || 'simulation_plane'}
            options={[
              { value: 'simulation_plane', label: 'simulation_plane' },
              { value: 'geo_osm', label: 'geo_osm' },
            ]}
            onChange={(nextValue) =>
              updateDraft((currentDraft) => ({
                ...currentDraft,
                coordinate_mode: nextValue,
              }))
            }
          />
        </div>
        <div className="editor-field">
          <Text strong>{t('simulationSpeed')}</Text>
          <InputNumber
            min={0.1}
            step={0.1}
            value={Number(draftConfig.simulation_speed ?? 1)}
            style={{ width: '100%' }}
            onChange={(nextValue) =>
              updateDraft((currentDraft) => ({
                ...currentDraft,
                simulation_speed: nextValue ?? 1,
              }))
            }
          />
        </div>
      </div>
    </section>
  );
}

export function renderEntitySection({
  t,
  title,
  typeValue,
  typeOptions,
  onTypeChange,
  addLabel,
  onAdd,
  loading,
  rowKeySelectedId,
  dataSource,
  columns,
  rowPrefix,
  setSelectedId,
  setSelectedNodeId,
}) {
  return (
    <section className="panel-section">
      <div className="section-title-row">
        <Title level={5}>{title}</Title>
        <Space wrap>
          <Select
            value={typeValue}
            style={{ width: 220 }}
            options={typeOptions}
            onChange={onTypeChange}
          />
          <Button icon={<PlusOutlined />} onClick={onAdd}>
            {addLabel}
          </Button>
        </Space>
      </div>
      <Table
        size="small"
        loading={loading}
        className="studio-entity-table"
        scroll={{ x: 'max-content' }}
        rowKey={(row) => row.id}
        dataSource={dataSource}
        pagination={false}
        rowClassName={(row) => (row.id === rowKeySelectedId ? 'studio-row-selected' : '')}
        onRow={(row) => ({
          onClick: () => {
            setSelectedId(row.id);
            setSelectedNodeId(`${rowPrefix}:${row.id}`);
          },
        })}
        columns={columns}
      />
    </section>
  );
}

export function renderGraphSection({ t, reviewResult, graph, selectedNodeId, onNodeSelect }) {
  return (
    <section className="panel-section graph-panel">
      <div className="section-title-row">
        <Title level={5}>{t('graphTitle')}</Title>
        <Space wrap>
          <Tag>{`${t('issues')}: ${reviewResult?.issues?.length || 0}`}</Tag>
          <Tag color="orange">{`${t('graphWarnings')}: ${graph.warnings?.length || 0}`}</Tag>
        </Space>
      </div>
      <Paragraph type="secondary">{t('graphHint')}</Paragraph>
      <RelationGraph
        graph={graph || EMPTY_GRAPH}
        selectedNodeId={selectedNodeId}
        onNodeSelect={onNodeSelect}
      />
    </section>
  );
}

export function renderGraphWarningsSection({ t, graph }) {
  return (
    <section className="panel-section">
      <Title level={5}>{t('graphWarnings')}</Title>
      {(graph.warnings || []).length ? (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {(graph.warnings || []).map((warning, index) => (
            <Alert key={`${warning}_${index}`} type="warning" showIcon message={warning} />
          ))}
        </Space>
      ) : (
        <Alert type="success" showIcon message={t('validateSuccess')} />
      )}
    </section>
  );
}

export function renderInspectorSection({
  t,
  showAgentInspector,
  showWorkflowInspector,
  showNodeInspector,
  selectedNode,
  renderWorkflowInspector,
  renderAgentInspector,
  renderNodeInspector,
  metadataJson,
}) {
  return (
    <section className="panel-section">
      <div className="section-title-row">
        <Title level={5}>{t('inspector')}</Title>
        <Space wrap>
          {showAgentInspector ? <Tag color="blue">{t('selectedAgent')}</Tag> : null}
          {showWorkflowInspector ? <Tag color="cyan">{t('selectedWorkflow')}</Tag> : null}
          {showNodeInspector && selectedNode ? <Tag color="orange">{selectedNode.kind}</Tag> : null}
        </Space>
      </div>

      {showWorkflowInspector ? renderWorkflowInspector() : null}
      {showAgentInspector ? renderAgentInspector() : null}
      {showNodeInspector ? renderNodeInspector() : null}
      {!showWorkflowInspector && !showAgentInspector && !showNodeInspector ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectEntityHint')} />
      ) : null}

      <div className="inspector-section">
        <Title level={5}>{t('metadata')}</Title>
        <pre className="inspector-json">{metadataJson}</pre>
      </div>
    </section>
  );
}
