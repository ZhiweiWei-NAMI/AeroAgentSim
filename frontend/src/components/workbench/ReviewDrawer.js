import React from 'react';
import { Alert, Drawer, Empty, Space, Tag, Typography } from 'antd';

import { useI18n } from '../../i18n/I18nProvider';

const { Paragraph, Text, Title } = Typography;

function severityColor(level) {
  if (level === 'error') {
    return 'error';
  }
  return 'warning';
}

function ReviewDrawer({ open, onClose, reviewResult, reviewGraph, reviewing, error }) {
  const { t } = useI18n();
  const issues = reviewResult?.issues || [];
  const preflightChecks = reviewResult?.preflight_checks || [];

  return (
    <Drawer
      title={t('reviewTitle')}
      width={560}
      open={open}
      onClose={onClose}
      destroyOnClose={false}
    >
      <Paragraph type="secondary" style={{ marginTop: -8 }}>
        {t('reviewSummary')}
      </Paragraph>

      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}

      {reviewing ? (
        <Alert type="info" showIcon message={t('validating')} style={{ marginBottom: 16 }} />
      ) : null}

      {reviewResult ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <section className="panel-section">
            <Title level={5} style={{ marginTop: 0 }}>
              {t('reviewDraft')}
            </Title>
            <Space wrap>
              <Tag color={reviewResult.valid ? 'success' : 'error'}>
                {reviewResult.valid ? t('ok') : t('blocked')}
              </Tag>
              <Tag>{`${t('issues')}: ${issues.length}`}</Tag>
              <Tag>{`${t('warnings')}: ${(reviewResult.graph_warnings || []).length + (reviewResult.warnings || []).length}`}</Tag>
            </Space>
          </section>

          <section className="panel-section">
            <Title level={5} style={{ marginTop: 0 }}>
              {t('issues')}
            </Title>
            {issues.length ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {issues.map((issue, index) => (
                  <Alert
                    key={`${issue.category || 'issue'}_${issue.message}_${index}`}
                    type={issue.level === 'error' ? 'error' : 'warning'}
                    showIcon
                    message={issue.message}
                    description={
                      <Space wrap>
                        <Tag color={severityColor(issue.level)}>{issue.level}</Tag>
                        <Tag>{issue.category || 'validation'}</Tag>
                        {issue.path ? <Text code>{issue.path}</Text> : null}
                      </Space>
                    }
                  />
                ))}
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('validateSuccess')} />
            )}
          </section>

          <section className="panel-section">
            <Title level={5} style={{ marginTop: 0 }}>
              {t('runtimePreflight')}
            </Title>
            {preflightChecks.length ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {preflightChecks.map((check) => (
                  <Alert
                    key={check.name}
                    type={check.status === 'error' ? 'error' : check.status === 'warning' ? 'warning' : 'success'}
                    showIcon
                    message={`${check.name}: ${check.message}`}
                  />
                ))}
              </Space>
            ) : (
              <Text type="secondary">{t('reviewPending')}</Text>
            )}
          </section>

          <section className="panel-section">
            <Title level={5} style={{ marginTop: 0 }}>
              {t('graphTitle')}
            </Title>
            {(reviewGraph?.warnings || []).length ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {(reviewGraph?.warnings || []).map((warning, index) => (
                  <Alert key={`${warning}_${index}`} type="warning" showIcon message={warning} />
                ))}
              </Space>
            ) : (
              <Text type="secondary">{t('validateSuccess')}</Text>
            )}
          </section>
        </Space>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('reviewPending')} />
      )}
    </Drawer>
  );
}

export default ReviewDrawer;
