/** Conservative ts-prune-style audit using the installed TypeScript compiler. No downloads.
 * Reports candidates, never deletes: test-only exports and dynamic CSS need manual review.
 */
import ts from 'typescript';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const config=ts.readConfigFile(path.join(root,'tsconfig.json'),ts.sys.readFile);
const parsed=ts.parseJsonConfigFileContent(config.config,ts.sys,root,{allowJs:true,noEmit:true});
const files=ts.sys.readDirectory(path.join(root,'src'),['.ts','.tsx','.js','.jsx']);
const program=ts.createProgram(files,parsed.options),checker=program.getTypeChecker();
const sources=program.getSourceFiles().filter(f=>f.fileName.startsWith(path.join(root,'src')+path.sep)&&!f.isDeclarationFile&&/\.[jt]sx?$/.test(f.fileName));
const sourceFiles=new Set(sources.map(s=>s.fileName));
const runtimeFile=file=>file.endsWith('.d.ts')&&sourceFiles.has(file.slice(0,-5)+'.js')?file.slice(0,-5)+'.js':file;
const alias=s=>s?.flags&ts.SymbolFlags.Alias?checker.getAliasedSymbol(s):s;
const identity=s=>{const declaration=s?.declarations?.[0];return declaration?runtimeFile(declaration.getSourceFile().fileName)+':'+s.name:s;};
const uses=new Map(),edges=new Map();
for(const source of sources){
 const deps=new Set();edges.set(source.fileName,deps);
 const visit=node=>{
  if(ts.isIdentifier(node)){
   const symbol=identity(alias(checker.getSymbolAtLocation(node)));
   if(symbol){let refs=uses.get(symbol);if(!refs){refs=new Set();uses.set(symbol,refs);}refs.add(source.fileName);}
  }
  const spec=(ts.isImportDeclaration(node)||ts.isExportDeclaration(node))?node.moduleSpecifier:
   ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword?node.arguments[0]:
   ts.isNewExpression(node)&&ts.isIdentifier(node.expression)&&node.expression.text==='URL'?node.arguments?.[0]:undefined;
  if(spec&&ts.isStringLiteralLike(spec)){
   const resolved=ts.resolveModuleName(spec.text,source.fileName,parsed.options,ts.sys).resolvedModule;
   if(resolved)deps.add(runtimeFile(resolved.resolvedFileName));
  }
  ts.forEachChild(node,visit);
 };visit(source);
}
const reachable=new Set();
function walk(file){if(reachable.has(file))return;reachable.add(file);for(const next of edges.get(file)??[])walk(next);}
walk(path.join(root,'src/index.js'));
const test=file=>/\.(test|spec)\.|\/test-setup\.|(?:fixture|TestSetup)\./.test(file);
const relative=file=>path.relative(root,file);
const exports=[];
for(const source of sources){
 const module=checker.getSymbolAtLocation(source);if(!module||test(source.fileName))continue;
 for(const exp of checker.getExportsOfModule(module)){
  const symbol=identity(alias(exp)),external=[...(uses.get(symbol)??[])].filter(file=>file!==source.fileName);
  if(external.some(file=>reachable.has(file)&&!test(file)))continue;
  exports.push({file:relative(source.fileName),name:exp.name,usedBy:external.map(relative),reachable:reachable.has(source.fileName)});
 }
}
const unreachable=sources.filter(s=>!reachable.has(s.fileName)&&!test(s.fileName)).map(s=>({file:relative(s.fileName),bytes:Buffer.byteLength(s.text)}));
const text=sources.filter(s=>reachable.has(s.fileName)).map(s=>s.text).join('\n');
const css=[];
for(const file of ts.sys.readDirectory(path.join(root,'src'),['.css'])){
 // Literal class tokens only: dynamic prefixes are deliberately flagged for review too.
 const content=fs.readFileSync(file,'utf8');
 const classes=[...new Set([...content.matchAll(/\.([a-zA-Z_][\w-]*)/g)].map(m=>m[1]))];
 const absent=classes.filter(name=>!text.includes(name));if(absent.length)css.push({file:relative(file),candidates:absent});
}
console.log(JSON.stringify({entry:'src/index.js',reachableModules:reachable.size,unreachable,exports,css},null,2));
