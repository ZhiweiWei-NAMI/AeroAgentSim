import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { Details } from './Details';
import { ConsoleNotifications, useConsoleNotice } from './Notifications';
import { RunShortcuts, ShortcutsHelp } from './shortcuts';
import ConsoleShell from './ConsoleShell';
afterEach(()=>{cleanup();vi.useRealTimers();});
it('loads details on demand and retains an unfinished buffer across closing',async()=>{
 render(<Details title="Run details"><textarea aria-label="Raw buffer" defaultValue="kernel-run-payload" /></Details>);
 expect(screen.queryByLabelText('Raw buffer')).toBeNull();
 const trigger=screen.getByTestId('details-trigger');fireEvent.click(trigger);
 fireEvent.change(screen.getByLabelText('Raw buffer'),{target:{value:'{unfinished'}});
 fireEvent.click(screen.getByRole('button',{name:'Close'}));await waitFor(()=>expect(trigger).toHaveFocus());
 fireEvent.click(trigger);expect(screen.getByLabelText('Raw buffer')).toHaveValue('{unfinished');
});
it('guards shortcuts during typing, replay and dialogs, then invokes live controls',async()=>{
 const toggle=vi.fn(),inject=vi.fn();
 const {rerender}=render(<><input aria-label="Input"/><RunShortcuts enabled={false} onToggle={toggle} onInject={inject}/><ShortcutsHelp/></>);
 const replay=new KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true});document.body.dispatchEvent(replay);expect(replay.defaultPrevented).toBe(false);
 rerender(<><input aria-label="Input"/><RunShortcuts enabled onToggle={toggle} onInject={inject}/><ShortcutsHelp/></>);
 fireEvent.keyDown(screen.getByLabelText('Input'),{key:' '});expect(toggle).not.toHaveBeenCalled();
 fireEvent.keyDown(document.body,{key:'?'});await screen.findByRole('dialog');fireEvent.keyDown(document.body,{key:' '});expect(toggle).not.toHaveBeenCalled();
 fireEvent.click(screen.getByLabelText('Close'));await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
 fireEvent.keyDown(document.body,{key:' '});fireEvent.keyDown(document.body,{key:'i'});expect(toggle).toHaveBeenCalledTimes(1);expect(inject).toHaveBeenCalledTimes(1);
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
 expect(screen.getByTestId('nav-inspect')).toHaveAttribute('href','/inspect/run-secret?api=http%3A%2F%2Fapi&mode=live');expect(screen.getByTestId('nav-studio')).toHaveAttribute('href','/studio?api=http%3A%2F%2Fapi');expect(screen.queryByText('run-secret')).toBeNull();
});
