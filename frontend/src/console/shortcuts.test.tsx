import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {RunShortcuts,SimpleShortcuts,ShortcutsHelp} from './shortcuts';
import {I18nProvider} from '../i18n/I18nProvider';
afterEach(()=>{cleanup();localStorage.clear();});
const handlers=()=>({onPlay:vi.fn(),onFollow:vi.fn(),onDirector:vi.fn(),onCinema:vi.fn(),onWhy:vi.fn(),onLanguage:vi.fn(),onExitCinema:vi.fn()});

describe('Simple keyboard shortcuts',()=>{
 it('dispatches Space, F, D, C, W, L and Esc once and prevents native actions',()=>{
  const p=handlers();render(<SimpleShortcuts {...p}/>);
  for(const [key,handler] of [[' ',p.onPlay],['F',p.onFollow],['d',p.onDirector],['C',p.onCinema],['w',p.onWhy],['L',p.onLanguage],['Escape',p.onExitCinema]] as const){const event=new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true});document.body.dispatchEvent(event);expect(handler).toHaveBeenCalledOnce();expect(event.defaultPrevented).toBe(true);}
 });
 it('ignores typing, comboboxes, visible dialogs, repeated keys and modifier chords',()=>{
  const p=handlers();const view=render(<><input aria-label="text"/><div contentEditable data-testid="editor"/><div role="combobox" data-testid="combo"/><SimpleShortcuts {...p}/></>);
  fireEvent.keyDown(screen.getByLabelText('text'),{key:'c'});fireEvent.keyDown(screen.getByTestId('editor'),{key:'f'});fireEvent.keyDown(screen.getByTestId('combo'),{key:'l'});
  for(const changes of [{repeat:true},{ctrlKey:true},{altKey:true},{metaKey:true}])fireEvent.keyDown(document.body,{key:'d',...changes});
  view.rerender(<><div role="dialog"/><SimpleShortcuts {...p}/></>);fireEvent.keyDown(document.body,{key:' '});
  for(const handler of Object.values(p))expect(handler).not.toHaveBeenCalled();
  view.rerender(<><div role="dialog" aria-hidden="true"/><SimpleShortcuts {...p}/></>);fireEvent.keyDown(document.body,{key:' '});expect(p.onPlay).toHaveBeenCalledOnce();
  view.rerender(<><div style={{display:'none'}}><div role="dialog"/></div><SimpleShortcuts {...p}/></>);fireEvent.keyDown(document.body,{key:'c'});expect(p.onCinema).toHaveBeenCalledOnce();
 });
 it('does not double-toggle Space through the force-mounted operations drawer',()=>{
  const p=handlers(),live=vi.fn();render(<><section data-testid="simple-run-view"><SimpleShortcuts {...p}/></section><RunShortcuts enabled onToggle={live} onInject={()=>{}}/></>);
  fireEvent.keyDown(document.body,{key:' '});expect(p.onPlay).toHaveBeenCalledOnce();expect(live).not.toHaveBeenCalled();
 });
 it('uses current callbacks and cleans up its document listener',()=>{
  const p=handlers(),next=handlers(),view=render(<SimpleShortcuts {...p}/>);view.rerender(<SimpleShortcuts {...next}/>);fireEvent.keyDown(document.body,{key:'c'});expect(next.onCinema).toHaveBeenCalledOnce();expect(p.onCinema).not.toHaveBeenCalled();view.unmount();fireEvent.keyDown(document.body,{key:'c'});expect(next.onCinema).toHaveBeenCalledOnce();
 });
 it('documents the watch shortcuts in the existing bilingual Help dialog',async()=>{
  localStorage.setItem('aeroagentsim.locale','zh-CN');render(<I18nProvider><ShortcutsHelp/></I18nProvider>);fireEvent.keyDown(document.body,{key:'?'});
  expect(await screen.findByRole('dialog',{name:'快捷键'})).toHaveTextContent('切换影院模式');expect(screen.getByTestId('shortcuts-help')).toHaveTextContent('查看已选实体最新事件的原因');
 });
});
