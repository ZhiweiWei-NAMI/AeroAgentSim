import http from 'node:http';
import {readFile,stat,realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {gzipSync} from 'node:zlib';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const port=Number(process.env.PORT||4333),host='127.0.0.1';
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.md':'text/plain; charset=utf-8'};
const compressed=new Map();
export const server=http.createServer(async(req,res)=>{
 try{
  if(!['GET','HEAD'].includes(req.method)){res.writeHead(405,{Allow:'GET, HEAD'});res.end('Read only');return;}
  let pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  if(pathname==='/'){res.writeHead(302,{Location:'/ui/'});res.end();return;}
  if(pathname.endsWith('/'))pathname+='index.html';
  if(!['/ui/','/data/','/spec/','/review/'].some(prefix=>pathname.startsWith(prefix))){res.writeHead(404);res.end('Not found');return;}
  const file=path.resolve(root,'.'+pathname),resolved=await realpath(file);
  if(!resolved.startsWith(root+path.sep)){res.writeHead(403);res.end('Forbidden');return;}
  const st=await stat(resolved);if(!st.isFile())throw new Error('not file');
  const headers={'Content-Type':types[path.extname(file)]||'application/octet-stream','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Vary':'Accept-Encoding'};
  let bytes;
  if(st.size>1024&&/\bgzip\b/.test(req.headers['accept-encoding']||'')){
   const cacheKey=resolved+':'+st.mtimeMs;if(!compressed.has(cacheKey)){if(compressed.size>500)compressed.clear();compressed.set(cacheKey,gzipSync(await readFile(resolved),{level:6}));}bytes=compressed.get(cacheKey);headers['Content-Encoding']='gzip';
  }else bytes=await readFile(resolved);
  headers['Content-Length']=bytes.length;res.writeHead(200,headers);res.end(req.method==='HEAD'?undefined:bytes);
 }catch{res.writeHead(404);res.end('Not found');}
});
server.listen(port,host,()=>process.stdout.write(`P08 private native star graph: http://${host}:${port}/ui/\nRead only, loopback only; no telemetry or external runtime assets.\n`));
