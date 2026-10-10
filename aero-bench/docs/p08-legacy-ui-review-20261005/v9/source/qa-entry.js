// QA entry only. Source renderer presentation is patched; published legacy facts are unchanged.
globalThis.__P08_TEST__=true;
const {createApp}=await import('./app.js');
const notice='published legacy dataset / UI-only visual review';
const unavailable=['source-coherent','source-native'];
for(const id of unavailable){const button=document.getElementById(id);button.disabled=true;button.title='UNLOADED — not included in this published QA dataset';button.querySelector('span').textContent='未加载 / UNLOADED';}
// QA scope notice lives on document.body so it stays visible at <=1250px, where the
// responsive QA layout hides the masthead .header-end that previously held it.
const tag=document.createElement('div');tag.className='qa-source';tag.innerHTML='<b>'+notice+'</b>新13案例及原始目录未加载 · provider未接入';
document.body.prepend(tag);
document.title=notice+' · P08';
globalThis.p08=createApp({initialSource:'instances'});
await globalThis.p08.start();
// Replace the full-package provenance dialog for this deliberately bounded entry.
document.getElementById('provenance-button').addEventListener('click',event=>{
 event.stopImmediatePropagation();
 const box=document.getElementById('provenance-content');box.replaceChildren();
 for(const value of [notice,'UI source: 4f9adf6e8b851d3a7b1086fba3d297f1620607a2','Published legacy data: 7ca57707930fc45fdc1d48f32c6470beabf33bb8','Original catalog: UNLOADED. Accepted13cases: UNLOADED. No rule execution or native-provider integration is claimed.']){const p=document.createElement('p');p.textContent=value;box.append(p);}
 document.getElementById('provenance-dialog').showModal();
},true);

// QA-only drawers retain controls/data and fit the unoccluded graph area.
let drawerCamera=null,drawerManuallyChanged=false;
const previousCameraChange=p08.renderer.callbacks.onChange;
p08.renderer.callbacks.onChange=camera=>{if(drawerCamera)drawerManuallyChanged=true;previousCameraChange(camera);};
function fitVisibleGraph(){
 const renderer=p08.renderer,canvas=document.getElementById('graph').getBoundingClientRect();
 let area={x:0,y:0,w:canvas.width,h:canvas.height},occluded=false;
 for(const target of ['.navigator','.inspector']){
  const pane=document.querySelector(target);if(getComputedStyle(pane).display==='none'||getComputedStyle(pane).position!=='absolute')continue;
  const box=pane.getBoundingClientRect();if(box.right<=canvas.left||box.left>=canvas.right||box.bottom<=canvas.top||box.top>=canvas.bottom)continue;
  occluded=true;
  if(box.width>=canvas.width*.8&&box.top>canvas.top)area.h=Math.min(area.h,box.top-canvas.top);
  else if(box.left>canvas.left)area.w=Math.min(area.w,box.left-canvas.left);
  else{const edge=box.right-canvas.left;area.w-=Math.max(0,edge-area.x);area.x=edge;}
 }
 renderer.visibleViewport=occluded?area:null;
 const controls=document.querySelector('.canvas-bottom');
 controls.style.top=occluded&&area.h<canvas.height?Math.max(8,area.y+area.h-controls.clientHeight-8)+'px':'';
 controls.style.bottom=occluded&&area.h<canvas.height?'auto':'';
 if(occluded){if(!drawerCamera){drawerCamera={...renderer.camera};drawerManuallyChanged=false;}if(!drawerManuallyChanged)renderer.fit({preserveOrientation:true});}
 else if(drawerCamera){if(!drawerManuallyChanged)renderer.camera=drawerCamera;drawerCamera=null;renderer.dirty=true;}
}
for(const [kind,label,target] of [['filters','筛选 / Filters','.navigator'],['details','详情 / Details','.inspector']]){
 const button=document.createElement('button');button.className='qa-drawer-toggle qa-'+kind+'-toggle';button.type='button';button.textContent=label;button.setAttribute('aria-expanded','false');
 const pane=document.querySelector(target);pane.id='qa-'+kind+'-drawer';button.setAttribute('aria-controls',pane.id);
 button.addEventListener('click',()=>{const open=!document.body.classList.contains('qa-'+kind+'-open');document.body.classList.toggle('qa-'+kind+'-open',open);button.setAttribute('aria-expanded',String(open));fitVisibleGraph();});
 const close=document.createElement('button');close.type='button';close.className='qa-drawer-close';close.textContent='收起 / Close';close.setAttribute('aria-label',label+' — 收起 / Close');
 close.addEventListener('click',()=>{document.body.classList.remove('qa-'+kind+'-open');button.setAttribute('aria-expanded','false');fitVisibleGraph();});pane.prepend(close);
 document.querySelector('.graph-heading').append(button);
}
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&(document.body.classList.contains('qa-filters-open')||document.body.classList.contains('qa-details-open'))){event.stopImmediatePropagation();document.body.classList.remove('qa-filters-open','qa-details-open');for(const button of document.querySelectorAll('.qa-drawer-toggle'))button.setAttribute('aria-expanded','false');fitVisibleGraph();}},true);
window.addEventListener('resize',()=>requestAnimationFrame(fitVisibleGraph));
