import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { BehaviourEditor } from './BehaviourEditor';
import { applicableFields, applicableRelations, patch, type Draft } from './model';
const draft:Draft={format:'aeroagentsim.behaviour-package/v1',id:'example',revision:1,unknown:{enabled:false,value:null},predicates:{ready:{profile:'committed_reactive/v1',roles:{task:'child'},expression:{op:'eq',args:[{field:'phase',role:'task',path:[]},{literal:'queued'}]}}},chains:{response:{roles:{task:'child'},trigger:{predicate:'ready',edge:'entered'},initial:'waiting',terminal:['done'],states:['waiting','done'],transitions:[{id:'launch',from:'waiting',on:{instance:'activated'},guard:'ready',to:'done',actions:[],unknownTransition:{keep:true}}]}},bindings:[],conflicts:[],injection_points:[]};
function Harness(){const [value,setValue]=useState(draft);return <><BehaviourEditor value={value} onChange={setValue}/><pre data-testid="model">{JSON.stringify(value)}</pre></>;}
it('shares state-machine graph, transition forms and raw model while retaining unsupported content',()=>{
 render(<Harness/>);fireEvent.click(screen.getByRole('button',{name:/^chains$/}));
 expect(screen.getByLabelText('Graph transition launch')).toBeTruthy();fireEvent.change(screen.getByLabelText('Transition 0 id'),{target:{value:'finish'}});
 expect(screen.getByLabelText('Graph transition finish')).toBeTruthy();expect(screen.getByTestId('model')).toHaveTextContent('unknownTransition');
 fireEvent.click(screen.getByRole('button',{name:/^raw$/}));fireEvent.click(screen.getByTestId('details-trigger'));const raw=screen.getByLabelText('Behaviour package JSON') as HTMLTextAreaElement;expect(JSON.parse(raw.value).unknown).toEqual({enabled:false,value:null});
});
it('preserves unsupported AST operators and filters inherited fields by real ancestry',()=>{
 const authored={...draft,predicates:{ready:{profile:'committed_reactive/v1',roles:{},expression:{op:'futureDialect',extra:{keep:true}}}}};render(<BehaviourEditor value={authored} onChange={()=>{}}/>);
 expect(screen.getByText(/Unsupported AST content retained/)).toBeTruthy();
 expect(applicableFields('child',[{id:'child',parents:['parent'],abstract:false},{id:'parent',parents:[],abstract:true}],[{id:'inherited',type:'parent'},{id:'other',type:'unrelated'}])).toEqual([{id:'inherited',type:'parent'}]);
 expect(applicableRelations('child','child',[{id:'child',parents:['parent'],abstract:false}],[{id:'actual',source_type:'parent',target_type:'parent'},{id:'opposite',source_type:'other',target_type:'parent'}])).toEqual([{id:'actual',source_type:'parent',target_type:'parent'}]);
 expect((patch(draft,['chains','response','initial'],'done') as Draft).unknown).toEqual(draft.unknown);
});
it('blocks compiler validation while a raw JSON buffer is invalid',()=>{
 const states:boolean[]=[];render(<BehaviourEditor value={draft} onChange={()=>{}} onValidityChange={valid=>states.push(valid)}/>);
 fireEvent.click(screen.getByRole('button',{name:/^raw$/}));fireEvent.click(screen.getByTestId('details-trigger'));
 fireEvent.change(screen.getByLabelText('Behaviour package JSON'),{target:{value:'{"id":'}});
 expect(screen.getByRole('button',{name:'Validate package on server'})).toBeDisabled();expect(states.at(-1)).toBe(false);
});
