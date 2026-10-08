import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || '/api';
export const WS_BASE_URL =
  process.env.REACT_APP_WS_BASE_URL ||
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 10000,
});

import { normalizeLog } from './state-shapes';
const CONNECTIVITY_ERROR_CODES = new Set(['ERR_NETWORK', 'ECONNABORTED', 'ETIMEDOUT']);

function extractErrorPayload(data) {
  const candidate =
    data?.detail && typeof data.detail === 'object' && !Array.isArray(data.detail)
      ? data.detail
      : data;
  const detailValue =
    typeof data?.detail === 'string'
      ? data.detail
      : candidate?.detail || candidate?.message || '';
  return {
    detail: detailValue,
    errors: candidate?.errors || [],
    warnings: candidate?.warnings || [],
    checks: candidate?.checks || [],
    runId: candidate?.run_id || candidate?.runId || null,
    recentLogs: candidate?.recent_logs || candidate?.recentLogs || [],
    raw: candidate,
  };
}

function isConnectivityFailure(error, response) {
  if (response) {
    return false;
  }
  const code = String(error?.code || error?.cause?.code || '').toUpperCase();
  if (CONNECTIVITY_ERROR_CODES.has(code)) {
    return true;
  }
  if (error?.request) {
    return true;
  }
  return /network|timeout|failed to fetch|load failed/i.test(String(error?.message || ''));
}

export function normalizeApiError(error) {
  const response = error?.response;
  const payload = extractErrorPayload(response?.data);
  const message = payload.detail || error?.message || 'Request failed';
  const normalized = new Error(message);
  normalized.name = 'WorkbenchApiError';
  normalized.status = response?.status || null;
  normalized.detail = payload.raw;
  normalized.errors = payload.errors;
  normalized.warnings = payload.warnings;
  normalized.checks = payload.checks;
  normalized.runId = payload.runId;
  normalized.recentLogs = (Array.isArray(payload.recentLogs) ? payload.recentLogs : []).map((item, index) =>
    normalizeLog(item, index)
  );
  normalized.isConnectivityError = isConnectivityFailure(error, response);
  normalized.response = response;
  normalized.originalError = error;
  return normalized;
}

export async function requestStrict(request) {
  try {
    return await request();
  } catch (error) {
    throw normalizeApiError(error);
  }
}

