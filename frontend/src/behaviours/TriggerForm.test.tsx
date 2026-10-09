import { render, screen, fireEvent } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import { TriggerForm } from './TriggerForm';
it('switches trigger category while preserving unsupported authored extensions',()=>{
 const change=vi.fn();render(<TriggerForm label="Rule" predicates={['ready']} value={{predicate:'ready',edge:'entered',future:{nullable:null}}} onChange={change}/>);
 fireEvent.change(screen.getByLabelText('Rule category'),{target:{value:'event'}});
 expect(change).toHaveBeenLastCalledWith({event:'',future:{nullable:null}});
});
