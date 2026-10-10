import '@testing-library/jest-dom';
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

import { useWorkbench } from '../context/WorkbenchContext';
import { I18nProvider } from '../i18n/I18nProvider';
import { runApi } from '../services/workbenchApi';

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
  runApi: {
    listRuns: jest.fn(),
    getStatus: jest.fn(),
    getTrajectories: jest.fn(),
    getLogs: jest.fn(),
    deleteRun: jest.fn(),
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

const TrajectoriesLogsPage = require('./TrajectoriesLogsPage').default;

function renderPage() {
  return render(
    <I18nProvider>
      <TrajectoriesLogsPage />
    </I18nProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useWorkbench.mockReturnValue({
    authoritativeActionsEnabled: false,
    displayOnlyFallbackMode: true,
  });
  runApi.listRuns.mockResolvedValue([
    {
      run_id: 'run_1',
      status: 'COMPLETED',
      simulation_time: 24,
      updated_at: '2026-03-27T12:00:00Z',
    },
  ]);
  runApi.getStatus.mockResolvedValue({
    run_id: 'run_1',
    status: 'COMPLETED',
    simulation_time: 24,
    updated_at: '2026-03-27T12:00:00Z',
  });
  runApi.getTrajectories.mockResolvedValue({
    coordinate_mode: 'simulation_plane',
    trajectories: [
      {
        agent_id: 'delivery_drone_alpha',
        agent_type: 'DeliveryDroneAgent',
        points: [
          { x: 120, y: 20, z: 30 },
          { x: 180, y: 60, z: 30 },
        ],
      },
    ],
  });
  runApi.getLogs.mockResolvedValue([
    {
      id: 'log_1',
      level: 'info',
      event: 'MoveToComponent.task_completed',
      source: 'delivery_drone_alpha',
      task_name: 'Move to delivery point',
      workflow_id: 'logistics_alpha',
      message: 'completed',
      timestamp: '2026-03-27T12:00:05Z',
    },
  ]);
});

test('renders trajectory data and disables run deletion in offline display mode', async () => {
  renderPage();

  expect(await screen.findByText('Offline Display Mode')).toBeInTheDocument();

  await waitFor(() => {
    expect(screen.getByTestId('trajectories-map')).toHaveTextContent('markers:2;trajectories:1');
  });

  expect(screen.getByRole('button', { name: /Delete Run/i })).toBeDisabled();
  expect(screen.getByText('Move to delivery point')).toBeInTheDocument();
  expect(screen.getAllByText('logistics_alpha').length).toBeGreaterThan(0);
});
