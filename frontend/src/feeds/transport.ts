import { parseLosslessJson } from './lossless-json';

/**
 * One local-first HTTP policy for the browser clients.
 *
 * The browser console has no credential path for `AEROAGENTSIM_API_TOKEN`, so a
 * protected server is reported as unsupported rather than as an endless
 * connection attempt. Requests are single-shot: this module never retries a
 * state-changing POST. Feature readers own their own read-reconnect policy and
 * can use `isRetryableReadError` to keep retrying only transport failures.
 */
export type TransportErrorKind = 'authentication' | 'forbidden' | 'not_found' | 'unsupported' | 'network' | 'http';

/** The one honest answer for a protected server; shared with `ApiConnection`. */
export const BROWSER_TOKEN_NOTICE = {
  en: 'authentication required: browser token support is not available; use a local unprotected server',
  zh: '需要身份验证：浏览器不支持令牌；请使用本地未受保护的服务器',
} as const;

const FRIENDLY: Record<Exclude<TransportErrorKind, 'http'>, { en: string; zh: string }> = {
  authentication: BROWSER_TOKEN_NOTICE,
  forbidden: {
    en: 'request refused: the local server did not accept this browser origin; open the console from a local origin and use an unprotected local server',
    zh: '请求被拒绝：本地服务器不接受此浏览器来源；请从本地来源打开控制台，并使用未受保护的本地服务器',
  },
  not_found: {
    en: 'endpoint not found: this server does not expose the requested capability; check the API address and server version',
    zh: '未找到端点：此服务器不提供所请求的功能；请检查 API 地址和服务器版本',
  },
  unsupported: {
    en: 'capability not implemented: this server does not support the requested operation; use a newer server or avoid this action',
    zh: '功能尚未实现：此服务器不支持所请求的操作；请使用更新的服务器或避免此操作',
  },
  network: {
    en: 'cannot reach the local server: start it and check the API address; remote or protected deployments are not supported by this browser console',
    zh: '无法连接本地服务器：请启动服务器并检查 API 地址；此浏览器控制台不支持远程或受保护的部署',
  },
};

function classifyStatus(status: number): TransportErrorKind {
  if (status === 0) return 'network';
  if (status === 401) return 'authentication';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 501) return 'unsupported';
  return 'http';
}

/** A classified, actionable failure. `message` keeps the technical status for logs. */
export class TransportError extends Error {
  readonly status: number;
  readonly detail: string;
  /** Raw response text, retained for clients with their own detail policy. */
  readonly body: string;

  constructor(status: number, detail: string, body = detail) {
    super(status === 0 ? `Connection failed: ${detail || 'network request failed'}` : `HTTP ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
    this.body = body;
  }

  get kind(): TransportErrorKind { return classifyStatus(this.status); }
  /** The capability is absent on this server (not a generic client mistake). */
  get unavailable(): boolean { return this.status === 404 || this.status === 501; }
  get en(): string {
    const kind = this.kind;
    if (kind === 'http') return `request failed with HTTP ${this.status}: ${this.detail || 'no detail returned by the server'}`;
    return FRIENDLY[kind].en;
  }
  get zh(): string {
    const kind = this.kind;
    if (kind === 'http') return `请求失败（HTTP ${this.status}）：${this.detail || '服务器未返回详细信息'}`;
    return FRIENDLY[kind].zh;
  }
}

export function localizedTransportMessage(error: TransportError, locale = 'en-US'): string {
  return locale === 'zh-CN' ? error.zh : error.en;
}

/** 401 and 403 both mean this browser cannot be authenticated against this server. */
export function isAuthenticationError(error: unknown): boolean {
  if (error instanceof TransportError) return error.kind === 'authentication' || error.kind === 'forbidden';
  if (error && typeof error === 'object' && 'status' in error) {
    const status = (error as { status?: unknown }).status;
    return status === 401 || status === 403;
  }
  return error instanceof Error && /^HTTP (401|403)\b/.test(error.message);
}

/** Only reads may reconnect; a state-changing POST must fail on its first answer. */
export function isRetryableReadError(error: unknown): boolean {
  if (error instanceof TransportError) return error.kind === 'network' || (error.kind === 'http' && error.status >= 500);
  // Injected/legacy readers can still surface the raw platform failure; keep
  // their historical read-retry contract while the typed transport is normal.
  return error instanceof TypeError || /^Error: HTTP 5\d\d:/.test(String(error));
}

export function isAbortError(error: unknown): boolean {
  return (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError')
    || (error instanceof Error && error.name === 'AbortError');
}

export function requestUrl(base: string, path: string): string {
  const root = base.replace(/\/+$/, '');
  return `${root}${path.startsWith('/') ? path : `/${path}`}`;
}

/** Preserve a JSON `detail` when a server supplies one; otherwise keep the raw body. */
export function responseDetail(source: string): string {
  if (!source.trim()) return '';
  try {
    const parsed: unknown = JSON.parse(source);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail?: unknown }).detail;
      if (typeof detail === 'string') return detail;
      if (detail !== undefined && detail !== null) return JSON.stringify(detail);
    }
  } catch { /* Non-JSON server errors remain readable. */ }
  return source;
}

export function asNetworkError(error: unknown): TransportError {
  if (error instanceof TransportError) return error;
  return new TransportError(0, error instanceof Error ? error.message : String(error));
}

async function transportFetch(base: string, path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(requestUrl(base, path), init);
  } catch (error) {
    if (isAbortError(error) || init?.signal?.aborted) throw error;
    throw asNetworkError(error);
  }
}

/** Read the server's error detail without hiding the HTTP status. */
export async function transportErrorFromResponse(response: Response): Promise<TransportError> {
  let text = '';
  try { text = await response.text(); } catch { /* Keep the status even when the body cannot be read. */ }
  return new TransportError(response.status, responseDetail(text) || response.statusText || 'request failed', text);
}

export async function ensureResponseOk(response: Response): Promise<Response> {
  if (!response.ok) throw await transportErrorFromResponse(response);
  return response;
}

/** Fetch and fail with a classified error. A successful streaming body is untouched. */
export async function checkedFetch(base: string, path: string, init?: RequestInit): Promise<Response> {
  return ensureResponseOk(await transportFetch(base, path, init));
}

async function responseText(response: Response, signal?: AbortSignal): Promise<string> {
  try {
    return await response.text();
  } catch (error) {
    if (isAbortError(error) || signal?.aborted) throw error;
    throw asNetworkError(error);
  }
}

/**
 * One JSON request. `parseLosslessJson` is the default; authoring clients pass
 * `parseAuthoringJson` explicitly so behaviour numeric kinds keep their policy.
 */
export async function requestJson(
  base: string,
  path: string,
  init?: RequestInit,
  parse: (source: string) => unknown = parseLosslessJson,
): Promise<unknown> {
  const response = await checkedFetch(base, path, init);
  return parse(await responseText(response, init?.signal ?? undefined));
}

/** One raw-text request (YAML export), with the same classified failures. */
export async function requestText(base: string, path: string, init?: RequestInit): Promise<string> {
  const response = await checkedFetch(base, path, init);
  return responseText(response, init?.signal ?? undefined);
}

/** Read-only readiness probe used by `ApiConnection`; it never sends credentials. */
export async function probeConnection(base: string, signal?: AbortSignal, path = '/v1/templates'): Promise<void> {
  await requestText(base, path, signal ? { signal } : undefined);
}
