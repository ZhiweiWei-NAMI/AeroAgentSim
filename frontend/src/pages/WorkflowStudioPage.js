import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
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
import { DeleteOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons';

import RelationGraph from '../components/workbench/RelationGraph';
import { useWorkbench } from '../context/WorkbenchContext';
import { useI18n } from '../i18n/I18nProvider';
import {
  catalogApi,
  configApi,
  defaultComponentsForDefinition,
  MOCK_CONFIG_ID,
  normalizeAgentTypeToken,
  normalizeWorkflowTypeToken,
} from '../services/workbenchApi';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

const EMPTY_GRAPH = {
  nodes: [],
  edges: [],
  trigger_conditions: [],
  task_bindings: [],
  critical_path: [],
  warnings: [],
};

function localizeKey(locale) {
  return locale === 'zh-CN' ? 'zh_CN' : 'en_US';
}

function resolveDefinitionName(definition, locale) {
  if (!definition) {
    return '';
  }
  return (
    definition.display_name?.[localizeKey(locale)] ||
    definition.name ||
    definition.id ||
    definition.type ||
    ''
  );
}

function defaultValueForTemplate(template) {
  if (template?.default !== undefined && template?.default !== null) {
    return template.default;
  }
  const type = String(template?.value_type || 'string').toLowerCase();
  if (type.includes('bool')) {
    return false;
  }
  if (type.includes('int') || type.includes('float') || type.includes('number')) {
    return 0;
  }
  if (type.includes('dict') || type.includes('object')) {
    return {};
  }
  if (type.includes('list') || type.includes('array')) {
    return [];
  }
  return '';
}

function mergeTemplateDefaults(templateMap, current = {}, base = {}) {
  const next = { ...base, ...current };
  Object.entries(templateMap || {}).forEach(([key, template]) => {
    if (next[key] === undefined) {
      next[key] = defaultValueForTemplate(template);
    }
  });
  return next;
}

function filterCompatibleComponents(existingComponents, definition) {
  const compatibleSet = new Set(definition?.compatible_components || []);
  const filtered = (existingComponents || []).filter((c) => compatibleSet.has(c));
  if (filtered.length > 0) {
    return filtered;
  }
  return defaultComponentsForDefinition(definition);
}

function defaultInitialPositionForDefinition(definition, index) {
  const normalized = normalizeAgentTypeToken(definition?.id || definition?.name);
  const basePosition = [40 + index * 40, 60 + index * 20, 10];
  if (normalized.includes('station')) {
    return [basePosition[0], basePosition[1], 0];
  }
  if (normalized.includes('drone')) {
    return [basePosition[0], basePosition[1], 30];
  }
  return basePosition;
}

function buildDefinitionRef(kind, definition) {
  if (!definition || definition.source !== 'custom') {
    return null;
  }
  return {
    kind,
    definition_id: definition.id,
    version: definition.version || null,
    source: 'custom',
  };
}

function cloneValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => cloneValue(item));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneValue(item)])
    );
  }
  return value;
}

function resolveAgentPosition(agent) {
  const rawPosition = agent?.initial_position || agent?.properties?.position || [0, 0, 0];
  return [0, 1, 2].map((index) => {
    const numeric = Number(rawPosition?.[index] ?? 0);
    return Number.isFinite(numeric) ? numeric : 0;
  });
}

function resolveWorkflowFlightAltitude(agent) {
  const position = resolveAgentPosition(agent);
  const altitude = Number(position?.[2] ?? 0);
  return altitude > 0 ? altitude : 30;
}

function withAltitude(position, altitude) {
  const normalized = resolveAgentPosition({ initial_position: position });
  return [normalized[0], normalized[1], altitude];
}

function isCoordinate3d(value) {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((item) => typeof item === 'number' && !Number.isNaN(item))
  );
}

function isCoordinateCollection(value) {
  return Array.isArray(value) && value.length > 0 && value.every((item) => isCoordinate3d(item));
}

function isPayloadCollection(value) {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (item) =>
        item &&
        typeof item === 'object' &&
        !Array.isArray(item) &&
        typeof item.id === 'string' &&
        item.id
    )
  );
}

function isCompatibleWorkflowOwner(definition, agent) {
  const workflowType = normalizeWorkflowTypeToken(definition?.id || definition?.name);
  const agentType = normalizeAgentTypeToken(agent?.type);
  if (workflowType === 'logistics') {
    return agentType === 'deliverydrone' || agentType === 'delivery';
  }
  if (['inspection', 'charging', 'imageprocessing'].includes(workflowType)) {
    return agentType === 'drone' || agentType === 'deliverydrone';
  }
  return true;
}

