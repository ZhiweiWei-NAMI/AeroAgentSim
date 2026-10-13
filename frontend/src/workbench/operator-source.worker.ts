import { RunsApi } from '../feeds/http';
import { OperatorSource } from './operator-source';
import type { OperatorSourceReply, OperatorSourceRequest } from './operator-source-client';

let source: OperatorSource | undefined;
const reply = (message: OperatorSourceReply) => self.postMessage(message);
self.onmessage = (event: MessageEvent<OperatorSourceRequest>) => {
  void (async () => {
    const message = event.data;
    if (message.kind === 'start') {
      if (source) throw Error('Operator source was already started');
      source = new OperatorSource(new RunsApi(message.base), message.runId, {
        acknowledged: message.acknowledged,
        onReceipt: ns => reply({ kind: 'receipt', ns }),
        onError: error => reply({ kind: 'error', error }),
      });
      await source.start();
      return;
    }
    if (!source) throw Error('Operator source has not started');
    if (message.kind === 'hold') { await source.hold(); reply({ kind: 'held', id: message.id }); }
    else if (message.kind === 'resume') source.resume();
  })().catch(error => reply({ kind: 'fatal', error: String(error) }));
};
