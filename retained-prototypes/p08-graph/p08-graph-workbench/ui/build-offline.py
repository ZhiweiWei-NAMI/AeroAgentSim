#!/usr/bin/env python3
"""Make a same-data, double-click HTML; no external script, style, font or data request."""
from pathlib import Path
import base64,gzip,hashlib,json,re
ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/'ui'
files=sorted((ROOT/'data/scenarios').glob('*.json'))
files += [ROOT/'data'/n for n in ['manifest.json','search-index.json','aliases.json','conflicts.json','coverage.json','relation-signatures.json','input-manifest.json','graph.json']]
files += sorted((ROOT/'spec').glob('*.json'))
files += [ROOT/'review/domain'/n for n in ['VERDICT.md','coverage-ledger.json','resolution-ledger.json','adversarial-scenarios.json']]
files += [ROOT/'review/technical'/n for n in ['acceptance-summary.json','artifact-audit.json','schema-validation.json','rebuild-comparison.json']]
entries={};blocks=[]
for i,path in enumerate(files):
    data=path.read_bytes();key=path.relative_to(ROOT).as_posix();element=f'p08-gzip-{i}'
    compressed=gzip.compress(data,compresslevel=9,mtime=0)
    entries[key]={'element_id':element,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    blocks.append(f'<script type="application/gzip" id="{element}">{base64.b64encode(compressed).decode()}</script>')
html=(UI/'index.html').read_text()
html=html.replace('<link rel="stylesheet" href="styles.css">','<style>'+(UI/'styles.css').read_text()+'</style>')
html=re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]+>', '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data: blob:; connect-src \'none\'; object-src \'none\'; base-uri \'none\'">',html)
html=html.replace('只读 · 私有原型','离线 HTML · 只读')
script="globalThis.__P08_TEST__=true;\n"
for name in ['model.js','renderer.js','app.js','offline-transport.js']:
    source=(UI/name).read_text();source=re.sub(r'^import .*;\n','',source,flags=re.M);source=re.sub(r'^export (?=(?:const|function|class)\b)','',source,flags=re.M);script+='\n'+source
script+='\nconst P08_EMBEDDED='+json.dumps(entries,ensure_ascii=False,separators=(',',':'))+';\n'
script+='''
const offline=createOfflineTransport(P08_EMBEDDED);
globalThis.p08OfflineData=offline;
const app=createApp({fetchImpl:offline.fetchImpl,manifestURL:offline.base+'data/manifest.json'});
globalThis.p08GraphApp=app;
document.addEventListener('click',async event=>{
 const link=event.target.closest('a');if(!link)return;
 const original=link.getAttribute('href')||'';if(original.startsWith('blob:')||original.startsWith('data:'))return;
 const url=new URL(original,offline.base+'ui/'),path=decodeURIComponent(url.pathname.slice(1));
 if(url.origin!==new URL(offline.base).origin)return;
 event.preventDefault();
 try{const bytes=await offline.readBytes(path),blob=new Blob([bytes],{type:path.endsWith('.json')?'application/json':'text/plain'}),download=URL.createObjectURL(blob),a=document.createElement('a');a.href=download;a.download=path.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(download),1000);}catch(error){document.getElementById('error').hidden=false;document.getElementById('error').textContent=error.message;}
});
app.start();
'''
html=html.replace('<script type="module" src="app.js"></script>','\n'.join(blocks)+'\n<script>'+script+'</script>')
output=UI/'P08-graph-explorer-offline.html';output.write_text(html)
report={'file':output.name,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'embedded_files':len(entries),'source_graph_sha256':entries['data/graph.json']['sha256'],'network_requests':'none; connect-src none; embedded transport only','runtime_requirement':'native DecompressionStream(gzip); feature-detected; no CDN fallback','visual_verification':'not_run'}
(UI/'offline-build-report.json').write_text(json.dumps(report,indent=2)+'\n')
(UI/'offline-embedded-manifest.json').write_text(json.dumps(entries,indent=2)+'\n')
print(json.dumps(report,indent=2))
