// Main content panels for the run console page (map, status, marker, logs).
import React from 'react';
import { Alert, Descriptions, Empty, Space, Table, Tag, Typography } from 'antd';

import Map2D from '../../components/workbench/Map2D';
import { formatTimestamp, isSuccessfulTaskLog, runStatusColor } from './consoleModel';

const { Text, Title } = Typography;

export function renderSpatialPanel({
  t,
  toolbar,
  selectedRun,
  spatial,
  selectedRunId,
  selectedMarker,
  onSelectMarker,
}) {
  return (
    <section className="panel-section">
      <div className="section-title-row">
        <Title level={5}>{t('realtimeSpatial')}</Title>
        {toolbar}
      </div>
      <Alert
        type="info"
        showIcon
        message={t('realtimeSpatialHint')}
        style={{ marginBottom: 12 }}
      />
      {selectedRunId && spatial ? (
        <Map2D
          mode={spatial.coordinate_mode || selectedRun?.coordinate_mode || 'simulation_plane'}
          markers={spatial.agents || []}
          trajectories={[]}
          height={520}
          testId="run-console-map"
          selectedMarkerId={selectedMarker?.id}
          onSelectMarker={onSelectMarker}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRunData')} />
      )}
    </section>
  );
}

export function renderStatusPanel({ t, selectedRun, status, spatial }) {
  return (
    <section className="panel-section">
      <Title level={5}>{t('runStatus')}</Title>
      {selectedRun ? (
        <Descriptions size="small" column={1}>
          <Descriptions.Item label={t('runId')}>{selectedRun.run_id}</Descriptions.Item>
          <Descriptions.Item label={t('status')}>
            <Tag color={runStatusColor(status?.status || selectedRun.status)}>
              {status?.status || selectedRun.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label={t('simulationTime')}>
            {status?.simulation_time ?? selectedRun.simulation_time ?? '—'}
          </Descriptions.Item>
          <Descriptions.Item label={t('configId')}>{selectedRun.config_id || '-'}</Descriptions.Item>
          <Descriptions.Item label={t('coordinateMode')}>
            {spatial?.coordinate_mode || selectedRun.coordinate_mode || 'simulation_plane'}
          </Descriptions.Item>
          <Descriptions.Item label={t('updated')}>
            {formatTimestamp(status?.updated_at || selectedRun.updated_at)}
          </Descriptions.Item>
        </Descriptions>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRun')} />
      )}
    </section>
  );
}

export function renderCriticalPathPanel({ t, graph, graphError }) {
  return (
    <section className="panel-section">
      <Title level={5}>{t('criticalPath')}</Title>
      {graphError && <Alert type="error" message={graphError} />}
      {(graph?.critical_path || []).length ? (
        <div className="inline-tags">
          {(graph.critical_path || []).map((item) => (
            <Tag color="orange" key={item}>
              {item}
            </Tag>
          ))}
        </div>
      ) : (
        <Text type="secondary">{t('noData')}</Text>
      )}
    </section>
  );
}

export function renderRegistryRefsPanel({ t, selectedRun }) {
  return (
    <section className="panel-section">
      <Title level={5}>{t('registryReferences')}</Title>
      {(selectedRun?.registry_references || []).length ? (
        <div className="inline-tags">
          {(selectedRun.registry_references || []).map((item, index) => (
            <Tag key={`${item.kind}_${item.definition_id}_${index}`}>
              {`${item.kind}:${item.definition_id}@${item.version || 'latest'}`}
            </Tag>
          ))}
        </div>
      ) : (
        <Text type="secondary">{t('noData')}</Text>
      )}
    </section>
  );
}

export function renderMarkerPanel({ t, selectedMarker }) {
  return (
    <section className="panel-section map-sidecard">
      <Title level={5}>{t('liveMarkerDetail')}</Title>
      {selectedMarker ? (
        <>
          <Descriptions size="small" column={1}>
            <Descriptions.Item label="agent_id">{selectedMarker.id}</Descriptions.Item>
            <Descriptions.Item label={t('classType')}>{selectedMarker.type || '-'}</Descriptions.Item>
            <Descriptions.Item label={t('status')}>
              <Tag color={selectedMarker.status === 'error' ? 'error' : 'blue'}>
                {selectedMarker.status || 'idle'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('workflowType')}>
              {selectedMarker.workflow_id || '-'}
            </Descriptions.Item>
            <Descriptions.Item label={t('taskId')}>
              {selectedMarker.task_name || selectedMarker.task_id || '-'}
            </Descriptions.Item>
          </Descriptions>
          <div className="map-meta">
            <Tag>{`x/lng: ${selectedMarker.position?.x ?? selectedMarker.position?.lng ?? '—'}`}</Tag>
            <Tag>{`y/lat: ${selectedMarker.position?.y ?? selectedMarker.position?.lat ?? '—'}`}</Tag>
            <Tag>{`z: ${selectedMarker.position?.z ?? '—'}`}</Tag>
          </div>
          <Text className="field-help">{`${t('recentLog')}: ${selectedMarker.recent_log || '-'}`}</Text>
        </>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectMarker')} />
      )}
    </section>
  );
}

export function renderLogsPanel({ t, logs }) {
  return (
    <section className="panel-section">
      <Title level={5}>{t('realtimeLogs')}</Title>
      <Table
        size="small"
        scroll={{ x: 'max-content' }}
        rowKey={(row, index) => row.id || `${row.timestamp}_${index}`}
        dataSource={logs}
        pagination={{ pageSize: 8 }}
        columns={[
          {
            title: t('status'),
            key: 'level',
            width: 120,
            render: (_value, row) => (
              <Space direction="vertical" size={4}>
                <Tag
                  color={row.level === 'error' ? 'error' : row.level === 'warning' ? 'warning' : 'blue'}
                >
                  {row.level}
                </Tag>
                {isSuccessfulTaskLog(row) ? <Tag color="success">task ok</Tag> : null}
              </Space>
            ),
          },
          {
            title: 'Event',
            dataIndex: 'event',
            width: 200,
            render: (value) => value || '-',
          },
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
  );
}
