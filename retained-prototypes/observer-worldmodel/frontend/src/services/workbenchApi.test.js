const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPut = jest.fn();
const mockDelete = jest.fn();

jest.mock('axios', () => ({
  create: jest.fn(() => ({
    get: mockGet,
    post: mockPost,
    put: mockPut,
    delete: mockDelete,
  })),
}));

function networkError(message = 'Network down') {
  const error = new Error(message);
  error.code = 'ERR_NETWORK';
  return error;
}

describe('workbenchApi fallback behavior', () => {
  beforeEach(() => {
    jest.resetModules();
    mockGet.mockReset();
    mockPost.mockReset();
    mockPut.mockReset();
    mockDelete.mockReset();
    window.localStorage.clear();
    delete global.WebSocket;
  });

  test('display GETs fallback to local config and supported workflow catalog entries only', async () => {
    mockGet.mockRejectedValue(networkError());

    const { catalogApi, configApi } = require('./workbenchApi');
    const [config, agents, workflows] = await Promise.all([
      configApi.getConfig('default'),
      catalogApi.getAgents(),
      catalogApi.getWorkflows(),
    ]);

    expect(config.config_id).toBe('default');
    expect(config.workflows).toHaveLength(1);
    expect(config.workflows[0].type).toBe('logistics_workflow');
    expect(agents.map((agent) => agent.id)).toContain('delivery_station');
    expect(workflows.map((workflow) => workflow.id)).toEqual([
      'inspection_workflow',
      'charging_workflow',
      'logistics_workflow',
      'image_processing_workflow',
    ]);
  });

  test('generic client exceptions do not trigger fallback', async () => {
    mockGet.mockRejectedValue(new Error('boom'));

    const { configApi } = require('./workbenchApi');

    await expect(configApi.getConfig('default')).rejects.toMatchObject({
      message: 'boom',
      isConnectivityError: false,
    });
  });

  test('run control does not fallback on network errors', async () => {
    mockPost.mockRejectedValue(networkError());

    const { runApi } = require('./workbenchApi');

    await expect(runApi.startRun('default')).rejects.toMatchObject({
      message: 'Network down',
      isConnectivityError: true,
    });
  });

  test('authoritative config and registry mutations stay strict on connectivity failures', async () => {
    mockPut.mockRejectedValue(networkError());
    mockPost.mockRejectedValue(networkError());
    mockDelete.mockRejectedValue(networkError());

    const { configApi, registryApi } = require('./workbenchApi');

    await expect(
      configApi.saveConfig('default', {
        config_id: 'default',
        name: 'Draft',
        coordinate_mode: 'simulation_plane',
        agents: [],
        workflows: [],
      })
    ).rejects.toMatchObject({ isConnectivityError: true });

    await expect(
      configApi.validate('default', {
        config_id: 'default',
        name: 'Draft',
        coordinate_mode: 'simulation_plane',
        agents: [],
        workflows: [],
      })
    ).rejects.toMatchObject({ isConnectivityError: true });

    await expect(
      registryApi.save('workflows', {
        id: 'custom_flow',
        version: '1.0.0',
        display_name: { en_US: 'Custom Flow', zh_CN: '' },
        description: { en_US: 'Custom workflow', zh_CN: '' },
        adapter_type: 'inspection',
        states: ['moving'],
        start_state: 'moving',
        property_templates: {},
        trigger_conditions: [],
        task_bindings: [],
        supported_agent_types: [],
      })
    ).rejects.toMatchObject({ isConnectivityError: true });

    await expect(
      registryApi.validate('workflows', 'custom_flow', {
        id: 'custom_flow',
        version: '1.0.0',
      })
    ).rejects.toMatchObject({ isConnectivityError: true });

    await expect(
      registryApi.delete('workflows', 'custom_flow', '1.0.0')
    ).rejects.toMatchObject({ isConnectivityError: true });
  });

  test('normalizes structured logs without flattening workflow and task metadata', async () => {
    mockGet.mockResolvedValue({
      data: [
        {
          id: 'log_1',
          run_id: 'run_1',
          source: 'agent_alpha',
          level: 'info',
          event: 'MoveToComponent.task_completed',
          message: 'task completed',
          time: 12,
          value: {
            task_id: 'task_1',
            task_name: 'Move to point',
            workflow_id: 'wf_1',
            task_class: 'MoveToTask',
            status: 'COMPLETED',
            result: {
              status: 'completed',
              distance: 20,
            },
          },
        },
      ],
    });

    const { runApi } = require('./workbenchApi');
    const logs = await runApi.getLogs('run_1');

    expect(logs).toHaveLength(1);
    expect(logs[0]).toMatchObject({
      source: 'agent_alpha',
      source_id: 'agent_alpha',
      event: 'MoveToComponent.task_completed',
      workflow_id: 'wf_1',
      task_id: 'task_1',
      task_name: 'Move to point',
      task_class: 'MoveToTask',
      status: 'completed',
    });
    expect(logs[0].result).toMatchObject({
      status: 'completed',
      distance: 20,
    });
  });

  test('normalizes spatial payloads with task ids and task names preserved separately', async () => {
    mockGet.mockResolvedValue({
      data: {
        run_id: 'run_1',
        coordinate_mode: 'simulation_plane',
        timestamp: 12,
        agents: [
          {
            agent_id: 'agent_alpha',
            agent_type: 'DroneAgent',
            status: 'ACTIVE',
            current_workflow: 'wf_1',
            current_task: 'Move to charger',
            current_task_id: 'task_1',
            position: [10, 20, 30],
            display_position: [10, 20, 30],
          },
        ],
      },
    });

    const { runApi } = require('./workbenchApi');
    const spatial = await runApi.getSpatial('run_1');

    expect(spatial.agents).toHaveLength(1);
    expect(spatial.agents[0]).toMatchObject({
      id: 'agent_alpha',
      workflow_id: 'wf_1',
      task_id: 'task_1',
      task_name: 'Move to charger',
      status: 'active',
      position: { x: 20, y: 10, z: 30 },
    });
  });

  test('normalizes structured HTTP response errors for run start', async () => {
    mockPost.mockRejectedValue({
      response: {
        status: 500,
        data: {
          detail: {
            detail: 'Simulation preflight failed.',
            run_id: 'run_failed',
            errors: ['ResourceManager: bad config'],
            recent_logs: [{ source: 'Preflight', message: 'bad config', level: 'error' }],
          },
        },
      },
    });

    const { runApi } = require('./workbenchApi');

    await expect(runApi.startRun('default')).rejects.toMatchObject({
      status: 500,
      runId: 'run_failed',
      errors: ['ResourceManager: bad config'],
    });
  });

  test('websocket dispatch preserves structured log payloads', () => {
    class MockSocket {
      constructor() {
        this.listeners = {};
      }

      addEventListener(name, callback) {
        this.listeners[name] = callback;
      }

      emit(name, payload) {
        this.listeners[name]?.(payload);
      }

      close() {}
    }

    global.WebSocket = jest.fn(() => new MockSocket());

    const handlers = {
      onStatus: jest.fn(),
      onLog: jest.fn(),
      onSpatial: jest.fn(),
      onWorkflowState: jest.fn(),
      onMessage: jest.fn(),
    };
    const { createWorkbenchSocket } = require('./workbenchApi');
    const socket = createWorkbenchSocket(handlers);

    socket.emit('message', {
      data: JSON.stringify({
        type: 'sim_status',
        run_id: 'run_1',
        simulation_time: 8,
      }),
    });
    socket.emit('message', {
      data: JSON.stringify({
        type: 'log_event',
        source: 'agent_alpha',
        event: 'MoveToComponent.task_completed',
        value: {
          task_id: 'task_1',
          workflow_id: 'wf_1',
          status: 'COMPLETED',
          result: { status: 'completed' },
        },
      }),
    });
    socket.emit('message', {
      data: JSON.stringify({
        type: 'spatial_snapshot',
        coordinate_mode: 'simulation_plane',
        agents: [{ agent_id: 'agent_alpha', position: [1, 2, 3], status: 'idle' }],
      }),
    });
    socket.emit('message', {
      data: JSON.stringify({
        type: 'workflow_state_diff',
        workflow_id: 'wf_1',
        state: 'moving',
      }),
    });
    socket.emit('message', { data: 'plain text' });

    expect(handlers.onStatus).toHaveBeenCalledWith(
      expect.objectContaining({ run_id: 'run_1', simulation_time: 8 })
    );
    expect(handlers.onLog).toHaveBeenCalledWith(
      expect.objectContaining({
        source: 'agent_alpha',
        event: 'MoveToComponent.task_completed',
        workflow_id: 'wf_1',
        task_id: 'task_1',
        status: 'completed',
      })
    );
    expect(handlers.onSpatial).toHaveBeenCalledWith(
      expect.objectContaining({
        agents: [expect.objectContaining({ id: 'agent_alpha' })],
      })
    );
    expect(handlers.onWorkflowState).toHaveBeenCalledWith(
      expect.objectContaining({ workflow_id: 'wf_1', state: 'moving' })
    );
    expect(handlers.onMessage).toHaveBeenCalledWith('plain text');
  });
});
