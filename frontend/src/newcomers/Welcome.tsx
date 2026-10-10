import {useEffect,useState} from 'react';
import {Modal} from 'antd';
import {useLocation} from 'react-router-dom';
import './gallery.css';
export const WELCOME_EVENT='aas:welcome-help';
const KEY='aas:welcome-dismissed';
export const WELCOME_STEPS=[
 {title:'Pick a scenario',body:'Choose a card and press Run now. The settings are ready to use.'},
 {title:'Watch it run',body:'Follow the vehicles, read the events and explore the results.'},
 {title:'Change one thing and compare',body:'Customize a setting, run again and compare both runs in Runs.'},
] as const;
export function openWelcome(){document.dispatchEvent(new CustomEvent(WELCOME_EVENT));}
export function Welcome({open,onClose}:{open?:boolean;onClose?:()=>void}){
 const location=useLocation(),[selfOpen,setSelfOpen]=useState(()=>{try{return localStorage.getItem(KEY)!=='1';}catch{return true;}});
 useEffect(()=>{const show=()=>setSelfOpen(true);document.addEventListener(WELCOME_EVENT,show);return()=>document.removeEventListener(WELCOME_EVENT,show);},[]);
 useEffect(()=>{if(new URLSearchParams(location.search).has('welcome'))setSelfOpen(true);},[location.search]);
 const close=()=>{try{localStorage.setItem(KEY,'1');}catch{/* Walkthrough still closes for this visit. */}setSelfOpen(false);onClose?.();};
 return <Modal open={open??selfOpen} onCancel={close} footer={null} centered width={520}>
  <div className="welcome-panel" data-testid="welcome-panel"><h2>Your first simulation</h2><p className="template-card-summary">You do not need to build a scenario to get started.</p>
   <ol className="welcome-steps">{WELCOME_STEPS.map((step,index)=><li className="welcome-step" key={step.title}><span className="welcome-step-num">{index+1}</span><div><h3>{step.title}</h3><p>{step.body}</p></div></li>)}</ol>
   <button className="console-btn console-btn-primary" data-testid="welcome-close" onClick={close}>Choose a scenario</button>
  </div>
 </Modal>;
}
export default Welcome;
