import http from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const port=Number(process.env.PORT||4318),host='127.0.0.1';
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.md':'text/plain; charset=utf-8'};
export const server=http.createServer(async(req,res)=>{try{let pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);if(pathname==='/'){res.writeHead(302,{Location:'/ui/'});res.end();return;}if(pathname.endsWith('/'))pathname+='index.html';const file=path.resolve(root,'.'+pathname);if(!file.startsWith(root+path.sep)){res.writeHead(403);res.end('Forbidden');return;}if(!['/ui/','/data/','/spec/','/review/'].some(prefix=>pathname.startsWith(prefix))){res.writeHead(404);res.end('Not found');return;}const st=await stat(file);if(!st.isFile())throw new Error('not file');res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Length':st.size});if(req.method==='HEAD')res.end();else res.end(await readFile(file));}catch{res.writeHead(404);res.end('Not found');}});
server.listen(port,host,()=>process.stdout.write(`P08 private local graph explorer: http://${host}:${port}/ui/\nNo external services, dispatch or telemetry.\n`));
