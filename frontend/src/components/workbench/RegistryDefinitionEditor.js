import React from 'react';
import { Button, Col, Input, Row, Select, Space, Switch, Table, Typography } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';

import { useI18n } from '../../i18n/I18nProvider';
import TemplateTableEditor from './TemplateTableEditor';

const { Paragraph, Title } = Typography;

const TRIGGER_TYPE_OPTIONS = [
  { value: 'state', label: 'state' },
  { value: 'event', label: 'event' },
  { value: 'time', label: 'time' },
];

const OPERATOR_OPTIONS = [
  { value: 'equals', label: 'equals' },
  { value: 'not_equals', label: 'not_equals' },
  { value: 'greater_than', label: 'greater_than' },
  { value: 'less_than', label: 'less_than' },
  { value: 'greater_equal', label: 'greater_equal' },
  { value: 'less_equal', label: 'less_equal' },
];

function parseJson(value, fallback) {
  if (!value) {
    return fallback;
  }
  try {
    return JSON.parse(value);
  } catch (_error) {
    return fallback;
  }
}

function stringifyJson(value) {
  return JSON.stringify(value || {}, null, 2);
}

function encodeTaskOption(task) {
  return JSON.stringify({
    source: task.source || 'builtin',
    id: task.id || task.name,
    version: task.version || null,
    name: task.name || task.id,
  });
}

function decodeTaskOption(value) {
  try {
    return JSON.parse(value);
  } catch (_error) {
    return null;
  }
}

