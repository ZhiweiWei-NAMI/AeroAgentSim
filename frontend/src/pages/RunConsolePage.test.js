import '@testing-library/jest-dom';
import React, { act } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { useWorkbench } from '../context/WorkbenchContext';
import { I18nProvider } from '../i18n/I18nProvider';
import {
  configApi,
  createWorkbenchSocket,
  runApi,
  systemApi,
} from '../services/workbenchApi';

vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal();
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

vi.mock('../context/WorkbenchContext', () => ({
  useWorkbench: vi.fn(),
}));

vi.mock('../components/workbench/Map2D', () => ({
  default: function MockMap2D(props) {
    return (
      <div data-testid={props.testId || 'map2d'}>
        {`markers:${props.markers?.length || 0};trajectories:${props.trajectories?.length || 0}`}
      </div>
    );
  },
}));

vi.mock('../services/workbenchApi', () => ({
  configApi: {
    getGraph: vi.fn(),
  },
  createWorkbenchSocket: vi.fn(),
  runApi: {
    listRuns: vi.fn(),
    getStatus: vi.fn(),
    getLogs: vi.fn(),
    getSpatial: vi.fn(),
    startRun: vi.fn(),
    pauseRun: vi.fn(),
    resumeRun: vi.fn(),
    resetRun: vi.fn(),
    deleteRun: vi.fn(),
  },
  systemApi: {
    getHealth: vi.fn(),
    resetRuntime: vi.fn(),
  },
}));

const matchMediaMock = vi.fn().mockImplementation((query) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn(),
}));

window.matchMedia = matchMediaMock;
global.matchMedia = matchMediaMock;
globalThis.matchMedia = matchMediaMock;

const { default: RunConsolePage } = await import('./RunConsolePage');

function renderPage() {
  return render(
    <I18nProvider>
      <RunConsolePage />
    </I18nProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useWorkbench.mockReturnValue({
    authoritativeActionsEnabled: true,
    draftConfig: { config_id: 'default' },
    saveDraft: vi.fn().mockResolvedValue({ config_id: 'default' }),
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
    return { close: vi.fn() };
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
