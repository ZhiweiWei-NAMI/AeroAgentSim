import { FIXTURE,sampleFixtureFrame } from './fixture.js';
import { translator } from './i18n.js';
import { mountParcelView } from './view.js';
const root=document.getElementById('app');
let language='zh',t=translator(language),playing=false,speed=1,cursor=.1,lastNow=null,currentTick=-1;
root.innerHTML=`<header class="app-header"><div class="brand"><span class="brand-mark">P<span>02</span></span><div><h1 id="app-title"></h1><p id="app-subtitle"></p></div></div><div class="header-right"><span id="prototype-label" class="prototype-pill"></span><div class="language-switch" aria-label="Language"><button data-language="zh" aria-pressed="true">中文</button><button data-language="en" aria-pressed="false">EN</button></div></div></header><main><div class="fixture-notice"><span class="notice-icon">◇</span><span id="fixture-note"></span><span class="run-label">FIXTURE · SMARTPARK-001</span></div><section class="transport"><div class="transport-buttons"><button id="play" class="play-button"></button><button id="reset"></button></div><div class="time-display"><strong id="time-value">00:00.1</strong><span id="tick-value"></span></div><div class="scrubber"><div class="scrubber-heading"><label for="timeline" id="timeline-label"></label><span>00:00 — 01:24</span></div><input id="timeline" type="range" min="0.1" max="84" step="0.1" value="0.1"><div class="timeline-markers"><button data-jump="6">UGV</button><button data-jump="29">UAV</button><button data-jump="38" class="fault-marker">38s · LINK ↓</button><button data-jump="49">49s · ROUTE ↗</button><button data-jump="78">78s · LOCKER</button></div></div><label class="speed-select"><span id="speed-label"></span><select id="speed"><option value=".5">0.5×</option><option selected value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select></label></section><div id="view"></div><footer class="app-footer"><span id="support-note"></span><span id="scope-note"></span></footer></main>`;
const el=id=>document.getElementById(id);
const view=mountParcelView(el('view'),{language,onSeek:seek});
function languageUI(){
  document.documentElement.lang=language==='zh'?'zh-CN':'en';
  for(const [id,key] of [['app-title','title'],['app-subtitle','subtitle'],['prototype-label','prototype'],['fixture-note','fixtureNote'],['reset','reset'],['timeline-label','timeline'],['speed-label','speed'],['support-note','support'],['scope-note','scope']])el(id).textContent=t(key);
  el('play').textContent=`${playing?'Ⅱ':'▶'} ${t(playing?'pause':cursor>=84?'replay':'play')}`;
  document.querySelectorAll('[data-language]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.language===language)));
  view.setLanguage(language);update(true);
}
function update(force=false){
  const frame=sampleFixtureFrame(cursor);if(!force&&frame.tick===currentTick)return;
  currentTick=frame.tick;view.setFrame(frame);
  el('timeline').value=String(cursor);
  el('time-value').textContent=`${Math.floor(frame.timeSeconds/60).toString().padStart(2,'0')}:${(frame.timeSeconds%60).toFixed(1).padStart(4,'0')}`;
  el('tick-value').textContent=`${t('tick')} ${frame.tick} / 840`;
  el('timeline').setAttribute('aria-valuetext',`${frame.timeSeconds} ${t('seconds')}`);
}
function setPlaying(value){playing=value;lastNow=null;el('play').textContent=`${playing?'Ⅱ':'▶'} ${t(playing?'pause':cursor>=84?'replay':'play')}`;}
function seek(value){setPlaying(false);cursor=Math.min(84,Math.max(.1,value));update(true);}
el('play').addEventListener('click',()=>{if(cursor>=84)cursor=.1;setPlaying(!playing);update();});
el('reset').addEventListener('click',()=>seek(.1));
el('timeline').addEventListener('input',event=>seek(Number(event.target.value)));
el('speed').addEventListener('change',event=>{speed=Number(event.target.value);lastNow=null;});
document.querySelectorAll('[data-jump]').forEach(button=>button.addEventListener('click',()=>seek(Number(button.dataset.jump))));
document.querySelectorAll('[data-language]').forEach(button=>button.addEventListener('click',()=>{language=button.dataset.language;t=translator(language);languageUI();}));
document.addEventListener('visibilitychange',()=>{if(document.hidden)setPlaying(false);});
function animate(now){
  if(playing){if(lastNow!==null)cursor=Math.min(FIXTURE.durationSeconds,cursor+Math.min((now-lastNow)/1000,.2)*speed);lastNow=now;update();if(cursor>=FIXTURE.durationSeconds)setPlaying(false);}
  requestAnimationFrame(animate);
}
languageUI();requestAnimationFrame(animate);
// Explicit, read-only host integration boundary. The component itself owns no clock.
export { mountParcelView };
