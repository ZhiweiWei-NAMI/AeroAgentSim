import {cp,mkdir,rm} from 'node:fs/promises';
await rm('dist',{recursive:true,force:true});
await mkdir('dist',{recursive:true});
for(const file of ['index.html','favicon.svg','src']) await cp(file,`dist/${file}`,{recursive:true});
console.log('Static distribution built in dist/. No remote connections configured.');
