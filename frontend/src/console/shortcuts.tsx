import { useEffect, useRef, useState } from 'react';
import { Modal } from 'antd';
import { useI18n } from '../i18n/I18nProvider';
export const SHORTCUTS_HELP_EVENT = 'aas:shortcuts-help';
function typing(target: EventTarget | null) {
  return target instanceof HTMLElement && (!!target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"],[role="combobox"]') || target.isContentEditable);
}
function dialogOpen() {
  return [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].some(node => node.getAttribute('aria-hidden') !== 'true' && !node.closest('[aria-hidden="true"]') && getComputedStyle(node).display !== 'none' && getComputedStyle(node).visibility !== 'hidden');
}
export function RunShortcuts({enabled,onToggle,onInject}:{enabled:boolean;onToggle:()=>void;onInject:()=>void}) {
  const current = useRef({enabled,onToggle,onInject}); current.current={enabled,onToggle,onInject};
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(event.ctrlKey||event.metaKey||event.altKey||event.repeat||typing(event.target)||dialogOpen()||!current.current.enabled)return;
      if(event.key===' '){event.preventDefault();current.current.onToggle();}
      if(event.key.toLowerCase()==='i'){event.preventDefault();current.current.onInject();}
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
    <div data-testid="shortcuts-help"><dl className="console-shortcuts"><div><dt><kbd>{t('Space')}</kbd></dt><dd>{t('Pause or resume the live simulation')}</dd></div><div><dt><kbd>i</kbd></dt><dd>{t('Introduce an event')}</dd></div><div><dt><kbd>?</kbd></dt><dd>{t('Open shortcuts help')}</dd></div></dl><p>{t('Run controls apply to the selected live run. Shortcuts stay inactive while typing, replaying or using a dialog.')}</p></div>
  </Modal>;
}
