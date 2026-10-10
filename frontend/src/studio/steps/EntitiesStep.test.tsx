import {useState} from 'react';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {I18nProvider} from '../../i18n/I18nProvider';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {EntitiesStep} from './EntitiesStep';
import {StudioApi} from '../api';

beforeEach(()=>{
 window.localStorage.setItem('aeroagentsim.locale','en-US');
 vi.stubGlobal('matchMedia',vi.fn((media:string)=>({media,matches:false,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn()})));
 const computed=window.getComputedStyle.bind(window);
 vi.spyOn(window,'getComputedStyle').mockImplementation(element=>computed(element));
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});

test('initial fields use inherited registry schemas and preserve unrecognized authored facts',async()=>{
 const onChange=vi.fn();
 const scenario={registry:{types:[{id:'Vehicle',name:'Vehicle',parents:['Object'],abstract:false},{id:'Object',parents:[],abstract:true}],fields:[{id:'energy',type:'Object',schema:{type:'number'}}]},entities:[{id:'alpha',type:'Vehicle',facts:{extension:'keep'}}]};
 const {container}=render(<EntitiesStep scenario={scenario} onChange={onChange} api={new StudioApi('http://unused')}/>);
 fireEvent.click(container.querySelector('.guided-entities-group-label')!);
 fireEvent.click(screen.getByRole('button',{name:'Inspect'}));
 const picker=await screen.findByRole('combobox',{name:'New initial field'});
 fireEvent.mouseDown(picker);
 fireEvent.click(await screen.findByText('Energy'));
 const input=await screen.findByLabelText('Initial field value');
 expect(screen.getByRole('button',{name:'Add initial field'})).toBeDisabled();
 fireEvent.change(input,{target:{value:'12'}});
 fireEvent.click(screen.getByRole('button',{name:'Add initial field'}));
 await waitFor(()=>expect(onChange).toHaveBeenCalledTimes(1));
 expect(onChange.mock.calls[0][0].entities[0].facts).toEqual({extension:'keep',energy:{$number:'12'}});
});


test('an AeroGraph deep link opens the actual workspace entity',async()=>{
 const scenario={registry:{types:[{id:'Vehicle',name:'Vehicle',parents:[],abstract:false}],fields:[]},entities:[{id:'alpha',type:'Vehicle',facts:{}}]};
 render(<EntitiesStep initialEntityId="alpha" scenario={scenario} onChange={vi.fn()} api={new StudioApi('http://unused')}/>);
 expect(await screen.findByRole('dialog')).toHaveTextContent('Alpha');
 expect(screen.getByRole('dialog')).toHaveTextContent('Type ancestry:');
});

test('existing field edits retain invalid text and report validity without overwriting the last valid draft',async()=>{
 const changed=vi.fn(),validity=vi.fn();
 const initial={registry:{types:[{id:'Vehicle',name:'Vehicle',parents:[],abstract:false}],fields:[{id:'energy',type:'Vehicle',schema:{type:'number'}}]},entities:[{id:'alpha',type:'Vehicle',facts:{energy:10,extension:'keep'}}]};
 function Editor(){const [scenario,setScenario]=useState(initial);return <I18nProvider><EntitiesStep initialEntityId="alpha" scenario={scenario} onChange={next=>{changed(next);setScenario(next as typeof initial);}} onValidityChange={validity} api={new StudioApi('http://unused')}/></I18nProvider>;}
 render(<Editor/>);
 const input=await screen.findByLabelText('Energy');
 fireEvent.change(input,{target:{value:'not-a-number'}});
 expect(input).toHaveValue('not-a-number');
 expect(changed).not.toHaveBeenCalled();
 await waitFor(()=>expect(validity).toHaveBeenLastCalledWith(false));
 fireEvent.change(input,{target:{value:'11'}});
 await waitFor(()=>expect(validity).toHaveBeenLastCalledWith(true));
 expect(changed.mock.lastCall?.[0].entities[0].facts).toEqual({energy:{$number:'11'},extension:'keep'});
});

test('an initial field can be removed without discarding unknown authored facts',async()=>{
 const changed=vi.fn();
 const scenario={extension:{keep:true},registry:{types:[{id:'Vehicle',name:'Vehicle',parents:[],abstract:false}],fields:[{id:'energy',type:'Vehicle',schema:{type:'number'}}]},entities:[{id:'alpha',type:'Vehicle',facts:{energy:10,opaque:{keep:true}}}]};
 render(<I18nProvider><EntitiesStep initialEntityId="alpha" scenario={scenario} onChange={changed} api={new StudioApi('http://unused')}/></I18nProvider>);
 fireEvent.click(await screen.findByRole('button',{name:'Remove initial field Energy'}));
 expect(changed.mock.lastCall?.[0].entities[0].facts).toEqual({opaque:{keep:true}});
 expect(changed.mock.lastCall?.[0].extension).toEqual({keep:true});
});

test('an entity deep link is safe when the draft has no entities collection',()=>{
 render(<EntitiesStep initialEntityId="missing" scenario={{registry:{types:[],fields:[]}}} onChange={vi.fn()} api={new StudioApi('http://unused')}/>);
 expect(screen.getByText('No entities yet. Add one from a registry type below, or place it in Scene.')).toBeInTheDocument();
 expect(screen.queryByRole('dialog')).toBeNull();
});
