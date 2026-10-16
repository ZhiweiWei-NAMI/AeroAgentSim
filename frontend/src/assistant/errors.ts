import { ApiError } from '../workbench/api';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

/** The server guard only trusts requests made from the machine itself (security.py). */
export function nonLocalHost(loc: Location | { hostname: string } = globalThis.location): boolean {
  return !!loc.hostname && !LOCAL_HOSTS.has(loc.hostname);
}

/** Map a failed assistant request to copy a user can act on, in either locale. */
export function assistantErrorText(problem: unknown, zh: boolean, loc?: Location | { hostname: string; port?: string; protocol?: string }): string {
  if (problem instanceof ApiError) {
    if (problem.status === 403 && problem.detail.includes('Untrusted request Host')) {
      const page = loc ?? globalThis.location;
      const protocol = page.protocol || 'http:';
      const port = page.port || (protocol === 'https:' ? '443' : '80');
      const tunnel = `ssh -N -L ${port}:localhost:${port} ${page.hostname.replace(/^\[|\]$/g, '')}`;
      return zh
        ? `此服务器只接受本机发起的修改。请通过 SSH 隧道访问（${tunnel}，然后打开 ${protocol}//localhost:${port}），或请运维将 AEROAGENTSIM_CONSOLE_URL 设为当前页面的来源地址（协议、主机和端口）。`
        : `This server only accepts changes from its own machine. Open it through an SSH tunnel (${tunnel}, then ${protocol}//localhost:${port}), or ask the operator to set AEROAGENTSIM_CONSOLE_URL to this page's origin (scheme, host and port).`;
    }
    if (problem.status === 401) return zh ? '此服务器需要 API 令牌。' : 'This server requires an API token.';
    if (problem.status === 415) return zh ? '服务器要求以 application/json 格式发送请求，请刷新页面后重试。' : 'The server requires application/json requests. Reload the page and retry.';
    return problem.detail || (zh ? `请求失败（HTTP ${problem.status}）。` : `Request failed (HTTP ${problem.status}).`);
  }
  return String(problem);
}
