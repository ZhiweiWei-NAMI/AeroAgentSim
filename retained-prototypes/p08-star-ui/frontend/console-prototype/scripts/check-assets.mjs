import {readFile,readdir,stat} from 'node:fs/promises';
import path from 'node:path';
const root=process.cwd();let count=0;
async function checkFile(file){const content=await readFile(file,'utf8');for(const match of content.matchAll(/(?:from\s+|import\s*)['"](\.[^'"]+)['"]/g)){const target=path.resolve(path.dirname(file),match[1]);if(!target.startsWith(root+path.sep))throw new Error(`Import outside project: ${target}`);if(!(await stat(target)).isFile())throw new Error(`Missing module ${target}`);count++;}}
for(const file of await readdir('src'))if(file.endsWith('.js'))await checkFile(path.join(root,'src',file));
const html=await readFile('index.html','utf8');for(const match of html.matchAll(/(?:src|href)="(\.\/[^"#]+)"/g))await stat(path.resolve(root,match[1]));
console.log(`Import and asset closure verified (${count} imports).`);