function RegistryDefinitionEditor({
  kind,
  value,
  onChange,
  componentOptions = [],
  taskOptions = [],
}) {
  const { t } = useI18n();
  const definition = value || {};
  const states = definition.states || [];
  const selectedTaskValue = (binding) =>
    binding.task_ref
      ? JSON.stringify({
          source: binding.task_ref.source || 'custom',
          id: binding.task_ref.definition_id,
          version: binding.task_ref.version || null,
          name: binding.task_ref.definition_id,
        })
      : JSON.stringify({
          source: 'builtin',
          id: binding.task_class || '',
          version: null,
          name: binding.task_class || '',
        });

  const update = (patch) => onChange?.({ ...definition, ...patch });

  const updateLocalized = (field, localeKey, nextValue) => {
    update({
      [field]: {
        ...(definition[field] || {}),
        [localeKey]: nextValue,
      },
    });
  };

  const updateArray = (field, nextValue) => {
    update({ [field]: nextValue || [] });
  };

  const updateRows = (field, rows) => {
    update({ [field]: rows });
  };

  const addTrigger = () => {
    updateRows('trigger_conditions', [
      ...(definition.trigger_conditions || []),
      {
        source_state: states[0] || '',
        target_state: states[0] || '',
        trigger_type: 'state',
        source_ref: '',
        operator: 'equals',
        target_value: '',
        config: {},
        description: '',
      },
    ]);
  };

  const addBinding = () => {
    updateRows('task_bindings', [
      ...(definition.task_bindings || []),
      {
        workflow_state: states[0] || '',
        component: componentOptions[0] || '',
        task_class: '',
        task_name: '',
        task_ref: null,
        target_state: {},
        properties: {},
      },
    ]);
  };

  const triggerRows = definition.trigger_conditions || [];
  const bindingRows = definition.task_bindings || [];

  const updateTriggerField = (index, field, nextValue) => {
    const nextRows = triggerRows.slice();
    nextRows[index] = { ...nextRows[index], [field]: nextValue };
    updateRows('trigger_conditions', nextRows);
  };

  const updateBindingField = (index, field, nextValue) => {
    const nextRows = bindingRows.slice();
    nextRows[index] = { ...nextRows[index], [field]: nextValue };
    updateRows('task_bindings', nextRows);
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{t('classType')}</Paragraph>
          <Input value={definition.id || ''} onChange={(event) => update({ id: event.target.value })} />
        </Col>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{t('version')}</Paragraph>
          <Input value={definition.version || '1.0.0'} onChange={(event) => update({ version: event.target.value })} />
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{`${t('displayName')} (${t('localeEn')})`}</Paragraph>
          <Input
            value={definition.display_name?.en_US || ''}
            onChange={(event) => updateLocalized('display_name', 'en_US', event.target.value)}
          />
        </Col>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{`${t('displayName')} (${t('localeZh')})`}</Paragraph>
          <Input
            value={definition.display_name?.zh_CN || ''}
            onChange={(event) => updateLocalized('display_name', 'zh_CN', event.target.value)}
          />
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{`${t('description')} (${t('localeEn')})`}</Paragraph>
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            value={definition.description?.en_US || ''}
            onChange={(event) => updateLocalized('description', 'en_US', event.target.value)}
          />
        </Col>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{`${t('description')} (${t('localeZh')})`}</Paragraph>
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            value={definition.description?.zh_CN || ''}
            onChange={(event) => updateLocalized('description', 'zh_CN', event.target.value)}
          />
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Paragraph style={{ marginBottom: 4 }}>{t('enabled')}</Paragraph>
          <Switch checked={definition.enabled !== false} onChange={(checked) => update({ enabled: checked })} />
        </Col>
        {kind === 'agents' ? (
          <Col xs={24} md={12}>
            <Paragraph style={{ marginBottom: 4 }}>{t('baseAgentType')}</Paragraph>
            <Input
              value={definition.base_agent_type || 'DroneAgent'}
              onChange={(event) => update({ base_agent_type: event.target.value })}
            />
          </Col>
        ) : (
          <Col xs={24} md={12}>
            <Paragraph style={{ marginBottom: 4 }}>{t('adapterType')}</Paragraph>
            <Input
              value={definition.adapter_type || ''}
              onChange={(event) => update({ adapter_type: event.target.value })}
            />
          </Col>
        )}
      </Row>

      {kind === 'agents' ? (
        <>
          <div>
            <Title level={5} style={{ marginTop: 0 }}>{t('allowedComponents')}</Title>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              value={definition.allowed_components || []}
              options={componentOptions.map((item) => ({ value: item, label: item }))}
              onChange={(nextValue) => updateArray('allowed_components', nextValue)}
            />
          </div>
          <div>
            <Title level={5} style={{ marginTop: 0 }}>{t('propertySchema')}</Title>
            <TemplateTableEditor
              value={definition.state_templates || {}}
              onChange={(nextValue) => update({ state_templates: nextValue })}
              addLabel={t('create')}
            />
          </div>
          <div>
            <Title level={5} style={{ marginTop: 0 }}>{t('defaultProperties')}</Title>
            <Input.TextArea
              autoSize={{ minRows: 4, maxRows: 8 }}
              value={stringifyJson(definition.default_properties || {})}
              onChange={(event) => update({ default_properties: parseJson(event.target.value, {}) })}
            />
          </div>
        </>
      ) : null}

      {kind === 'tasks' ? (
        <>
          <Row gutter={12}>
            <Col xs={24} md={12}>
              <Paragraph style={{ marginBottom: 4 }}>{t('component')}</Paragraph>
              <Select
                allowClear
                style={{ width: '100%' }}
                value={definition.component || undefined}
                options={componentOptions.map((item) => ({ value: item, label: item }))}
                onChange={(nextValue) => update({ component: nextValue || null })}
              />
            </Col>
            <Col xs={24} md={12}>
              <Paragraph style={{ marginBottom: 4 }}>{t('necessaryMetrics')}</Paragraph>
              <Select
                mode="tags"
                style={{ width: '100%' }}
                value={definition.necessary_metrics || []}
                onChange={(nextValue) => updateArray('necessary_metrics', nextValue)}
              />
            </Col>
          </Row>
          <div>
            <Paragraph style={{ marginBottom: 4 }}>{t('producedStates')}</Paragraph>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={definition.produced_states || []}
              onChange={(nextValue) => updateArray('produced_states', nextValue)}
            />
          </div>
          <div>
            <Title level={5} style={{ marginTop: 0 }}>{t('parameterSchema')}</Title>
            <TemplateTableEditor
              value={definition.parameter_schema || {}}
              onChange={(nextValue) => update({ parameter_schema: nextValue })}
              addLabel={t('create')}
            />
          </div>
          <div>
            <Title level={5} style={{ marginTop: 0 }}>{t('targetStateSchema')}</Title>
            <TemplateTableEditor
              value={definition.target_state_schema || {}}
              onChange={(nextValue) => update({ target_state_schema: nextValue })}
              addLabel={t('create')}
            />
          </div>
        </>
      ) : null}

      {kind === 'workflows' ? (
        <>
          <div>
            <Paragraph style={{ marginBottom: 4 }}>{t('states')}</Paragraph>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={definition.states || []}
              onChange={(nextValue) => updateArray('states', nextValue)}
            />
          </div>
          <Row gutter={12}>
            <Col xs={24} md={12}>
              <Paragraph style={{ marginBottom: 4 }}>{t('startState')}</Paragraph>
              <Select
                allowClear
                style={{ width: '100%' }}
                value={definition.start_state || undefined}
                options={(definition.states || []).map((item) => ({ value: item, label: item }))}
                onChange={(nextValue) => update({ start_state: nextValue || null })}
              />
            </Col>
            <Col xs={24} md={12}>
              <Paragraph style={{ marginBottom: 4 }}>{t('supportedAgentTypes')}</Paragraph>
              <Select
                mode="tags"
                style={{ width: '100%' }}
                value={definition.supported_agent_types || []}
                onChange={(nextValue) => updateArray('supported_agent_types', nextValue)}
              />
            </Col>
          </Row>
          <div>
            <Title level={5} style={{ marginTop: 0 }}>{t('propertySchema')}</Title>
            <TemplateTableEditor
              value={definition.property_templates || {}}
              onChange={(nextValue) => update({ property_templates: nextValue })}
              addLabel={t('create')}
            />
          </div>

          <div>
            <div className="section-title-row">
              <Title level={5} style={{ marginTop: 0 }}>{t('triggerConditions')}</Title>
              <Button size="small" icon={<PlusOutlined />} onClick={addTrigger}>
                {t('create')}
              </Button>
            </div>
            <Table
              size="small"
              pagination={false}
              rowKey={(_row, index) => `trigger_${index}`}
              dataSource={triggerRows}
              scroll={{ x: 1180 }}
              columns={[
                {
                  title: 'from',
                  dataIndex: 'source_state',
                  width: 120,
                  render: (value, _row, index) => (
                    <Input value={value} onChange={(event) => updateTriggerField(index, 'source_state', event.target.value)} />
                  ),
                },
                {
                  title: 'to',
                  dataIndex: 'target_state',
                  width: 120,
                  render: (value, _row, index) => (
                    <Input value={value} onChange={(event) => updateTriggerField(index, 'target_state', event.target.value)} />
                  ),
                },
                {
                  title: 'trigger',
                  dataIndex: 'trigger_type',
                  width: 120,
                  render: (value, _row, index) => (
                    <Select
                      value={value}
                      options={TRIGGER_TYPE_OPTIONS}
                      style={{ width: '100%' }}
                      onChange={(nextValue) => updateTriggerField(index, 'trigger_type', nextValue)}
                    />
                  ),
                },
                {
                  title: 'source_ref',
                  dataIndex: 'source_ref',
                  width: 160,
                  render: (value, _row, index) => (
                    <Input value={value} onChange={(event) => updateTriggerField(index, 'source_ref', event.target.value)} />
                  ),
                },
                {
                  title: 'operator',
                  dataIndex: 'operator',
                  width: 140,
                  render: (value, _row, index) => (
                    <Select
                      allowClear
                      value={value}
                      options={OPERATOR_OPTIONS}
                      style={{ width: '100%' }}
                      onChange={(nextValue) => updateTriggerField(index, 'operator', nextValue)}
                    />
                  ),
                },
                {
                  title: 'target_value',
                  dataIndex: 'target_value',
                  width: 160,
                  render: (value, _row, index) => (
                    <Input value={value} onChange={(event) => updateTriggerField(index, 'target_value', event.target.value)} />
                  ),
                },
                {
                  title: 'config',
                  dataIndex: 'config',
                  width: 220,
                  render: (value, _row, index) => (
                    <Input.TextArea
                      autoSize={{ minRows: 1, maxRows: 3 }}
                      value={stringifyJson(value || {})}
                      onChange={(event) => updateTriggerField(index, 'config', parseJson(event.target.value, {}))}
                    />
                  ),
                },
                {
                  title: 'description',
                  dataIndex: 'description',
                  render: (value, _row, index) => (
                    <Input value={value} onChange={(event) => updateTriggerField(index, 'description', event.target.value)} />
                  ),
                },
                {
                  title: t('action'),
                  dataIndex: 'action',
                  width: 64,
                  render: (_value, _row, index) => (
                    <Button
                      danger
                      type="text"
                      icon={<DeleteOutlined />}
                      onClick={() => updateRows('trigger_conditions', triggerRows.filter((_item, rowIndex) => rowIndex !== index))}
                    />
                  ),
                },
              ]}
            />
          </div>

          <div>
            <div className="section-title-row">
              <Title level={5} style={{ marginTop: 0 }}>{t('taskBindings')}</Title>
              <Button size="small" icon={<PlusOutlined />} onClick={addBinding}>
                {t('create')}
              </Button>
            </div>
            <Table
              size="small"
              pagination={false}
              rowKey={(_row, index) => `binding_${index}`}
              dataSource={bindingRows}
              scroll={{ x: 1280 }}
              columns={[
                {
                  title: 'state',
                  dataIndex: 'workflow_state',
                  width: 140,
                  render: (value, _row, index) => (
                    <Select
                      allowClear
                      value={value}
                      options={(definition.states || []).map((item) => ({ value: item, label: item }))}
                      style={{ width: '100%' }}
                      onChange={(nextValue) => updateBindingField(index, 'workflow_state', nextValue)}
                    />
                  ),
                },
                {
                  title: t('component'),
                  dataIndex: 'component',
                  width: 160,
                  render: (value, _row, index) => (
                    <Select
                      allowClear
                      value={value}
                      options={componentOptions.map((item) => ({ value: item, label: item }))}
                      style={{ width: '100%' }}
                      onChange={(nextValue) => updateBindingField(index, 'component', nextValue)}
                    />
                  ),
                },
                {
                  title: 'task',
                  dataIndex: 'task',
                  width: 240,
                  render: (_value, row, index) => (
                    <Select
                      showSearch
                      value={selectedTaskValue(row)}
                      options={taskOptions.map((task) => ({
                        value: encodeTaskOption(task),
                        label: `${task.name || task.id} [${task.source}${task.version ? `@${task.version}` : ''}]`,
                      }))}
                      style={{ width: '100%' }}
                      onChange={(nextValue) => {
                        const decoded = decodeTaskOption(nextValue);
                        if (!decoded) {
                          return;
                        }
                        if (decoded.source === 'custom') {
                          updateBindingField(index, 'task_ref', {
                            kind: 'tasks',
                            definition_id: decoded.id,
                            version: decoded.version,
                            source: 'custom',
                          });
                          updateBindingField(index, 'task_class', null);
                        } else {
                          updateBindingField(index, 'task_ref', null);
                          updateBindingField(index, 'task_class', decoded.name || decoded.id);
                        }
                      }}
                    />
                  ),
                },
                {
                  title: t('name'),
                  dataIndex: 'task_name',
                  width: 180,
                  render: (value, _row, index) => (
                    <Input value={value} onChange={(event) => updateBindingField(index, 'task_name', event.target.value)} />
                  ),
                },
                {
                  title: 'target_state',
                  dataIndex: 'target_state',
                  width: 220,
                  render: (value, _row, index) => (
                    <Input.TextArea
                      autoSize={{ minRows: 1, maxRows: 3 }}
                      value={stringifyJson(value || {})}
                      onChange={(event) => updateBindingField(index, 'target_state', parseJson(event.target.value, {}))}
                    />
                  ),
                },
                {
                  title: 'properties',
                  dataIndex: 'properties',
                  width: 220,
                  render: (value, _row, index) => (
                    <Input.TextArea
                      autoSize={{ minRows: 1, maxRows: 3 }}
                      value={stringifyJson(value || {})}
                      onChange={(event) => updateBindingField(index, 'properties', parseJson(event.target.value, {}))}
                    />
                  ),
                },
                {
                  title: t('action'),
                  dataIndex: 'action',
                  width: 64,
                  render: (_value, _row, index) => (
                    <Button
                      danger
                      type="text"
                      icon={<DeleteOutlined />}
                      onClick={() => updateRows('task_bindings', bindingRows.filter((_item, rowIndex) => rowIndex !== index))}
                    />
                  ),
                },
              ]}
            />
          </div>

          <div>
            <Paragraph style={{ marginBottom: 4 }}>{t('criticalPath')}</Paragraph>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={definition.critical_path || []}
              onChange={(nextValue) => updateArray('critical_path', nextValue)}
            />
          </div>
        </>
      ) : null}
    </Space>
  );
}

export default RegistryDefinitionEditor;
