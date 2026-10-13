import {useEffect,useState} from 'react';
import {InputNumber,Select,Slider,Switch} from 'antd';
import {useI18n} from '../i18n/I18nProvider';
import {localized} from '../i18n/translate';
import {type AlgorithmDescriptor,type KnobValue,type TemplateKnob} from './api';
import {knobOptionLabel} from './presentation';
import './knobs.css';

export function TemplateKnobControl({knob,value,onChange,algorithms,onValidityChange,disabled=false}:{knob:TemplateKnob;value:KnobValue;onChange:(value:KnobValue)=>void;algorithms?:AlgorithmDescriptor[];onValidityChange?:(valid:boolean)=>void;disabled?:boolean}){
 const {t,locale}=useI18n();
 const [invalid,setInvalid]=useState(false);
 useEffect(()=>()=>onValidityChange?.(true),[knob.id]);
 const report=(valid:boolean)=>{setInvalid(!valid);onValidityChange?.(valid);};
 const numberValid=(next:number|null)=>next!==null&&Number.isFinite(next)&&(knob.type!=='integer'||Number.isSafeInteger(next))&&(knob.min===undefined||next>=knob.min)&&(knob.max===undefined||next<=knob.max);
 const id=`knob-${knob.id}`;
 const names=new Map(algorithms?.map(row=>[row.id,row]));
 const selectedAlgorithm=typeof value==='string'?names.get(value):undefined;
 const hint=localized(selectedAlgorithm,'description',locale)??localized(knob,'description',locale);
 return <div className="template-knob-row"><label id={`${id}-label`}>{localized(knob,'label',locale)}</label>
  {knob.type==='choice'?<Select disabled={disabled} aria-labelledby={`${id}-label`} data-testid={id} value={String(knob.options!.indexOf(value))} options={knob.options!.map((option,index)=>({value:String(index),label:knobOptionLabel(knob,option,locale,algorithms),title:typeof option==='string'?localized(names.get(option),'description',locale):undefined}))} onChange={index=>onChange(knob.options![Number(index)])}/>
   :knob.type==='boolean'?<Switch disabled={disabled} aria-labelledby={`${id}-label`} data-testid={id} checked={value as boolean} checkedChildren={t('yes')} unCheckedChildren={t('no')} onChange={onChange}/>
   :<div className="template-number-control">{knob.min!==undefined&&knob.max!==undefined&&<Slider disabled={disabled} ariaLabelledByForHandle={`${id}-label`} data-testid={`${id}-slider`} min={knob.min} max={knob.max} step={knob.type==='integer'?1:0.1} value={value as number} onChange={next=>{report(true);onChange(next);}}/>}
     <InputNumber disabled={disabled} aria-labelledby={`${id}-label`} aria-invalid={invalid} data-testid={id} min={knob.min} max={knob.max} precision={knob.type==='integer'?0:undefined} step={knob.type==='integer'?1:0.1} value={value as number} onInput={raw=>report(raw.trim()!==''&&numberValid(Number(raw)))} onChange={next=>{const valid=numberValid(next);report(valid);if(valid)onChange(next!);}}/>
    </div>}
  {invalid&&<span className="wb-field-error" role="alert">{locale==='zh-CN'?'请输入范围内的有效数值。':'Enter a valid number within the bounds.'}</span>}
  {hint&&<span className="template-knob-hint">{hint}</span>}
 </div>;
}
