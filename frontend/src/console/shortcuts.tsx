import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';
import { useI18n } from '../i18n/I18nProvider';
export const SHORTCUTS_HELP_EVENT = 'aas:shortcuts-help';
function typing(target: EventTarget | null) {
  return target instanceof HTMLElement && (!!target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"],[role="combobox"]') || target.isContentEditable);
}
function dialogOpen() {
  return [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].some(node => {
    // forceRender drawers retain a visible-looking dialog inside a display:none
    // wrapper. Checking the dialog alone disables every watch shortcut forever.
    for(let ancestor:HTMLElement|null=node;ancestor;ancestor=ancestor.parentElement){
      const style=getComputedStyle(ancestor);
      if(ancestor.hidden||ancestor.getAttribute('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden')return false;
    }
    return true;
  });
}
export function RunShortcuts({enabled,onToggle,onInject}:{enabled:boolean;onToggle:()=>void;onInject:()=>void}) {
  const current = useRef({enabled,onToggle,onInject}); current.current={enabled,onToggle,onInject};
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(event.ctrlKey||event.metaKey||event.altKey||event.repeat||typing(event.target)||dialogOpen()||!current.current.enabled)return;
      // Simple owns Space for both live controls and recorded playback. The
      // operations drawer is force-mounted, so it must not toggle a second time.
      if(event.key===' '&&!document.querySelector('[data-testid="simple-run-view"]')){event.preventDefault();current.current.onToggle();}
      if(event.key.toLowerCase()==='i'){event.preventDefault();current.current.onInject();}
    };
    document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);
  },[]);
  return null;
}
interface SimpleShortcutProps {
  enabled?:boolean;
  onPlay:()=>void;onFollow:()=>void;onDirector:()=>void;onCinema:()=>void;onWhy:()=>void;onLanguage:()=>void;onExitCinema:()=>void;
}
export function SimpleShortcuts(props:SimpleShortcutProps) {
  const current=useRef(props);current.current=props;
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(current.current.enabled===false||event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||event.repeat||typing(event.target)||dialogOpen())return;
      const p=current.current;
      const action=event.key===' '?p.onPlay:event.key==='Escape'?p.onExitCinema:({f:p.onFollow,d:p.onDirector,c:p.onCinema,w:p.onWhy,l:p.onLanguage} as Record<string,()=>void>)[event.key.toLowerCase()];
      if(action){event.preventDefault();action();}
    };
    document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);
  },[]);
  return null;
}
export function ShortcutsHelp() {
  const { t } = useI18n();
  const [open,setOpen]=useState(false);
  useEffect(()=>{
    const help=()=>setOpen(true);
    const key=(event:KeyboardEvent)=>{if(event.key==='?'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.repeat&&!typing(event.target)&&!dialogOpen()){event.preventDefault();help();}};
    document.addEventListener(SHORTCUTS_HELP_EVENT,help);document.addEventListener('keydown',key);
    return()=>{document.removeEventListener(SHORTCUTS_HELP_EVENT,help);document.removeEventListener('keydown',key);};
  },[]);
  return <Modal closable={{ 'aria-label': t('Close') }} title={t('Keyboard shortcuts')} open={open} onCancel={()=>setOpen(false)} footer={null}>
    <div data-testid="shortcuts-help"><dl className="console-shortcuts"><div><dt><kbd>{t('Space')}</kbd></dt><dd>{t('Play / pause playback or the live simulation')}</dd></div><div><dt><kbd>F</kbd></dt><dd>{t('Follow the selected entity')}</dd></div><div><dt><kbd>D</kbd></dt><dd>{t('Toggle Director / resume Director')}</dd></div><div><dt><kbd>C</kbd></dt><dd>{t('Toggle cinema mode')}</dd></div><div><dt><kbd>{t('Esc')}</kbd></dt><dd>{t('Exit cinema mode')}</dd></div><div><dt><kbd>W</kbd></dt><dd>{t('Why? Open the selected entity’s latest event')}</dd></div><div><dt><kbd>L</kbd></dt><dd>{t('Switch between English and Chinese')}</dd></div><div><dt><kbd>I</kbd></dt><dd>{t('Introduce an event')}</dd></div><div><dt><kbd>?</kbd></dt><dd>{t('Open shortcuts help')}</dd></div></dl><p>{t('Camera, cinema, Why? and language shortcuts apply in Simple view. Shortcuts stay inactive while typing or using a dialog.')}</p></div>
  </Modal>;
}
