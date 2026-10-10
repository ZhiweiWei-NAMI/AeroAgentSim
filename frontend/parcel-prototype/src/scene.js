export const escapeHtml = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const project=([e,n,u=0])=>[240+5.8*e-3*n,570-1.85*e-2.7*n-5*u];
const p=v=>project(v).map(n=>n.toFixed(2)).join(',');
const poly=(points,fill,stroke='none',extra='')=>`<polygon points="${points.map(p).join(' ')}" fill="${fill}" stroke="${stroke}" ${extra}/>`;
function box(e,n,u,w,d,h,colors,extra=''){
  const a=[e,n,u],b=[e+w,n,u],c=[e+w,n+d,u],f=[e,n+d,u],top=[a,b,c,f].map(v=>[v[0],v[1],v[2]+h]);
  return `<g ${extra}>${poly([a,b,top[1],top[0]],colors[1])}${poly([a,f,top[3],top[0]],colors[2])}${poly(top,colors[0])}</g>`;
}
const path=points=>points.map((v,i)=>`${i?'L':'M'} ${p(v)}`).join(' ');
function tree(e,n,scale=1){const [x,y]=project([e,n,0]);return `<g opacity=".94"><ellipse cx="${x+4}" cy="${y+2}" rx="13" ry="5" fill="#132f2420"/><path d="M${x} ${y}v-${18*scale}" stroke="#857263" stroke-width="3"/><ellipse cx="${x}" cy="${y-20*scale}" rx="${12*scale}" ry="${16*scale}" fill="#80ad96"/><ellipse cx="${x-3}" cy="${y-24*scale}" rx="${8*scale}" ry="${11*scale}" fill="#a4c7b1"/></g>`;}
function label(position,title,subtitle='',color='#425363') { const [x,y]=project(position);return `<g class="scene-label" pointer-events="none"><text x="${x}" y="${y}" text-anchor="middle" fill="${color}" font-size="14" font-weight="650">${escapeHtml(title)}</text>${subtitle?`<text x="${x}" y="${y+18}" text-anchor="middle" fill="#6e7c89" font-size="10">${escapeHtml(subtitle)}</text>`:''}</g>`; }
function pick(id,body,selected){return `<g data-entity-id="${escapeHtml(id)}" class="scene-object ${selected===id?'selected':''}" role="img" aria-label="${escapeHtml(id)}">${body}</g>`;}
function marker(position,id,selected){if(selected!==id)return '';const [x,y]=project(position);return `<g pointer-events="none"><ellipse cx="${x}" cy="${y+4}" rx="22" ry="9" fill="none" stroke="#e19228" stroke-width="2"/><path d="M${x} ${y-22}v-16" stroke="#bd761c" stroke-width="2"/><circle cx="${x}" cy="${y-42}" r="5" fill="#e8a13b" stroke="#fff" stroke-width="2"/></g>`;}
function staticScene(t,selected){
  let s=`<defs><pattern id="park-grid" width="36" height="36" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".8" fill="#bdc7cf"/></pattern><filter id="scene-shadow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="9" stdDeviation="9" flood-color="#42566b" flood-opacity=".1"/></filter></defs><rect width="1100" height="680" fill="#eaf0f2"/><rect width="1100" height="680" fill="url(#park-grid)" opacity=".33"/>`;
  s+=poly([[0,0,-1],[130,0,-1],[130,70,-1],[0,70,-1]],'#d1ddd9');
  s+=poly([[0,0,0],[130,0,0],[130,70,0],[0,70,0]],'#f2f5f2','#d8e0e2');
  const road=[[0,7,0.06],[32,7,.06],[32,24,.06],[116,24,.06],[116,62,.06]];
  s+=`<path d="${path(road)}" fill="none" stroke="#d2dade" stroke-width="42" stroke-linejoin="round"/><path d="${path(road)}" fill="none" stroke="#f9fbfb" stroke-width="1.7" stroke-dasharray="9 9"/>`;
  s+=`<path d="${path([[18,18,.08],[27,18,.08],[27,24,.08],[52,24,.08]])}" fill="none" stroke="#9fb7c2" stroke-width="18" stroke-linejoin="round"/>`;
  s+=poly([[7,36,.02],[30,36,.02],[30,65,.02],[7,65,.02]],'#dce7db');
  s+=poly([[44,44,.02],[68,44,.02],[68,66,.02],[44,66,.02]],'#deeadf');
  s+=poly([[90,42,.02],[123,42,.02],[123,67,.02],[90,67,.02]],'#dce8e1');
  // Original geometric assets: warehouse, dock and storage blocks.
  let warehouse=box(4,20,0,22,16,8,['#318bdd','#226ab5','#19528c']);
  warehouse+=box(4,27,8,22,9,3,['#55a3e5','#2b7ec3','#246daf']);
  for(const e of [7,12,17,22]) {
    warehouse+=box(e,19.8,0,3,0.2,4,['#3c7aaa','#153b61','#1f507e']);
    warehouse+=box(e,18.4,0,3,1.5,.5,['#e9e4d7','#bebdae','#b1b6ad']);
  }
  warehouse+=box(6,18.8,.5,1.4,1.5,1.5,['#d8b880','#bd985c','#aa854f']);
  warehouse+=box(8,18.8,.5,1.4,1.5,1.8,['#d8b880','#bd985c','#aa854f']);
  warehouse+=marker([13,29,12],'warehouse',selected);
  s+=pick('warehouse',warehouse,selected);
  s+=label([10,39,0],t('warehouse'),'WAREHOUSE 01');
  // Pallet staging and a stationary forklift establish scale and loading affordances.
  s+=box(20,35,0,4,2,.2,['#c4ae88','#a89170','#b3a080']);
  s+=box(20.2,35.2,.2,1.7,1.6,1.5,['#d9bb88','#c4a36c','#b4925d']);
  s+=box(22.1,35.2,.2,1.7,1.6,2,['#e3c18a','#c4a36c','#b4925d']);
  s+=box(28,34,.3,3.5,2,1.3,['#e7b348','#c6932c','#b8872a']);
  s+=`<path d="${path([[31.5,34,0],[31.5,34,4],[31.5,36,4],[31.5,36,0]])}" fill="none" stroke="#394957" stroke-width="3"/>`;
  for(const e of [28.7,30.7]){const[x,y]=project([e,34,.3]);s+=`<circle cx="${x}" cy="${y}" r="3.5" fill="#3a4550"/>`;}
  // Landing pad is a true ground resource, with distinct intermediate tray.
  let pad=poly([[47,20,.1],[66,20,.1],[66,36,.1],[47,36,.1]],'#d0dedf','#aabcbe');
  const [px,py]=project([59,27,.2]);
  pad+=`<ellipse cx="${px}" cy="${py}" rx="38" ry="18" fill="none" stroke="#fdfefe" stroke-width="3"/><text x="${px}" y="${py+5}" text-anchor="middle" fill="#80a3ab" font-size="24" font-weight="700">H</text>`;
  pad+=marker([59,31,.2],'pad',selected);s+=pick('pad',pad,selected);
  s+=pick('tray',box(54,25,.2,2.2,2.3,.7,['#e6bb6a','#b2914f','#a37e37'])+marker([55,26,1],'tray',selected),selected);
  s+=label([54,38,0],t('pad'),'PAD A · TRANSFER');
  // Delivery lockers have visible compartments, with parcels rendered separately.
  let locker=box(109.5,41,0,6,2.5,4,['#d3e5e5','#638895','#86a5ab']);
  for(const e of [110,112,114])for(const u of [1,2.5]) locker+=box(e,40.98,u,1.4,.03,1.1,['#92b2b7','#bdd4d4','#a8c3c7']);
  locker+=marker([113,43,4],'locker',selected);s+=pick('locker',locker,selected);
  s+=label([112,50,0],t('locker'),'LOCKER C');
  const [rx,ry]=project([82,57,0]);
  s+=pick('relay',`<ellipse cx="${rx}" cy="${ry}" rx="12" ry="5" fill="#97b5b550"/><path d="M${rx} ${ry}v-45" stroke="#789697" stroke-width="3"/><circle cx="${rx}" cy="${ry-44}" r="5" fill="#60a79b"/><path d="M${rx-8} ${ry-54}q-7 10 0 20m16-20q7 10 0 20" fill="none" stroke="#79b7aa" stroke-width="2"/>`+marker([82,57,10],'relay',selected),selected);
  s+=label([84,65,0],t('relay'));
  s+=box(71,6,0,18,11,5,['#e2e6e3','#b9c5c3','#cad4cf']);
  s+=box(76,8,5,6,7,1,['#bad2cd','#a9c2bd','#abc3bb']);
  s+=box(91,7,0,15,9,3,['#d5dfda','#acbdb5','#b8c8bf']);
  for(const [e,n,scale] of [[6,48,1],[9,58,1.2],[20,57,1],[38,55,1.1],[45,56,1.2],[62,59,1],[96,61,1.1],[103,62,1],[122,52,1.15],[64,8,.8],[121,8,.9],[41,4,.9]])s+=tree(e,n,scale);
  s+=label([95,13,4],'SMART PARK','AUTHORED LOCAL SCENE','#8c9d98');
  s+=`<g transform="translate(44 602)" class="scene-compass"><path d="M0 24v-24l-4 9m4-9 4 9M0 24h28l-9-4m9 4-9 4" stroke="#8a9eaa" stroke-width="1.5" fill="none"/><text x="-4" y="-8" font-size="11" fill="#667c8b">N</text><text x="33" y="29" font-size="11" fill="#667c8b">E</text></g>`;
  return s;
}
function parcelGlyph(parcel,selected){
  const [e,n,u]=parcel.position;
  const inactive=parcel.id!=='parcel.p1042';
  let body=box(e-0.8,n-.7,u,1.6,1.4,1.25,inactive?['#d2b895','#bda180','#ac9070']:['#facb7d','#dfaa55','#c58b3d']);
  const [x,y]=project([e,n,u+1.25]);
  body+=`<path d="M${x-5} ${y-1}l9 -3" stroke="#96703c" stroke-width="2"/>`;
  body+=marker([e,n,u+1.7],parcel.id,selected);
  body+=`<g pointer-events="none"><rect x="${x+12}" y="${y-27}" width="66" height="21" rx="5" fill="#203546"/><text x="${x+45}" y="${y-12}" text-anchor="middle" fill="#fff1db" font-size="11" font-weight="600">${escapeHtml(parcel.label)}</text><path d="M${x+13} ${y-8}l-9 8" stroke="#203546" stroke-width="1.5"/></g>`;
  return pick(parcel.id,body,selected);
}
function carrierGlyph(entity,selected){
  const [e,n,u]=entity.position;let body='';
  const [sx,sy]=project([e,n,0]);body+=`<ellipse cx="${sx}" cy="${sy}" rx="${entity.kind==='uav'?20:17}" ry="7" fill="#59778228"/>`;
  if(entity.kind==='ugv'){
    body+=box(e-2.5,n-1.5,.5,5,3,.85,['#e8edef','#7796a6','#9eafb9']);
    body+=box(e-2.5,n-1.5,1.35,1.3,3,1.4,['#d5e0e5','#668ba0','#acc1cd']);
    for(const offset of [-1.7,1.5]){const [x,y]=project([e+offset,n-1.5,.5]);body+=`<circle cx="${x}" cy="${y}" r="5" fill="#34495a"/><circle cx="${x}" cy="${y}" r="2" fill="#7897a9"/>`;}
  }else{
    const [x,y]=project(entity.position);body+=`<path d="M${sx} ${sy}L${x} ${y}" stroke="#8da8b2" stroke-width="1" stroke-dasharray="3 5"/>`;
    for(const [de,dn] of [[-3,-2],[-3,2],[3,-2],[3,2]]){const [a,b]=project([e+de,n+dn,u]);body+=`<path d="M${x} ${y}L${a} ${b}" stroke="#536b7a" stroke-width="4"/><ellipse cx="${a}" cy="${b}" rx="12" ry="4.8" fill="#91acb06b" stroke="#3d6978" stroke-width="1.6"/>`;}
    body+=box(e-1.2,n-1,u-.25,2.4,2,.65,['#eff5f4','#75949d','#a0b9c0']);
    body+=`<path d="${path([[e-1,n-1,u-.2],[e-1,n-1,u-1],[e+1,n-1,u-1],[e+1,n-1,u-.2]])}" fill="none" stroke="#4a6876" stroke-width="1.5"/>`;
  }
  body+=marker(entity.position,entity.id,selected);
  const [x,y]=project([e,n,u]);body+=`<text x="${x}" y="${y+29}" text-anchor="middle" font-size="11" fill="#496579">${escapeHtml(entity.label)}</text>`;
  return pick(entity.id,body,selected);
}
export function renderScene(frame,{t,selectedId='parcel.p1042',follow=false}={}){
  let body=staticScene(t,selectedId);
  if(frame.routes){
    body+=`<path d="${path(frame.routes.ground)}" fill="none" stroke="#4c8c9d" stroke-width="2" stroke-dasharray="5 6" opacity=".65"/>`;
    body+=`<path d="${path(frame.routes.air)}" fill="none" stroke="#6eacb3" stroke-width="2" stroke-dasharray="6 6" opacity="${frame.activeRoute==='air'?'.65':'.22'}"/>`;
    if(frame.activeRoute==='alternate')body+=`<path d="${path(frame.routes.alternate)}" fill="none" stroke="#4e9a83" stroke-width="3" stroke-dasharray="7 5" opacity=".85"/>`;
  }
  const drone=frame.entities.find(e=>e.id==='uav.01');
  if(drone && frame.network && frame.timeSeconds>=29 && frame.timeSeconds<78){
    body+=`<path d="${path([drone.position,frame.activeRoute==='alternate'?[82,57,9]:[56,26,0]])}" fill="none" stroke="${frame.network.degraded?'#ce7e48':'#76ac9d'}" stroke-width="1.8" stroke-dasharray="3 5" opacity=".65"/>`;
  }
  for(const entity of frame.entities.filter(e=>['ugv','uav'].includes(e.kind)))body+=carrierGlyph(entity,selectedId);
  for(const parcel of frame.parcels)body+=parcelGlyph(parcel,selectedId);
  const selected=[...frame.parcels,...frame.entities].find(e=>e.id===selectedId);
  let viewBox='-10 30 1100 650';
  if(follow&&selected){const[x,y]=project(selected.position);viewBox=`${x-330} ${y-240} 660 430`;}
  return `<svg class="park-scene" xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" role="img" aria-label="${escapeHtml(t('scene'))}"><title>${escapeHtml(t('scene'))}</title><desc>${escapeHtml(t('mapHint'))}. ${escapeHtml(t('keyboard'))}</desc>${body}</svg>`;
}
