import { WS_BASE_URL } from './client';
import { normalizeLog, normalizeSpatialPayload } from './state-shapes';

export function createWorkbenchSocket(handlers = {}) {
  const socket = new WebSocket(WS_BASE_URL);
  socket.addEventListener('open', () => handlers.onOpen?.());
  socket.addEventListener('close', () => handlers.onClose?.());
  socket.addEventListener('error', event => { handlers.onError?.(event); handlers.onClose?.(); });
  socket.addEventListener('message', event => {
    let payload;
    try { payload = JSON.parse(event.data); }
    catch { handlers.onMessage?.(event.data); return; }
    switch (payload.type) {
      case 'sim_status': handlers.onStatus?.({ ...payload, simulation_time: payload.simulation_time ?? payload.time }); break;
      case 'log_event': handlers.onLog?.(normalizeLog(payload)); break;
      case 'spatial_snapshot': handlers.onSpatial?.(normalizeSpatialPayload(payload)); break;
      case 'workflow_state_diff': handlers.onWorkflowState?.(payload); break;
      default: handlers.onMessage?.(payload);
    }
  });
  return socket;
}
