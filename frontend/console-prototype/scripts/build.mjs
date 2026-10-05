import {cp,mkdir,rm} from 'node:fs/promises';
await rm('dist',{recursive:true,force:true});
await mkdir('dist',{recursive:true});
for(const file of ['index.html','favicon.svg','src']) await cp(file,`dist/${file}`,{recursive:true});
console.log('Static distribution built in dist/. No remote connections configured.');

// CRA dev and production servers serve this explicit standalone route.
// Copy only the public distribution, never package metadata, tests or source evidence.
await rm('../public/configuration', { recursive: true, force: true });
await cp('dist', '../public/configuration', { recursive: true });
console.log('Authoring route staged at /configuration/index.html.');
