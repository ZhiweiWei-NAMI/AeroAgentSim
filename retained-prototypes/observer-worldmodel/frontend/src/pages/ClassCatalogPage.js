import React, { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Input, Segmented, Space, Table, Tabs, Tag, Typography } from 'antd';
import { CopyOutlined, DeleteOutlined, PlusOutlined, SaveOutlined, SearchOutlined } from '@ant-design/icons';

import RegistryDefinitionEditor from '../components/workbench/RegistryDefinitionEditor';
import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import { catalogApi, registryApi } from '../services/workbenchApi';

const { Text, Title } = Typography;

function createBlankDefinition(kind) {
  if (kind === 'agents') {
    return {
      id: '',
      version: '1.0.0',
      enabled: true,
      display_name: { en_US: '', zh_CN: '' },
      description: { en_US: '', zh_CN: '' },
      base_agent_type: 'DroneAgent',
      allowed_components: [],
      state_templates: {},
      default_properties: {},
      initialization_schema: {},
    };
  }
  if (kind === 'tasks') {
    return {
      id: '',
      version: '1.0.0',
      enabled: true,
      display_name: { en_US: '', zh_CN: '' },
      description: { en_US: '', zh_CN: '' },
      adapter_type: 'declarative',
      component: null,
      necessary_metrics: [],
      produced_states: [],
      parameter_schema: {},
      target_state_schema: {},
    };
  }
  return {
    id: '',
    version: '1.0.0',
    enabled: true,
    display_name: { en_US: '', zh_CN: '' },
    description: { en_US: '', zh_CN: '' },
    adapter_type: 'declarative',
    states: [],
    start_state: null,
    property_templates: {},
    trigger_conditions: [],
    task_bindings: [],
    critical_path: [],
    supported_agent_types: [],
  };
}

function filterByKeyword(data, keyword) {
  if (!keyword) {
    return data;
  }
  const normalized = keyword.toLowerCase();
  return data.filter((item) => JSON.stringify(item).toLowerCase().includes(normalized));
}

