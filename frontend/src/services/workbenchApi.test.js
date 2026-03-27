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

describe('workbenchApi fallback behavior', () => {
  beforeEach(() => {
    jest.resetModules();
    mockGet.mockReset();
    mockPost.mockReset();
    mockPut.mockReset();
    mockDelete.mockReset();
    window.localStorage.clear();
  });

  test('falls back to local config data for network errors', async () => {
    mockGet.mockRejectedValue(new Error('Network down'));

    const { configApi } = require('./workbenchApi');
    const config = await configApi.getConfig('default');

    expect(config.config_id).toBe('default');
    expect(config.agents).toHaveLength(1);
  });

  test('run control does not fallback on network errors', async () => {
    mockPost.mockRejectedValue(new Error('Network down'));

    const { runApi } = require('./workbenchApi');

    await expect(runApi.startRun('default')).rejects.toMatchObject({
      message: 'Network down',
      isConnectivityError: true,
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
});
