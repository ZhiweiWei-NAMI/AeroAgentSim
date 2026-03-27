import '@testing-library/jest-dom';
import React, { act } from 'react';
import { render, screen, waitFor } from '@testing-library/react';

import { useWorkbench } from '../context/WorkbenchContext';
import { I18nProvider } from '../i18n/I18nProvider';
import {
  configApi,
  createWorkbenchSocket,
  runApi,
  systemApi,
} from '../services/workbenchApi';

jest.mock('antd', () => {
  const actual = jest.requireActual('antd');
  const Descriptions = ({ children }) => <div>{children}</div>;
  Descriptions.Item = ({ label, children }) => (
    <div>
      <span>{label}</span>
      {children}
    </div>
  );
  return {
    ...actual,
    Descriptions,
    Table: ({ dataSource = [], columns = [] }) => (
      <div data-testid="mock-table">
        {dataSource.map((row, rowIndex) => (
          <div key={row.id || rowIndex}>
            {columns.map((column, columnIndex) => {
              const value = column.dataIndex ? row[column.dataIndex] : undefined;
              const content = column.render ? column.render(value, row, rowIndex) : value;
              return <div key={`${column.key || column.dataIndex || columnIndex}_${rowIndex}`}>{content}</div>;
            })}
          </div>
        ))}
      </div>
    ),
  };
});

jest.mock('../context/WorkbenchContext', () => ({
  useWorkbench: jest.fn(),
}));

jest.mock('../components/workbench/Map2D', () => function MockMap2D(props) {
  return (
    <div data-testid={props.testId || 'map2d'}>
      {`markers:${props.markers?.length || 0};trajectories:${props.trajectories?.length || 0}`}
    </div>
  );
});

jest.mock('../services/workbenchApi', () => ({
  configApi: {
    getGraph: jest.fn(),
  },
  createWorkbenchSocket: jest.fn(),
  runApi: {
    listRuns: jest.fn(),
    getStatus: jest.fn(),
    getLogs: jest.fn(),
    getSpatial: jest.fn(),
    startRun: jest.fn(),
    pauseRun: jest.fn(),
    resumeRun: jest.fn(),
    resetRun: jest.fn(),
    deleteRun: jest.fn(),
  },
  systemApi: {
    getHealth: jest.fn(),
    resetRuntime: jest.fn(),
  },
}));

const matchMediaMock = jest.fn().mockImplementation((query) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: jest.fn(),
  removeListener: jest.fn(),
  addEventListener: jest.fn(),
  removeEventListener: jest.fn(),
  dispatchEvent: jest.fn(),
}));

window.matchMedia = matchMediaMock;
global.matchMedia = matchMediaMock;
globalThis.matchMedia = matchMediaMock;

const RunConsolePage = require('./RunConsolePage').default;

function renderPage() {
  return render(
    <I18nProvider>
      <RunConsolePage />
    </I18nProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useWorkbench.mockReturnValue({
    authoritativeActionsEnabled: true,
    draftConfig: { config_id: 'default' },
    saveDraft: jest.fn().mockResolvedValue({ config_id: 'default' }),
  });
  systemApi.getHealth.mockResolvedValue({
    backend_available: true,
    simulation_status: 'RUNNING',
  });
  runApi.listRuns.mockResolvedValue([
    {
      run_id: 'run_1',
      status: 'RUNNING',
      config_id: 'default',
      simulation_time: 12,
      updated_at: '2026-03-27T12:00:00Z',
    },
  ]);
  runApi.getStatus.mockResolvedValue({
    run_id: 'run_1',
    status: 'RUNNING',
    config_id: 'default',
    simulation_time: 12,
    updated_at: '2026-03-27T12:00:00Z',
  });
  runApi.getLogs.mockResolvedValue([]);
  runApi.getSpatial.mockResolvedValue({
    coordinate_mode: 'simulation_plane',
    agents: [
      {
        id: 'delivery_drone_alpha',
        type: 'DeliveryDroneAgent',
        status: 'active',
        workflow_id: 'logistics_alpha',
        task_id: 'task_move',
        task_name: 'Move to delivery point',
        position: { x: 120, y: 20, z: 30 },
        recent_log: 'moving',
      },
    ],
  });
  configApi.getGraph.mockResolvedValue({ critical_path: [], warnings: [] });
});

test('reflects structured task success logs and live spatial updates for the selected run only', async () => {
  let socketHandlers = null;
  createWorkbenchSocket.mockImplementation((handlers) => {
    socketHandlers = handlers;
    handlers.onOpen?.();
    return { close: jest.fn() };
  });

  renderPage();

  await waitFor(() => {
    expect(screen.getByTestId('run-console-map')).toHaveTextContent('markers:1;trajectories:0');
  });
  expect(screen.getByText('Move to delivery point')).toBeInTheDocument();

  act(() => {
    socketHandlers.onLog?.({
      id: 'log_ignore',
      run_id: 'run_2',
      level: 'info',
      event: 'MoveToComponent.task_completed',
      status: 'completed',
      result: { status: 'completed' },
      task_name: 'Other run task',
      workflow_id: 'wf_other',
      message: 'ignore me',
    });
    socketHandlers.onLog?.({
      id: 'log_1',
      run_id: 'run_1',
      level: 'info',
      event: 'MoveToComponent.task_completed',
      status: 'completed',
      result: { status: 'completed' },
      task_name: 'Move to delivery point',
      workflow_id: 'logistics_alpha',
      message: 'completed',
    });
    socketHandlers.onSpatial?.({
      run_id: 'run_1',
      coordinate_mode: 'simulation_plane',
      agents: [
        {
          id: 'delivery_drone_alpha',
          type: 'DeliveryDroneAgent',
          status: 'idle',
          workflow_id: 'logistics_alpha',
          task_id: 'task_move',
          task_name: 'Move to delivery point',
          position: { x: 180, y: 60, z: 30 },
          recent_log: 'completed',
        },
      ],
    });
  });

  expect(screen.queryByText('Other run task')).not.toBeInTheDocument();
  expect(await screen.findByText('task ok')).toBeInTheDocument();
  expect(screen.getAllByText('logistics_alpha').length).toBeGreaterThan(0);
});
