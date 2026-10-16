import { expect, it } from 'vitest';
import { assistantErrorText, nonLocalHost } from './errors';
import { ApiError } from '../workbench/api';

it('maps the request guard 403 to the tunnel copy with the page host', () => {
  const problem = new ApiError(403, 'Untrusted request Host or Origin');
  expect(assistantErrorText(problem, false, { hostname: '100.64.131.244', port: '18977' })).toContain('ssh -N -L 18977:localhost:18977 100.64.131.244');
  expect(assistantErrorText(problem, true, { hostname: '100.64.131.244', port: '18977' })).toContain('ssh -N -L 18977:localhost:18977 100.64.131.244');
  expect(assistantErrorText(problem, true, { hostname: '100.64.131.244', port: '18977' })).toContain('AEROAGENTSIM_CONSOLE_URL');
});

it('maps 401 to the token copy and keeps the server detail otherwise', () => {
  expect(assistantErrorText(new ApiError(401, 'Bearer token required'), false)).toBe('This server requires an API token.');
  expect(assistantErrorText(new ApiError(401, 'Bearer token required'), true)).toBe('此服务器需要 API 令牌。');
  expect(assistantErrorText(new ApiError(500, 'Backend exploded'), false)).toBe('Backend exploded');
  expect(assistantErrorText(new ApiError(500, ''), false)).toBe('Request failed (HTTP 500).');
  expect(assistantErrorText(new ApiError(500, ''), true)).toBe('请求失败（HTTP 500）。');
});

it('detects a non-localhost page including bracketed IPv6 loopback', () => {
  expect(nonLocalHost({ hostname: 'localhost' })).toBe(false);
  expect(nonLocalHost({ hostname: '127.0.0.1' })).toBe(false);
  expect(nonLocalHost({ hostname: '100.64.131.244' })).toBe(true);
  expect(nonLocalHost({ hostname: '[::1]' })).toBe(false);
});