function ClassCatalogPage() {
  const { t } = useI18n();
  const { authoritativeActionsEnabled, displayOnlyFallbackMode } = useWorkbench();
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [registryValidation, setRegistryValidation] = useState(null);
  const [catalog, setCatalog] = useState({
    agents: [],
    components: [],
    tasks: [],
    workflows: [],
    compatibility: [],
  });
  const [registryData, setRegistryData] = useState({
    agents: [],
    tasks: [],
    workflows: [],
  });
  const [activeRegistryKind, setActiveRegistryKind] = useState('agents');
  const [selectedRegistryKey, setSelectedRegistryKey] = useState('');
  const [editorValue, setEditorValue] = useState(createBlankDefinition('agents'));

  const deferredKeyword = useDeferredValue(keyword);

  const loadAll = async () => {
    setLoading(true);
    setError('');
    try {
      const [agents, components, tasks, workflows, compatibility, regAgents, regTasks, regWorkflows] = await Promise.all([
        catalogApi.getAgents('all'),
        catalogApi.getComponents(),
        catalogApi.getTasks('all'),
        catalogApi.getWorkflows('all'),
        catalogApi.getCompatibility('all'),
        registryApi.list('agents'),
        registryApi.list('tasks'),
        registryApi.list('workflows'),
      ]);
      setCatalog({
        agents,
        components,
        tasks,
        workflows,
        compatibility,
      });
      setRegistryData({
        agents: regAgents,
        tasks: regTasks,
        workflows: regWorkflows,
      });
    } catch (loadError) {
      setError(loadError?.message || 'Failed to load catalog');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const registryList = useMemo(
    () => registryData[activeRegistryKind] || [],
    [activeRegistryKind, registryData]
  );

  useEffect(() => {
    const selected = registryList.find(
      (item) => `${item.id}@${item.version}` === selectedRegistryKey
    );
    setEditorValue(selected || createBlankDefinition(activeRegistryKind));
    setRegistryValidation(null);
  }, [activeRegistryKind, registryList, selectedRegistryKey]);

  const filteredCatalog = useMemo(
    () => ({
      agents: filterByKeyword(catalog.agents, deferredKeyword),
      components: filterByKeyword(catalog.components, deferredKeyword),
      tasks: filterByKeyword(catalog.tasks, deferredKeyword),
      workflows: filterByKeyword(catalog.workflows, deferredKeyword),
      compatibility: filterByKeyword(catalog.compatibility, deferredKeyword),
      registry: filterByKeyword(registryList, deferredKeyword),
    }),
    [catalog, deferredKeyword, registryList]
  );

  const saveDefinition = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const saved = await registryApi.save(activeRegistryKind, editorValue);
      setSelectedRegistryKey(`${saved.id}@${saved.version}`);
      setMessage(`${t('save')} OK`);
      await loadAll();
    } catch (saveError) {
      setError(saveError?.message || 'Failed to save definition');
    } finally {
      setSaving(false);
    }
  };

  const validateDefinition = async () => {
    setError('');
    try {
      const result = await registryApi.validate(
        activeRegistryKind,
        editorValue.id || 'draft',
        editorValue,
        editorValue.version
      );
      setRegistryValidation(result);
    } catch (validationError) {
      setError(validationError?.message || 'Failed to validate definition');
    }
  };

  const deleteDefinition = async () => {
    if (!editorValue?.id) {
      return;
    }
    setSaving(true);
    setError('');
    try {
      await registryApi.delete(activeRegistryKind, editorValue.id, editorValue.version);
      setSelectedRegistryKey('');
      setEditorValue(createBlankDefinition(activeRegistryKind));
      await loadAll();
    } catch (deleteError) {
      setError(deleteError?.message || 'Failed to delete definition');
    } finally {
      setSaving(false);
    }
  };

  const copyDefinition = () => {
    setEditorValue({
      ...editorValue,
      id: editorValue.id ? `${editorValue.id}_copy` : '',
      version: '1.0.0',
    });
    setSelectedRegistryKey('');
  };

  return (
    <div className="workbench-page">
      <div className="workbench-page-head">
        <Title level={4}>{t('pageCatalog')}</Title>
        <Space wrap>
          <Text type="secondary">{t('filterByKeyword')}</Text>
          <Input
            allowClear
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            prefix={<SearchOutlined />}
            placeholder={t('filterByKeyword')}
            style={{ width: 280 }}
          />
          <Button onClick={loadAll}>{t('refresh')}</Button>
        </Space>
      </div>

      {message ? <Alert type="success" showIcon message={message} style={{ marginBottom: 12 }} /> : null}
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
      {displayOnlyFallbackMode ? (
        <Alert
          type="warning"
          showIcon
          message={t('authoritativeActionsDisabled')}
          description={t('catalogOfflineHint')}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Tabs
        items={[
          {
            key: 'builtin',
            label: t('builtinCatalog'),
            children: (
              <Tabs
                items={[
                  {
                    key: 'agents',
                    label: `${t('catalogAgents')} (${filteredCatalog.agents.length})`,
                    children: (
                      <Table
                        size="small"
                        loading={loading}
                        scroll={{ x: 'max-content' }}
                        rowKey={(row) => `${row.id}_${row.version}`}
                        dataSource={filteredCatalog.agents}
                        pagination={{ pageSize: 8 }}
                        columns={[
                          { title: t('classType'), dataIndex: 'name', width: 220 },
                          { title: t('source'), dataIndex: 'source', width: 100 },
                          { title: t('version'), dataIndex: 'version', width: 120 },
                          {
                            title: t('producedStates'),
                            dataIndex: 'state_templates',
                            render: (value) => Object.keys(value || {}).join(', '),
                          },
                          { title: t('description'), dataIndex: 'description' },
                        ]}
                      />
                    ),
                  },
                  {
                    key: 'components',
                    label: `${t('catalogComponents')} (${filteredCatalog.components.length})`,
                    children: (
                      <Table
                        size="small"
                        loading={loading}
                        scroll={{ x: 'max-content' }}
                        rowKey={(row) => row.name}
                        dataSource={filteredCatalog.components}
                        pagination={{ pageSize: 8 }}
                        columns={[
                          { title: t('classType'), dataIndex: 'name', width: 220 },
                          {
                            title: t('producedMetrics'),
                            dataIndex: 'produced_metrics',
                            render: (value) => (value || []).join(', '),
                          },
                          {
                            title: t('monitoredStates'),
                            dataIndex: 'monitored_states',
                            render: (value) => (value || []).join(', '),
                          },
                          { title: t('description'), dataIndex: 'description' },
                        ]}
                      />
                    ),
                  },
                  {
                    key: 'tasks',
                    label: `${t('catalogTasks')} (${filteredCatalog.tasks.length})`,
                    children: (
                      <Table
                        size="small"
                        loading={loading}
                        scroll={{ x: 'max-content' }}
                        rowKey={(row) => `${row.id}_${row.version}`}
                        dataSource={filteredCatalog.tasks}
                        pagination={{ pageSize: 8 }}
                        columns={[
                          { title: t('classType'), dataIndex: 'name', width: 220 },
                          { title: t('source'), dataIndex: 'source', width: 100 },
                          { title: t('version'), dataIndex: 'version', width: 120 },
                          {
                            title: t('necessaryMetrics'),
                            dataIndex: 'necessary_metrics',
                            render: (value) => (value || []).join(', '),
                          },
                          {
                            title: t('producedStates'),
                            dataIndex: 'produced_states',
                            render: (value) => (value || []).join(', '),
                          },
                          { title: t('description'), dataIndex: 'description' },
                        ]}
                      />
                    ),
                  },
                  {
                    key: 'workflows',
                    label: `${t('catalogWorkflows')} (${filteredCatalog.workflows.length})`,
                    children: (
                      <Table
                        size="small"
                        loading={loading}
                        scroll={{ x: 'max-content' }}
                        rowKey={(row) => `${row.id}_${row.version}`}
                        dataSource={filteredCatalog.workflows}
                        pagination={{ pageSize: 8 }}
                        columns={[
                          { title: t('classType'), dataIndex: 'name', width: 220 },
                          { title: t('source'), dataIndex: 'source', width: 100 },
                          { title: t('version'), dataIndex: 'version', width: 120 },
                          {
                            title: t('states'),
                            dataIndex: 'states',
                            render: (value) => (value || []).join(', '),
                          },
                          { title: t('description'), dataIndex: 'description' },
                        ]}
                      />
                    ),
                  },
                  {
                    key: 'compatibility',
                    label: `${t('compatibility')} (${filteredCatalog.compatibility.length})`,
                    children: (
                      <Table
                        size="small"
                        loading={loading}
                        scroll={{ x: 'max-content' }}
                        rowKey={(row, index) => `${row.relation}_${row.source}_${row.target}_${index}`}
                        dataSource={filteredCatalog.compatibility}
                        pagination={{ pageSize: 8 }}
                        columns={[
                          {
                            title: t('relation'),
                            dataIndex: 'relation',
                            width: 220,
                            render: (value) => <Tag color="blue">{value}</Tag>,
                          },
                          { title: t('sourceLabel'), dataIndex: 'source', width: 240 },
                          { title: t('targetLabel'), dataIndex: 'target', width: 240 },
                          { title: t('score'), dataIndex: 'score', width: 80 },
                        ]}
                      />
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: 'registry',
            label: t('registry'),
            children: (
              <div className="registry-layout">
                <section className="panel-section registry-list-panel">
                  <div className="section-title-row">
                    <Title level={5} style={{ marginTop: 0 }}>{t('registry')}</Title>
                    <Space wrap>
                      <Segmented
                        value={activeRegistryKind}
                        onChange={(value) => {
                          setActiveRegistryKind(value);
                          setSelectedRegistryKey('');
                        }}
                        options={[
                          { value: 'agents', label: t('registryKindAgents') },
                          { value: 'tasks', label: t('registryKindTasks') },
                          { value: 'workflows', label: t('registryKindWorkflows') },
                        ]}
                      />
                      <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        disabled={!authoritativeActionsEnabled}
                        onClick={() => {
                          setSelectedRegistryKey('');
                          setEditorValue(createBlankDefinition(activeRegistryKind));
                        }}
                      >
                        {t('create')}
                      </Button>
                    </Space>
                  </div>

                  <Table
                    size="small"
                    loading={loading}
                    scroll={{ x: 'max-content' }}
                    rowKey={(row) => `${row.id}@${row.version}`}
                    dataSource={filteredCatalog.registry}
                    pagination={{ pageSize: 8 }}
                    rowSelection={{
                      type: 'radio',
                      selectedRowKeys: selectedRegistryKey ? [selectedRegistryKey] : [],
                      onChange: (keys) => setSelectedRegistryKey(keys[0] || ''),
                    }}
                    columns={[
                      { title: t('classType'), dataIndex: 'id', width: 180 },
                      { title: t('version'), dataIndex: 'version', width: 120 },
                      {
                        title: t('enabled'),
                        dataIndex: 'enabled',
                        width: 100,
                        render: (value) => <Tag color={value === false ? 'default' : 'green'}>{String(value !== false)}</Tag>,
                      },
                      {
                        title: t('usage'),
                        dataIndex: 'usage',
                        render: (value) => `${value?.configs || 0} ${t('configs')} / ${value?.runs || 0} ${t('runs')}`,
                      },
                    ]}
                  />
                </section>

                <section className="panel-section registry-editor-panel">
                  <div className="section-title-row">
                    <Title level={5} style={{ marginTop: 0 }}>{t('registryEditor')}</Title>
                    <Space wrap>
                      <Button icon={<CopyOutlined />} disabled={!authoritativeActionsEnabled} onClick={copyDefinition}>
                        {t('copy')}
                      </Button>
                      <Button danger icon={<DeleteOutlined />} disabled={!authoritativeActionsEnabled} onClick={deleteDefinition}>
                        {t('delete')}
                      </Button>
                      <Button disabled={!authoritativeActionsEnabled} onClick={validateDefinition}>{t('registryValidate')}</Button>
                      <Button
                        type="primary"
                        icon={<SaveOutlined />}
                        loading={saving}
                        disabled={!authoritativeActionsEnabled}
                        onClick={saveDefinition}
                      >
                        {t('save')}
                      </Button>
                    </Space>
                  </div>

                  <RegistryDefinitionEditor
                    kind={activeRegistryKind}
                    value={editorValue}
                    onChange={setEditorValue}
                    componentOptions={catalog.components.map((item) => item.name)}
                    taskOptions={catalog.tasks}
                  />

                  <div style={{ marginTop: 16 }}>
                    <Title level={5} style={{ marginTop: 0 }}>{t('registryPreview')}</Title>
                    <pre className="json-preview">{JSON.stringify(editorValue, null, 2)}</pre>
                  </div>

                  {registryValidation ? (
                    <div style={{ marginTop: 16 }}>
                      <Title level={5} style={{ marginTop: 0 }}>{t('issues')}</Title>
                      {(registryValidation.issues || []).length ? (
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          {(registryValidation.issues || []).map((issue, index) => (
                            <Alert
                              key={`${issue.message}_${index}`}
                              type={issue.level === 'error' ? 'error' : 'warning'}
                              showIcon
                              message={issue.message}
                            />
                          ))}
                        </Space>
                      ) : (
                        <Alert type="success" showIcon message={t('validateSuccess')} />
                      )}
                    </div>
                  ) : null}
                </section>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}

export default ClassCatalogPage;