function selectWorkflowOwnerId(definition, agents = [], preferredAgentId = '') {
  const preferred = (agents || []).find((agent) => agent.id === preferredAgentId);
  if (preferred && isCompatibleWorkflowOwner(definition, preferred)) {
    return preferred.id;
  }
  const compatible = (agents || []).find((agent) => isCompatibleWorkflowOwner(definition, agent));
  if (compatible) {
    return compatible.id;
  }
  return preferred?.id || agents?.[0]?.id || '';
}

function selectLogisticsStations(agents = []) {
  const deliveryStations = (agents || []).filter(
    (agent) => normalizeAgentTypeToken(agent?.type) === 'deliverystation'
  );
  if (deliveryStations.length >= 1) {
    return {
      source: deliveryStations[0],
      target: deliveryStations[1] || deliveryStations[0],
    };
  }
  const stations = (agents || []).filter((agent) =>
    normalizeAgentTypeToken(agent?.type).includes('station')
  );
  return {
    source: stations[0] || null,
    target: stations[1] || stations[0] || null,
  };
}

function buildWorkflowProperties(
  definition,
  workflowAgentId,
  agents = [],
  index = 0,
  currentProperties = {}
) {
  const properties = mergeTemplateDefaults(definition.property_templates || {}, currentProperties);
  const normalized = normalizeWorkflowTypeToken(definition?.id || definition?.name);
  const workflowAgent = (agents || []).find((agent) => agent.id === workflowAgentId) || null;

  if (normalized === 'inspection') {
    if (isCoordinateCollection(currentProperties.inspection_points)) {
      return properties;
    }
    const [x, y, z] = resolveAgentPosition(workflowAgent);
    return {
      ...properties,
      inspection_points: [
        [x + 40, y, z || 30],
        [x + 40, y + 40, z || 30],
      ],
    };
  }

  if (normalized === 'imageprocessing') {
    if (isCoordinateCollection(currentProperties.sensing_locations)) {
      return properties;
    }
    const [x, y, z] = resolveAgentPosition(workflowAgent);
    return {
      ...properties,
      sensing_locations: [
        [x + 30, y, z || 30],
        [x + 60, y + 20, z || 30],
      ],
    };
  }

  if (normalized !== 'logistics') {
    return properties;
  }

  const altitude = resolveWorkflowFlightAltitude(workflowAgent);
  const { source, target } = selectLogisticsStations(agents);
  const sourceAgentId =
    (agents || []).some((agent) => agent.id === currentProperties.source_agent_id)
      ? currentProperties.source_agent_id
      : source?.id || '';
  const targetAgentId =
    (agents || []).some((agent) => agent.id === currentProperties.target_agent_id)
      ? currentProperties.target_agent_id
      : target?.id || '';
  const sourceAgent = (agents || []).find((agent) => agent.id === sourceAgentId) || source || null;
  const targetAgent = (agents || []).find((agent) => agent.id === targetAgentId) || target || null;

  return {
    ...properties,
    pickup_location: isCoordinate3d(currentProperties.pickup_location)
      ? cloneValue(currentProperties.pickup_location)
      : withAltitude(resolveAgentPosition(sourceAgent || workflowAgent), altitude),
    delivery_location: isCoordinate3d(currentProperties.delivery_location)
      ? cloneValue(currentProperties.delivery_location)
      : withAltitude(resolveAgentPosition(targetAgent || workflowAgent), altitude),
    payloads: isPayloadCollection(currentProperties.payloads)
      ? cloneValue(currentProperties.payloads)
      : [
          {
            id: `payload_${index + 1}`,
            description: 'Starter logistics payload',
          },
        ],
    source_agent_id: sourceAgentId,
    target_agent_id: targetAgentId,
  };
}

function buildAgentInstance(definition, index) {
  const definitionRef = buildDefinitionRef('agents', definition);
  const initialPosition = defaultInitialPositionForDefinition(definition, index);
  const properties = mergeTemplateDefaults(
    definition.state_templates || {},
    {},
    definition.default_properties || {}
  );
  if (properties.position === undefined) {
    properties.position = initialPosition;
  }
  return {
    id: `agent_${index + 1}`,
    name: resolveDefinitionName(definition, 'en-US') || `Agent ${index + 1}`,
    type: definition.id,
    source: definition.source || 'builtin',
    version: definition.version || null,
    definition_ref: definitionRef,
    registry_ref: definitionRef,
    initial_position: initialPosition,
    initial_battery: 100,
    components: defaultComponentsForDefinition(definition),
    properties,
  };
}

export function buildWorkflowInstance(definition, agents = [], index = 0, preferredAgentId = '') {
  const definitionRef = buildDefinitionRef('workflows', definition);
  const workflowAgentId = selectWorkflowOwnerId(definition, agents, preferredAgentId);
  return {
    id: `workflow_${index + 1}`,
    name: resolveDefinitionName(definition, 'en-US') || `Workflow ${index + 1}`,
    type: definition.id,
    source: definition.source || 'builtin',
    version: definition.version || null,
    definition_ref: definitionRef,
    registry_ref: definitionRef,
    agent_id: workflowAgentId,
    enabled: true,
    properties: buildWorkflowProperties(definition, workflowAgentId, agents, index),
  };
}

