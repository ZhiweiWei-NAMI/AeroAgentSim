#!/usr/bin/env python3
"""Build an offline, network-denied HTML from the exact HTTP app and lazy graph chunks."""
import json,gzip,base64,pathlib,re,hashlib
root=pathlib.Path(__file__).resolve().parents[1];ui=root/'ui'
paths=[root/'data/native-index.json',*sorted((root/'data/native-details').glob('*.json')),root/'data/typed-index.json',*sorted((root/'data/typed-details').glob('*.json')),root/'data/coherent/manifest.json',*sorted((root/'data/supplements').rglob('*.json')),*sorted((root/'data/coherent').glob('*/*.graph.json')),root/'data/instances/manifest.json',root/'data/instances/search-index.json',*sorted((root/'data/instances/scenarios').glob('*.json'))]
embedded={};report=[]
for path in paths:
 raw=path.read_bytes();key=path.relative_to(root).as_posix();compressed=gzip.compress(raw,compresslevel=9,mtime=0);embedded[key]=base64.b64encode(compressed).decode();report.append({'path':key,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
transport='''const embedded=EMBEDDED;const decoded=new Map();globalThis.fetch=async function(input){const url=new URL(String(input),document.baseURI),key=url.pathname.slice(url.pathname.lastIndexOf('/data/')+1);if(!Object.hasOwn(embedded,key))throw new Error('离线文件未包含此资源: '+key);if(!globalThis.DecompressionStream)throw new Error('此浏览器缺少 DecompressionStream；请使用近期版本的 Chrome、Edge、Firefox 或 Safari。');if(!decoded.has(key)){decoded.set(key,(async()=>{const bytes=Uint8Array.from(atob(embedded[key]),c=>c.charCodeAt(0)),stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));return new Response(stream).text();})());}const text=await decoded.get(key);return{ok:true,status:200,json:async()=>JSON.parse(text),text:async()=>text};};'''.replace('EMBEDDED',json.dumps(embedded,separators=(',',':')))
js=[]
for name in ['canonical-model.js','model.js','display-vocabulary.js','display.js','renderer.js','coherent.js','app.js']:
 source=(ui/name).read_text();source=re.sub(r'^import .*?;\n','',source,flags=re.M);source=re.sub(r'^export \{[^}]*\};?\n','',source,flags=re.M);source=re.sub(r'\bexport (?=(?:const|function|class))','',source);js.append(source)
script='(()=>{'+transport+'\n'+'\n'.join(js)+'\n})();'
html=(ui/'index.html').read_text().replace('<link rel="stylesheet" href="styles.css">','<style>'+(ui/'styles.css').read_text()+'</style>').replace('<script type="module" src="app.js"></script>','<script>'+script.replace('</script','<\\/script')+'</script>').replace("script-src 'self'","script-src 'unsafe-inline'").replace("connect-src 'self'","connect-src 'none'")
out=ui/'P08-native-star-offline.html';out.write_text(html);result={'output':out.name,'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'embedded_files':len(report),'inputs':report,'network_requests_allowed':False,'browser_visual_verification':'blocked_not_claimed'};(ui/'offline-build-report.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='inputs'},indent=2))
