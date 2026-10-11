import {useEffect,useState} from 'react';
import {Modal} from 'antd';
import {useLocation} from 'react-router-dom';
import {useI18n} from '../i18n/I18nProvider';
import {translate} from '../i18n/translate';
import './gallery.css';
export const WELCOME_EVENT='aas:welcome-help';
const KEY='aas:welcome-dismissed';
function welcomeSteps(locale:string){
 const t=(key:string)=>translate(locale,key);
 return [
  {title:t('Pick a scenario'),body:t('Choose a card and press Run now. The settings are ready to use.')},
  {title:t('Watch it run'),body:t('Follow the vehicles, read the events and explore the results.')},
  {title:t('Change one thing and compare'),body:t('Customize a setting, run again and compare both runs in Runs.')},
 ];
}
export const WELCOME_STEPS=welcomeSteps('en-US');
export function openWelcome(){document.dispatchEvent(new CustomEvent(WELCOME_EVENT));}
export function Welcome({open,onClose}:{open?:boolean;onClose?:()=>void}){
 const {t,locale}=useI18n();
 const location=useLocation(),[selfOpen,setSelfOpen]=useState(false);
 useEffect(()=>{const show=()=>setSelfOpen(true);document.addEventListener(WELCOME_EVENT,show);return()=>document.removeEventListener(WELCOME_EVENT,show);},[]);
 useEffect(()=>{if(new URLSearchParams(location.search).has('welcome'))setSelfOpen(true);},[location.search]);
 const close=()=>{try{localStorage.setItem(KEY,'1');}catch{/* Walkthrough still closes for this visit. */}setSelfOpen(false);onClose?.();};
 return <Modal open={open??selfOpen} onCancel={close} footer={null} centered width={520} closable={false}>
  <div className="welcome-panel" data-testid="welcome-panel"><div className="template-card-actions"><h2>{t('Your first simulation')}</h2><button type="button" className="console-btn" aria-label={t('Close')} onClick={close}>×</button></div><p className="template-card-summary">{t('You do not need to build a scenario to get started.')}</p>
   <ol className="welcome-steps">{welcomeSteps(locale).map((step,index)=><li className="welcome-step" key={index}><span className="welcome-step-num">{index+1}</span><div><h3>{step.title}</h3><p>{step.body}</p></div></li>)}</ol>
   <button className="console-btn console-btn-primary" data-testid="welcome-close" onClick={close}>{t('Choose a scenario')}</button>
  </div>
 </Modal>;
}
export default Welcome;
