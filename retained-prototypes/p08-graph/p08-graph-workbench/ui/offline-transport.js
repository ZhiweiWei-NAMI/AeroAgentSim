/** Offline-only transport: no network fallback, every byte comes from an embedded gzip block. */
export function createOfflineTransport(entries,{document=globalThis.document,DecompressionStream=globalThis.DecompressionStream,Response=globalThis.Response,Blob=globalThis.Blob,atob=globalThis.atob}={}) {
  const base='https://p08.offline/';
  async function readBytes(path,signal){
    if(signal?.aborted)throw new DOMException('Read cancelled','AbortError');
    const item=entries[path];if(!item)throw new Error(`离线包不含此文件：${path}`);
    if(typeof DecompressionStream!=='function')throw new Error('此浏览器不支持原生 gzip 解压。请使用支持 DecompressionStream 的浏览器，或打开配套本地 HTTP 版本。');
    const content=document.getElementById(item.element_id)?.textContent.trim();if(!content)throw new Error(`离线数据块缺失：${path}`);
    const binary=atob(content),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
    const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    const output=new Uint8Array(await new Response(stream).arrayBuffer());
    if(signal?.aborted)throw new DOMException('Read cancelled','AbortError');
    if(output.byteLength!==item.bytes)throw new Error(`离线数据长度不符：${path}`);
    return output;
  }
  async function fetchImpl(url,{signal}={}){const parsed=new URL(url,base);if(parsed.origin!==new URL(base).origin)throw new Error('离线版禁止外部数据请求。');const path=decodeURIComponent(parsed.pathname.slice(1));if(!entries[path])return{ok:false,status:404};const bytes=await readBytes(path,signal);return{ok:true,status:200,json:async()=>JSON.parse(new TextDecoder().decode(bytes)),text:async()=>new TextDecoder().decode(bytes),arrayBuffer:async()=>bytes.buffer};}
  return {base,entries,fetchImpl,readBytes};
}
