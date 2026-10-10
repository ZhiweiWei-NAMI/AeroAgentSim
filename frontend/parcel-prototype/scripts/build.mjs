import {cp,mkdir,rm} from 'node:fs/promises';
const root=new URL('../',import.meta.url),dist=new URL('../dist/',import.meta.url);
await rm(dist,{force:true,recursive:true});await mkdir(dist,{recursive:true});
for(const file of ['index.html','favicon.svg','src'])await cp(new URL(file,root),new URL(file,dist),{recursive:true});
console.log('Static distribution built in dist/ (no network/runtime dependencies).');
