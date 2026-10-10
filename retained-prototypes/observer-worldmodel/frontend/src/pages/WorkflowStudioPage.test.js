jest.mock('axios', () => ({
  create: jest.fn(() => ({
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  })),
}));

const { buildWorkflowInstance } = require('./WorkflowStudioPage');

describe('WorkflowStudioPage workflow starters', () => {
  test('buildWorkflowInstance seeds runnable logistics defaults from available agents', () => {
    const definition = {
      id: 'logistics_workflow',
      name: 'LogisticsWorkflow',
      source: 'builtin',
      version: 'builtin',
      property_templates: {
        pickup_location: { value_type: 'list', required: true },
        delivery_location: { value_type: 'list', required: true },
        payloads: { value_type: 'list', required: true },
        source_agent_id: { value_type: 'str', required: true },
        target_agent_id: { value_type: 'str', required: true },
      },
    };
    const agents = [
      {
        id: 'station_source',
        type: 'delivery_station',
        initial_position: [20, 40, 0],
      },
      {
        id: 'station_target',
        type: 'delivery_station',
        initial_position: [240, 180, 0],
      },
      {
        id: 'delivery_drone_alpha',
        type: 'delivery_drone_agent',
        initial_position: [120, 20, 30],
      },
    ];

    const workflow = buildWorkflowInstance(definition, agents, 1);

    expect(workflow.agent_id).toBe('delivery_drone_alpha');
    expect(workflow.properties).toEqual({
      pickup_location: [20, 40, 30],
      delivery_location: [240, 180, 30],
      payloads: [
        {
          id: 'payload_2',
          description: 'Starter logistics payload',
        },
      ],
      source_agent_id: 'station_source',
      target_agent_id: 'station_target',
    });
  });
});
