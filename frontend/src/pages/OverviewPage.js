import React, { useCallback, useEffect, useState, useTransition } from 'react';
import { Alert, Button, Col, Descriptions, Empty, Row, Space, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import { runApi } from '../services/workbenchApi';

const { Title, Text } = Typography;

function formatTs(value) {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString();
}

function OverviewPage() {
  const { t } = useI18n();
  const { draftConfig, refreshDraft, reviewResult, runReview } = useWorkbench();
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState('');
  const [isPending, startTransition] = useTransition();

  const loadData = useCallback(async () => {
    setError('');
    try {
      const [nextRuns] = await Promise.all([runApi.listRuns(), refreshDraft()]);
      startTransition(() => {
        setRuns(Array.isArray(nextRuns) ? nextRuns : []);
      });
    } catch (loadError) {
      setError(loadError?.message || 'Failed to load overview data');
    }
  }, [refreshDraft, startTransition]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (draftConfig && !reviewResult) {
      runReview(draftConfig).catch(() => {});
    }
  }, [draftConfig, reviewResult, runReview]);

  const latestRun = runs[0];

  return (
    <div className="workbench-page">
      <div className="workbench-page-head">
        <Title level={4}>{t('pageOverview')}</Title>
        <Space>
          <Text type="secondary">{`${t('configId')}: ${draftConfig?.config_id || 'default'}`}</Text>
          <Button icon={<ReloadOutlined />} loading={isPending} onClick={loadData}>
            {t('refresh')}
          </Button>
        </Space>
      </div>

      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}

      <Row gutter={16}>
        <Col xs={24} xl={8}>
          <section className="panel-section">
            <Title level={5}>{t('overviewConfig')}</Title>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label={t('configId')}>{draftConfig?.config_id || '-'}</Descriptions.Item>
              <Descriptions.Item label={t('coordinateMode')}>
                {draftConfig?.coordinate_mode || 'simulation_plane'}
              </Descriptions.Item>
              <Descriptions.Item label={t('agents')}>{draftConfig?.agents?.length || 0}</Descriptions.Item>
              <Descriptions.Item label={t('workflows')}>{draftConfig?.workflows?.length || 0}</Descriptions.Item>
              <Descriptions.Item label={t('updated')}>
                {formatTs(draftConfig?.metadata?.updated_at)}
              </Descriptions.Item>
            </Descriptions>
          </section>
        </Col>

        <Col xs={24} xl={8}>
          <section className="panel-section">
            <Title level={5}>{t('overviewLatestRun')}</Title>
            {latestRun ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label={t('runId')}>{latestRun.run_id}</Descriptions.Item>
                <Descriptions.Item label={t('status')}>
                  <Tag color={latestRun.status === 'RUNNING' ? 'processing' : latestRun.status === 'ERROR' ? 'error' : 'default'}>
                    {latestRun.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('simulationTime')}>
                  {latestRun.simulation_time ?? 0}
                </Descriptions.Item>
                <Descriptions.Item label={t('started')}>
                  {formatTs(latestRun.started_at)}
                </Descriptions.Item>
                <Descriptions.Item label={t('updated')}>
                  {formatTs(latestRun.updated_at)}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('noRun')} />
            )}
          </section>
        </Col>

        <Col xs={24} xl={8}>
          <section className="panel-section">
            <Title level={5}>{t('overviewReview')}</Title>
            {reviewResult ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label={t('status')}>
                  <Tag color={reviewResult.valid ? 'success' : 'error'}>
                    {reviewResult.valid ? t('ok') : t('blocked')}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('issues')}>
                  {reviewResult.issues?.length || 0}
                </Descriptions.Item>
                <Descriptions.Item label={t('blockers')}>
                  {reviewResult.blockers?.length || 0}
                </Descriptions.Item>
                <Descriptions.Item label={t('warnings')}>
                  {(reviewResult.graph_warnings || []).length + (reviewResult.compatibility_gaps || []).length}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('reviewPending')} />
            )}
          </section>
        </Col>
      </Row>

      <section className="panel-section">
        <Title level={5}>{t('issues')}</Title>
        {reviewResult?.issues?.length ? (
          <Table
            size="small"
            rowKey={(item, index) => `${item.message}_${index}`}
            dataSource={reviewResult.issues}
            pagination={{ pageSize: 8 }}
            columns={[
              {
                title: t('status'),
                dataIndex: 'level',
                width: 120,
                render: (value) => <Tag color={value === 'error' ? 'error' : 'warning'}>{value}</Tag>,
              },
              { title: t('relation'), dataIndex: 'category', width: 160 },
              { title: t('description'), dataIndex: 'message' },
            ]}
          />
        ) : (
          <Alert type="success" showIcon message={t('validateSuccess')} />
        )}
      </section>
    </div>
  );
}

export default OverviewPage;
