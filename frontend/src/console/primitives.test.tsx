import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { Details } from './Details';
import { ConsoleNotifications, useConsoleNotice } from './Notifications';
import ConsoleShell from './ConsoleShell';
import { RecordFields } from './Details';
import { I18nProvider } from '../i18n/I18nProvider';
afterEach(()=>{cleanup();vi.useRealTimers();});
it('loads details on demand and retains an unfinished buffer across closing',async()=>{
 render(<Details title="Run details"><textarea aria-label="Raw buffer" defaultValue="kernel-run-payload" /></Details>);
 expect(screen.queryByLabelText('Raw buffer')).toBeNull();
 const trigger=screen.getByTestId('details-trigger');fireEvent.click(trigger);
 expect(screen.getByRole('dialog',{name:'Run details · details'})).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('Raw buffer'),{target:{value:'{unfinished'}});
 fireEvent.click(screen.getByRole('button',{name:'Close'}));await waitFor(()=>expect(trigger).toHaveFocus());
 fireEvent.click(trigger);expect(screen.getByLabelText('Raw buffer')).toHaveValue('{unfinished');
});
it('T4-12: uses a glass dialog with structured fields and returns focus on Escape',async()=>{
 localStorage.setItem('aeroagentsim.locale','en-US');
 render(<I18nProvider><Details title="Admission receipt"><RecordFields record={{status:'applied',execution_status:'succeeded',execution_result:{injection_point:'restaurant_delay'}}}/></Details></I18nProvider>);
 const trigger=screen.getByTestId('details-trigger');fireEvent.click(trigger);
 const dialog=await screen.findByRole('dialog');
 expect(dialog).toHaveClass('wb-glass');
 expect(dialog.querySelector('.wb-record-fields')).toBeTruthy();
 expect(dialog).toHaveTextContent('Execution result');expect(dialog).toHaveTextContent('Restaurant Delay');
 expect(dialog.querySelector('.ant-drawer')).toBeNull();
 fireEvent.keyDown(dialog,{key:'Escape'});
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 await waitFor(()=>expect(trigger).toHaveFocus());
 localStorage.clear();
});
it('shows actual notices without extending their expiry as newer notices arrive',async()=>{
 vi.useFakeTimers();
 function Probe(){const notify=useConsoleNotice();return <button onClick={()=>notify({kind:'error',message:'Admission rejected'})}>Notify</button>;}
 render(<ConsoleNotifications><Probe/></ConsoleNotifications>);
 expect(screen.getByTestId('console-notices')).toBeEmptyDOMElement();fireEvent.click(screen.getByText('Notify'));
 await act(async()=>{await vi.advanceTimersByTimeAsync(5000);});fireEvent.click(screen.getByText('Notify'));expect(screen.getAllByRole('alert')).toHaveLength(2);
 await act(async()=>{await vi.advanceTimersByTimeAsync(1001);});expect(screen.getAllByRole('alert')).toHaveLength(1);
});
it('preserves API and selected run when navigating to Inspect, hiding its technical id',()=>{
 render(<MemoryRouter initialEntries={['/runs/run-secret?api=http%3A%2F%2Fapi&mode=live']}><ConsoleShell activeRun={{pathname:'/runs/run-secret',search:'?api=http%3A%2F%2Fapi&mode=live'}}><p>Contents</p></ConsoleShell></MemoryRouter>);
 expect(screen.getByTestId('nav-inspect')).toHaveAttribute('href','/inspect/run-secret?api=http%3A%2F%2Fapi&mode=live&view=expert');expect(screen.getByTestId('nav-studio')).toHaveAttribute('href','/studio?api=http%3A%2F%2Fapi');expect(screen.queryByText('run-secret')).toBeNull();
});