function isNumeric(valueType) {
  const type = String(valueType || '').toLowerCase();
  return type.includes('int') || type.includes('float') || type.includes('number');
}

function isBoolean(valueType) {
  return String(valueType || '').toLowerCase().includes('bool');
}

function isStructured(valueType) {
  const type = String(valueType || '').toLowerCase();
  return (
    type.includes('list') ||
    type.includes('array') ||
    type.includes('dict') ||
    type.includes('object')
  );
}

function JsonField({ value, onChange }) {
  const [text, setText] = useState(JSON.stringify(value ?? {}, null, 2));
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    setText(JSON.stringify(value ?? {}, null, 2));
    setInvalid(false);
  }, [value]);

  return (
    <>
      <TextArea
        rows={4}
        value={text}
        onChange={(event) => {
          const nextText = event.target.value;
          setText(nextText);
          if (!nextText.trim()) {
            setInvalid(false);
            onChange({});
            return;
          }
          try {
            const parsed = JSON.parse(nextText);
            setInvalid(false);
            onChange(parsed);
          } catch (_error) {
            setInvalid(true);
          }
        }}
      />
      {invalid ? <Text type="danger" className="field-help">JSON parse error</Text> : null}
    </>
  );
}

function TemplateEditor({ templates, values, onChange }) {
  const entries = Object.entries(templates || {});
  if (!entries.length) {
    return <Text type="secondary">No schema fields.</Text>;
  }

  return (
    <div className="editor-grid">
      {entries.map(([key, template]) => {
        const currentValue =
          values?.[key] !== undefined ? values[key] : defaultValueForTemplate(template);
        const fullWidth = isStructured(template.value_type);
        return (
          <div
            key={key}
            className={`editor-field ${fullWidth ? 'editor-field-full' : ''}`}
          >
            <Text strong>{key}</Text>
            {template.description ? <Text className="field-help">{template.description}</Text> : null}
            {isBoolean(template.value_type) ? (
              <Switch
                checked={Boolean(currentValue)}
                onChange={(checked) => onChange(key, checked)}
              />
            ) : null}
            {isNumeric(template.value_type) ? (
              <InputNumber
                value={Number(currentValue ?? 0)}
                style={{ width: '100%' }}
                onChange={(nextValue) => onChange(key, nextValue ?? 0)}
              />
            ) : null}
            {!isBoolean(template.value_type) &&
            !isNumeric(template.value_type) &&
            !isStructured(template.value_type) ? (
              <Input
                value={currentValue ?? ''}
                onChange={(event) => onChange(key, event.target.value)}
              />
            ) : null}
            {isStructured(template.value_type) ? (
              <JsonField value={currentValue} onChange={(nextValue) => onChange(key, nextValue)} />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

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

  const [catalog, setCatalog] = useState({
    agents: [],
    components: [],
    tasks: [],
    workflows: [],
  });
  const [graph, setGraph] = useState(EMPTY_GRAPH);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [nextAgentType, setNextAgentType] = useState(null);
  const [nextWorkflowType, setNextWorkflowType] = useState(null);
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [selectedWorkflowId, setSelectedWorkflowId] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState('');

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
  }, [nextAgentType, nextWorkflowType]);

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
  }, [reviewGraph]);

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
          setGraph(nextGraph || EMPTY_GRAPH);
        }
      } catch (_error) {
        if (active && reviewGraph) {
          setGraph(reviewGraph);
        }
      }
    }, 180);

    return () => {
      active = false;
      window.clearTimeout(handle);
    };
  }, [draftConfig, reviewGraph]);

  const stampDraft = (candidate) => {
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
  };

  const updateDraft = (updater) => {
    setDraftConfig((previous) => stampDraft(typeof updater === 'function' ? updater(previous) : updater));
    setMessage('');
  };

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

  const findAgentDefinition = useCallback(
    (agent) =>
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

  const findWorkflowDefinition = useCallback(
    (workflow) =>
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
    const definition =
      catalog.agents.find((item) => item.id === nextAgentType) || catalog.agents[0];
    if (!definition) {
      return;
    }
    const nextId = `agent_${(draftConfig?.agents || []).length + 1}`;
    updateDraft((currentDraft) => ({
      ...currentDraft,
      agents: [...(currentDraft?.agents || []), buildAgentInstance(definition, (currentDraft?.agents || []).length)],
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

  const agentColumns = [
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
            const definition = catalog.agents.find((item) => item.id === definitionId);
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

  const workflowColumns = [
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
            const definition = catalog.workflows.find((item) => item.id === definitionId);
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

  const renderAgentInspector = () => {
    if (!selectedAgent) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectEntityHint')} />;
    }
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div className="inspector-section">
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
                onChange={(definitionId) => {
                  const definition = catalog.agents.find((item) => item.id === definitionId);
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
                }}
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
                options={(catalog.components || []).map((component) => {
                  const isCompatible = (selectedAgentDefinition?.compatible_components || []).includes(component.name);
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
                <Text type="warning" className="field-help">{t('incompatibleComponentWarning')}</Text>
              ) : null}
            </div>
          </div>
        </div>

        <div className="inspector-section">
          <Title level={5}>{t('definitionBinding')}</Title>
          <Descriptions size="small" column={1}>
            <Descriptions.Item label={t('source')}>
              <Tag>{selectedAgent.source || 'builtin'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('version')}>
              {selectedAgent.version || 'builtin'}
            </Descriptions.Item>
            <Descriptions.Item label={t('description')}>
              {selectedAgentDefinition?.description || '-'}
            </Descriptions.Item>
          </Descriptions>
        </div>

        <div className="inspector-section">
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
        </div>
      </Space>
    );
  };

  const renderWorkflowInspector = () => {
    if (!selectedWorkflow) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectEntityHint')} />;
    }
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div className="inspector-section">
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
                onChange={(definitionId) => {
                  const definition = catalog.workflows.find((item) => item.id === definitionId);
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
                }}
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
        </div>

        <div className="inspector-section">
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
        </div>

        <div className="inspector-section">
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
        </div>

        <div className="inspector-section">
          <Title level={5}>{t('taskBindings')}</Title>
          {(selectedWorkflowDefinition?.task_bindings || []).length ? (
            <Table
              size="small"
              pagination={false}
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
        </div>

        <div className="inspector-section">
          <Title level={5}>{t('transitions')}</Title>
          {(selectedWorkflowDefinition?.trigger_conditions || []).length ? (
            <Table
              size="small"
              pagination={false}
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
        </div>
      </Space>
    );
  };

  const renderNodeInspector = () => {
    if (!selectedNode) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('selectEntityHint')} />;
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
  };

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

      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
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

          <section className="panel-section">
            <div className="section-title-row">
              <Title level={5}>{t('studioAgents')}</Title>
              <Space wrap>
                <Select
                  value={nextAgentType}
                  style={{ width: 220 }}
                  options={agentOptions}
                  onChange={setNextAgentType}
                />
                <Button icon={<PlusOutlined />} onClick={addAgent}>
                  {t('addAgent')}
                </Button>
              </Space>
            </div>
            <Table
              size="small"
              loading={catalogLoading}
              className="studio-entity-table"
              rowKey={(row) => row.id}
              dataSource={draftConfig.agents || []}
              pagination={false}
              rowClassName={(row) => (row.id === selectedAgentId ? 'studio-row-selected' : '')}
              onRow={(row) => ({
                onClick: () => {
                  setSelectedAgentId(row.id);
                  setSelectedNodeId(`agent:${row.id}`);
                },
              })}
              columns={agentColumns}
            />
          </section>

          <section className="panel-section">
            <div className="section-title-row">
              <Title level={5}>{t('studioWorkflows')}</Title>
              <Space wrap>
                <Select
                  value={nextWorkflowType}
                  style={{ width: 220 }}
                  options={workflowOptions}
                  onChange={setNextWorkflowType}
                />
                <Button icon={<PlusOutlined />} onClick={addWorkflow}>
                  {t('addWorkflow')}
                </Button>
              </Space>
            </div>
            <Table
              size="small"
              loading={catalogLoading}
              className="studio-entity-table"
              rowKey={(row) => row.id}
              dataSource={draftConfig.workflows || []}
              pagination={false}
              rowClassName={(row) => (row.id === selectedWorkflowId ? 'studio-row-selected' : '')}
              onRow={(row) => ({
                onClick: () => {
                  setSelectedWorkflowId(row.id);
                  setSelectedNodeId(`workflow:${row.id}`);
                },
              })}
              columns={workflowColumns}
            />
          </section>
        </div>

        <div className="panel-stack">
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
              onNodeSelect={(nodeId) => {
                setSelectedNodeId(nodeId);
                if (String(nodeId).startsWith('agent:')) {
                  setSelectedAgentId(String(nodeId).replace('agent:', ''));
                }
                if (String(nodeId).startsWith('workflow:')) {
                  setSelectedWorkflowId(String(nodeId).replace('workflow:', ''));
                }
              }}
            />
          </section>

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
        </div>

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
            <pre className="inspector-json">
              {JSON.stringify(
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
              )}
            </pre>
          </div>
        </section>
      </div>
    </div>
  );
}

export default WorkflowStudioPage;
