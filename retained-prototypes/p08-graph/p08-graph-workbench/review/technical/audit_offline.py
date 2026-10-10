"""Independent offline-HTML packaging inspection, not browser/pixel QA."""
from pathlib import Path
from html.parser import HTMLParser
import re,json,gzip,base64,hashlib,time
root=Path(__file__).resolve().parents[2];start=time.perf_counter();path=root/'ui/P08-graph-explorer-offline.html';blob=path.read_bytes();html=blob.decode();errors=[]
class Assets(HTMLParser):
 def __init__(self):super().__init__();self.external=[];self.csp=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='script' and a.get('src'):self.external.append({'tag':tag,'src':a['src']})
  if tag=='link' and a.get('href'):self.external.append({'tag':tag,'href':a['href']})
  if tag=='meta' and a.get('http-equiv','').lower()=='content-security-policy':self.csp.append(a.get('content',''))
parser=Assets();parser.feed(html)
if parser.external:errors.append({'external_assets':parser.external})
if not any("connect-src 'none'" in p for p in parser.csp):errors.append({'csp':'missing_no_network'})
if len(blob)>=50*1024*1024:errors.append({'oversize_bytes':len(blob)})
match=re.search(r'const P08_EMBEDDED=(\{[^\n]+\});',html);assert match,'Embedded manifest absent';entries=json.loads(match.group(1));external=json.loads((root/'ui/offline-embedded-manifest.json').read_text())
if entries!=external:errors.append({'manifest':'inline_sidecar_mismatch'})
blocks={m[1]:m[2] for m in re.finditer(r'<script type="application/gzip" id="([^"]+)">([^<]+)</script>',html)}
for file,item in entries.items():
 raw=gzip.decompress(base64.b64decode(blocks[item['element_id']],validate=True));sha=hashlib.sha256(raw).hexdigest()
 if len(raw)!=item['bytes'] or sha!=item['sha256']:errors.append({'file':file,'error':'embedded_manifest_hash_or_length'})
 if raw!=(root/file).read_bytes():errors.append({'file':file,'error':'current_source_bytes_differ'})
scenarios=[p for p in entries if p.startswith('data/scenarios/')]
if len(scenarios)!=75:errors.append({'scene_count':len(scenarios)})
report={'scope':'Offline artifact byte/source/CSP inspection only; no real browser or pixel-render evidence.','html_sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob),'embedded_files':len(entries),'embedded_scenes':len(scenarios),'graph_sha256':entries['data/graph.json']['sha256'],'all_embedded_bytes_match_current_sources':not errors,'errors':errors,'elapsed_seconds':round(time.perf_counter()-start,3),'roundtrip_boundary':'Original-document byte reconstruction uses the accompanying ZIP source snapshots; they are not duplicated in this standalone HTML.'}
(root/'review/technical/offline-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(bool(errors))
