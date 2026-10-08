import { apiClient, requestStrict } from './client';
import { flattenRegistryDefinition, toRegistryPayload, normalizeCatalogItem, normalizeCompatibilityMatrix } from './registry-shapes';
import { normalizeConfig, serializeConfigForApi, normalizeValidation, normalizePreflight, normalizeGraph } from './config-shapes';
import { normalizeRun, normalizeHealth, normalizeLog, normalizeSpatialPayload, normalizeTrajectoryPayload } from './state-shapes';
export const DEFAULT_CONFIG_ID = 'default';
/** Deprecated name retained for existing page imports; no mock data is returned. */
export const MOCK_CONFIG_ID = DEFAULT_CONFIG_ID;
function requireList(data) {
  if (!Array.isArray(data)) throw Error('Service response must contain a real list');
  return data;
}

export const catalogApi = {
  async getAgents(scope = 'all') {
    const data = await requestStrict(
      () => apiClient.get('/catalog/agents', { params: { scope } }).then((response) => response.data)
    );
    return requireList(data).map((item) => normalizeCatalogItem(item, 'agents'));
  },
  async getComponents() {
    const data = await requestStrict(
      () => apiClient.get('/catalog/components').then((response) => response.data)
    );
    return requireList(data).map((item) => normalizeCatalogItem(item, 'components'));
  },
  async getTasks(scope = 'all') {
    const data = await requestStrict(
      () => apiClient.get('/catalog/tasks', { params: { scope } }).then((response) => response.data)
    );
    return requireList(data).map((item) => normalizeCatalogItem(item, 'tasks'));
  },
  async getWorkflows(scope = 'all') {
    const data = await requestStrict(
      () => apiClient.get('/catalog/workflows', { params: { scope } }).then((response) => response.data)
    );
    return requireList(data).map((item) => normalizeCatalogItem(item, 'workflows'));
  },
  async getCompatibility(scope = 'all') {
    const data = await requestStrict(
      () => apiClient.get('/catalog/compatibility', { params: { scope } }).then((response) => response.data)
    );
    return normalizeCompatibilityMatrix(data);
  },
};

export const registryApi = {
  async list(kind) {
    const data = await requestStrict(
      () => apiClient.get(`/registry/${kind}`).then((response) => response.data)
    );
    return requireList(data).map((item) => flattenRegistryDefinition(kind, item));
  },
  async get(kind, definitionId, version) {
    const data = await requestStrict(
      () => apiClient.get(`/registry/${kind}/${definitionId}`, { params: { version } }).then((response) => response.data)
    );
    return data ? flattenRegistryDefinition(kind, data) : null;
  },
  async save(kind, payload) {
    const normalized = toRegistryPayload(kind, payload);
    const data = await requestStrict(() =>
      apiClient.post(`/registry/${kind}`, normalized).then((response) => response.data)
    );
    return flattenRegistryDefinition(kind, data);
  },
  async delete(kind, definitionId, version) {
    return requestStrict(() =>
      apiClient.delete(`/registry/${kind}/${definitionId}`, { params: { version } }).then((response) => response.data)
    );
  },
  async validate(kind, definitionId, payload, version) {
    const normalized = payload ? toRegistryPayload(kind, payload) : undefined;
    const data = await requestStrict(() =>
      apiClient
        .post(`/registry/${kind}/${definitionId}/validate`, normalized, { params: { version } })
        .then((response) => response.data)
    );
    return data;
  },
};

export const configApi = {
  async getConfig(configId = MOCK_CONFIG_ID) {
    const data = await requestStrict(
      () => apiClient.get(`/configs/${configId}`).then((response) => response.data)
    );
    return normalizeConfig(data);
  },
  async saveConfig(configId, configData) {
    const payload = serializeConfigForApi(configData);
    const data = await requestStrict(() =>
      apiClient.put(`/configs/${configId}`, payload).then((response) => response.data)
    );
    return normalizeConfig(data);
  },
  async validate(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestStrict(() =>
      apiClient
        .post(`/configs/${configId}/validate`, payload)
        .then((response) => response.data)
    );
    return normalizeValidation(data);
  },
  async preflight(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestStrict(() =>
      apiClient
        .post(`/configs/${configId}/preflight`, payload)
        .then((response) => response.data)
    );
    return normalizePreflight(data);
  },
  async getGraph(configId = MOCK_CONFIG_ID, configData) {
    const payload = configData ? serializeConfigForApi(configData) : undefined;
    const data = await requestStrict(
      () =>
        payload
          ? apiClient.post(`/configs/${configId}/graph`, payload).then((response) => response.data)
          : apiClient.get(`/configs/${configId}/graph`).then((response) => response.data)
    );
    return normalizeGraph(data);
  },
};

export const runApi = {
  async listRuns() {
    const data = await requestStrict(() => apiClient.get('/runs').then((response) => response.data));
    return requireList(data).map((run) => normalizeRun(run)).filter(Boolean);
  },
  async startRun(configId = MOCK_CONFIG_ID) {
    const data = await requestStrict(() =>
      apiClient.post('/runs', { config_id: configId }).then((response) => response.data)
    );
    return normalizeRun(data);
  },
  async getStatus(runId) {
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/status`).then((response) => response.data)
    );
    return normalizeRun(data);
  },
  async pauseRun(runId) {
    return requestStrict(() => apiClient.post(`/runs/${runId}/pause`).then((response) => response.data));
  },
  async resumeRun(runId) {
    return requestStrict(() => apiClient.post(`/runs/${runId}/resume`).then((response) => response.data));
  },
  async resetRun(runId) {
    return requestStrict(() => apiClient.post(`/runs/${runId}/reset`).then((response) => response.data));
  },
  async deleteRun(runId) {
    return requestStrict(() => apiClient.delete(`/runs/${runId}`).then((response) => response.data));
  },
  async getLogs(runId) {
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/logs`).then((response) => response.data)
    );
    return requireList(data).map((item, index) => normalizeLog(item, index));
  },
  async getSpatial(runId) {
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/spatial`).then((response) => response.data)
    );
    return normalizeSpatialPayload(data);
  },
  async getTrajectories(runId) {
    const run = await this.getStatus(runId);
    const data = await requestStrict(() =>
      apiClient.get(`/runs/${runId}/trajectories`).then((response) => response.data)
    );
    return normalizeTrajectoryPayload(data, run?.coordinate_mode);
  },
};

export const systemApi = {
  async getHealth() {
    const data = await requestStrict(() => apiClient.get('/health').then((response) => response.data));
    return normalizeHealth(data);
  },
  async resetRuntime() {
    return requestStrict(() => apiClient.post('/runtime/reset').then((response) => response.data));
  },
};
