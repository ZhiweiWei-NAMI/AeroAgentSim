// Isolated loopback server for the host mount example. Pattern attributed to
// the P02 parcel prototype server
// (validation/p02-parcel-host/source @ 15f473a4dc0ed4f80e3000b0acef6e875c617393,
// server.mjs, Apache-2.0 per the checkout's LICENSE), restricted to this
// owned host directory. It serves no private BENCH source: only files declared
// in this directory are reachable.
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 4407);
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error('PORT must be an integer from 1024 to 65535');
}
const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};
http.createServer(async (req, res) => {
  try {
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); return res.end(); }
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    const target = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
    const extension = path.extname(target);
    const insideRoot = target.startsWith(root + path.sep);
    const allowed = insideRoot && Boolean(types[extension]);
    if (!allowed || !(await stat(target)).isFile()) { res.writeHead(404); return res.end('Not found'); }
    res.writeHead(200, {
      'Content-Type': types[path.extname(target)] ?? 'application/octet-stream',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'self'; frame-ancestors 'self'",
    });
    res.end(req.method === 'HEAD' ? undefined : await readFile(target));
  } catch {
    res.writeHead(404); res.end('Not found');
  }
}).listen(port, '127.0.0.1', () => console.log(`P02 parcel host mount: http://localhost:${port} (loopback only)`));
