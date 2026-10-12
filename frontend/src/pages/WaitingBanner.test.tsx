import { render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { WaitingBanner } from './WaitingBanner';
import { I18nProvider } from '../i18n/I18nProvider';
afterEach(()=>localStorage.clear());

it('shows a waiting banner for a live operator watermark hold and a terminal input timeout', () => {
  const waiting = render(<WaitingBanner waiting={{ stream_ids: ['operator'], at_ns: '2500000000' }} />);
  expect(screen.getByTestId('waiting-banner')).toBeTruthy();
  expect(screen.getByText('Waiting for live inputs')).toBeTruthy();
  expect(screen.getByText(/waiting for live inputs at 00:02.5/)).toBeTruthy();
  expect(screen.queryByText(/Choose an event to continue/)).toBeNull();
  expect(screen.getByRole('button', {name:'Introduce an event'})).toBeTruthy();
  waiting.unmount();
  render(<WaitingBanner waiting={{ stream_ids: ['operator'], at_ns:'2500000000' }} terminal />);
  expect(screen.getByTestId('input-timeout-banner')).toBeTruthy();
  expect(screen.getByText(/Input wait timed out/)).toBeTruthy();
});
it('describes the actual operator boundary and optional action in Chinese',()=>{
 localStorage.setItem('aeroagentsim.locale','zh-CN');
 const view=render(<I18nProvider><WaitingBanner waiting={{stream_ids:['operator'],at_ns:'2500000000'}}/></I18nProvider>);
 expect(screen.getByText('等待实时输入')).toBeTruthy();expect(screen.getByText('场景在 00:02.5 等待实时输入。你可以引入一个事件。')).toBeTruthy();expect(screen.getByRole('button',{name:'输入一个事件'})).toBeTruthy();view.unmount();
});
