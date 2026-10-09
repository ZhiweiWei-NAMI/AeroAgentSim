import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {EntitiesStep} from './EntitiesStep';
import {StudioApi} from '../api';

beforeEach(()=>{
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
