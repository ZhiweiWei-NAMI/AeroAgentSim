import {readFile,readdir,access} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
for(const file of [...(await readdir(path.join(root,'src'))).filter(x=>x.endsWith('.js')).map(x=>'src/'+x),'server.mjs']){
  const absolute=path.join(root,file);execFileSync(process.execPath,['--check',absolute]);
  const source=await readFile(absolute,'utf8');
  for(const match of source.matchAll(/from\s+['"](\.[^'"]+)['"]/g))await access(path.resolve(path.dirname(absolute),match[1]));
}
const html=await readFile(path.join(root,'index.html'),'utf8');
for(const match of html.matchAll(/(?:href|src)="(\.[^"]+)"/g))await access(path.resolve(root,match[1]));
console.log('Syntax, local imports and HTML assets verified.');
