import { describe, expect, it } from 'vitest';
import { canonicaliseRecords, type AgentRecordInput } from './records';

function original(decisionId: string, id: string, phase: string, data: unknown, cut = 1, sequence = 0): AgentRecordInput {
  return {
    id,
    schemaId: 'aas.langgraph.record',
    atNs: `${cut}000000000`,
    cut,
    sequence,
    payload: { decision_id: decisionId, phase, data_json: JSON.stringify(data) },
  };
}

function alias(decisionId: string, id: string, source: string, phase: string, data: object, cut = 1, sequence = 1): AgentRecordInput {
  return {
    id,
    schemaId: 'aas.agent.record',
    atNs: `${cut}000000000`,
    cut,
    sequence,
    payload: {
      decision_id: decisionId,
      phase,
      data_json: JSON.stringify({ ...(data as Record<string, unknown>), source_schema: 'aas.langgraph.record', source_message_id: source }),
    },
  };
}

const fields = [{ entity: { id: 'uav.bravo' }, field: 'traffic.actor.role', status: 'known', value: 'candidate' }];

describe('canonicaliseRecords', () => {
  it('collapses a projected alias into its original and keeps both raw ids', () => {
    const observation = { initial: { observation: { fields } } };
    const timeline = canonicaliseRecords([
      alias('decision-1', 'record.raw/console/0', 'record.raw', 'observation', { fields, model: 'fixture-model', pin: { factory: 'fixture' } }, 2, 2),
      original('decision-1', 'record.raw', 'observation', observation, 2, 1),
    ], { entities: new Set(['uav.bravo']) });

    expect(timeline.problems).toEqual([]);
    expect(timeline.decisions).toHaveLength(1);
    const decision = timeline.decisions[0];
    expect(decision.phases.map(phase => phase.phase)).toEqual(['observation']);
    const phase = decision.phases[0];
    expect(phase.recordId).toBe('record.raw');
    expect(phase.originalId).toBe('record.raw');
    expect(phase.sourceMessageId).toBe('record.raw');
    expect(phase.aliasIds).toEqual(['record.raw/console/0']);
    expect(phase.rawIds).toEqual(['record.raw', 'record.raw/console/0']);
    expect(phase.data).toEqual(observation);
    expect(phase.views).toHaveLength(1);
    expect(phase.views[0]).toMatchObject({
      recordId: 'record.raw/console/0',
      sourceMessageId: 'record.raw',
      phase: 'observation',
    });
    expect(decision.fields[0]).toMatchObject({ name: 'uav.bravo.traffic.actor.role', entity: 'uav.bravo' });
    expect(decision.counts).toMatchObject({ records: 1, observations: 1, derived: 1 });
  });

  it('counts one model call although prompt and response views were projected', () => {
    const call = {
      identity: [1, 'uav_bravo', 'accept', 0],
      request: { messages: [], model: 'fixture-model' },
      response: { message: { content: '{"accept":true}' } },
      usage: { completion_tokens: 3 },
    };
    const proposal = { kind: 'event', schema: 'traffic.proposal.bid', payload: { accept: true } };
    const timeline = canonicaliseRecords([
      alias('decision-1', 'call.raw/console/0', 'call.raw', 'prompt', { model_call: call.identity, request: call.request }, 3, 2),
      alias('decision-1', 'call.raw/console/1', 'call.raw', 'response', call, 3, 3),
      original('decision-1', 'call.raw', 'model_call', call, 3, 1),
      original('decision-1', 'output.raw', 'output', proposal, 3, 4),
      alias('decision-1', 'output.raw/console/0', 'output.raw', 'validation', proposal, 3, 5),
      original('decision-1', 'receipt.raw', 'receipt', { command_id: 'cmd-1', call_id: 'call-1', status: 'succeeded' }, 3, 6),
    ]);

    expect(timeline.problems).toEqual([]);
    const decision = timeline.decisions[0];
    expect(decision.phases.map(phase => phase.phase)).toEqual(['model_call', 'output', 'receipt']);
    expect(decision.counts).toMatchObject({ records: 3, modelCalls: 1, outputs: 1, receipts: 1, derived: 3 });
    expect(decision.modelCalls).toHaveLength(1);
    expect(decision.modelCalls[0].views.map(view => view.phase)).toEqual(['prompt', 'response']);
    expect(decision.modelCalls[0].data).toEqual(call);
    expect(decision.outputs).toEqual([proposal]);
    expect(decision.receipts).toMatchObject([{ commandId: 'cmd-1', callId: 'call-1', status: 'succeeded' }]);
  });

  it('counts two genuinely distinct model calls separately', () => {
    const timeline = canonicaliseRecords([
      original('decision-1', 'call-a', 'model_call', { identity: [0, 'uav_bravo', 'first', 0], request: {} }, 1, 0),
      original('decision-1', 'call-b', 'model_call', { identity: [1, 'uav_bravo', 'second', 0], request: {} }, 1, 1),
    ]);

    expect(timeline.decisions[0].phases.map(phase => phase.recordId)).toEqual(['call-a', 'call-b']);
    expect(timeline.decisions[0].counts.modelCalls).toBe(2);
  });

  it('orders decisions and phases by cut then sequence, not arrival order', () => {
    const timeline = canonicaliseRecords([
      original('decision-a', 'finish', 'finished', { result: {} }, 3, 0),
      original('decision-a', 'observe', 'observation', { initial: { observation: { fields } } }, 1, 5),
      original('decision-a', 'call', 'model_call', { identity: [0, 'n', 'k', 0], request: {} }, 1, 2),
      original('decision-b', 'other', 'observation', { initial: { observation: { fields } } }, 0, 9),
    ], { entities: new Set(['uav.bravo']) });

    expect(timeline.decisions.map(decision => decision.decisionId)).toEqual(['decision-b', 'decision-a']);
    expect(timeline.decisions[1].phases.map(phase => [phase.recordId, phase.cut, phase.sequence])).toEqual([
      ['call', 1, 2],
      ['observe', 1, 5],
      ['finish', 3, 0],
    ]);
    expect(timeline.decisions[1].phases.map(phase => phase.simNs)).toEqual(['1000000000', '1000000000', '3000000000']);
  });

  it('tolerates unknown payload and data fields instead of rejecting the record', () => {
    const timeline = canonicaliseRecords([{
      id: 'future.raw',
      schemaId: 'aas.langgraph.record',
      atNs: '7000000000',
      cut: 7,
      sequence: 1,
      payload: {
        decision_id: 'decision-future',
        phase: 'model_call',
        data_json: JSON.stringify({ identity: [0, 'n', 'k', 0], request: {}, response: {}, future: { nested: true } }),
        future_payload: { retained: true },
      },
    }]);

    expect(timeline.problems).toEqual([]);
    const phase = timeline.decisions[0].phases[0];
    expect(phase.data).toMatchObject({ future: { nested: true } });
    expect(phase.payload).toMatchObject({ future_payload: { retained: true } });
    expect(phase.aliasIds).toEqual([]);
    expect(phase.rawIds).toEqual(['future.raw']);
  });

  it('keeps an alias inspectable when its original is outside the stream', () => {
    const timeline = canonicaliseRecords([{
      id: 'missing.raw/console/1',
      schemaId: 'aas.agent.record',
      atNs: '4000000000',
      cut: 4,
      sequence: 1,
      payload: {
        decision_id: 'decision-1',
        phase: 'response',
        data_json: JSON.stringify({ response: { message: { content: 'recorded' } }, source_message_id: 'missing.raw' }),
      },
    }]);

    expect(timeline.problems).toEqual([]);
    const phase = timeline.decisions[0].phases[0];
    expect(phase.phase).toBe('model_call');
    expect(phase.recordId).toBe('missing.raw');
    expect(phase.originalId).toBeUndefined();
    expect(phase.aliasIds).toEqual(['missing.raw/console/1']);
    expect(phase.rawIds).toEqual(['missing.raw', 'missing.raw/console/1']);
    expect(phase.views[0].phase).toBe('response');
    expect(timeline.decisions[0].counts).toMatchObject({ modelCalls: 1, derived: 1 });
  });

  it('reports an unknown phase as a problem without hiding valid records', () => {
    const timeline = canonicaliseRecords([
      original('decision-1', 'valid', 'finished', { result: {} }, 1, 0),
      { ...original('decision-1', 'invented', 'invented', {}, 1, 1) },
    ]);

    expect(timeline.problems).toHaveLength(1);
    expect(timeline.problems[0]).toContain('unknown phase');
    expect(timeline.decisions[0].phases.map(phase => phase.recordId)).toEqual(['valid']);
  });

  it('keeps proposed commands and actual receipts distinct', () => {
    const timeline = canonicaliseRecords([
      original('decision-1', 'command.raw', 'command', {
        call_id: 'call-1', schema: 'traffic.command', target: 'uav.bravo', payload: { go: true }, decision_summary: 'Go',
      }, 1, 0),
      original('decision-1', 'receipt.raw', 'receipt', {
        command_id: 'cmd-1', call_id: 'call-1', status: 'succeeded', result: { ok: true },
      }, 1, 1),
    ]);

    const decision = timeline.decisions[0];
    expect(decision.calls.map(call => call.callId)).toEqual(['call-1']);
    expect(decision.receipts.map(receipt => receipt.commandId)).toEqual(['cmd-1']);
    expect(decision.outputs).toEqual([]);
    expect(decision.counts).toMatchObject({ calls: 1, receipts: 1, outputs: 0 });
  });
});
