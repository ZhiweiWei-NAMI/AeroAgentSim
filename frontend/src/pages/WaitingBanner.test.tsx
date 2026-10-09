import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { WaitingBanner } from './WaitingBanner';

it('shows a waiting banner for a live operator watermark hold and a terminal input timeout', () => {
  const waiting = render(<WaitingBanner waiting={{ stream_ids: ['operator'], at_ns: '2500000000' }} />);
  expect(screen.getByTestId('waiting-banner')).toBeTruthy();
  expect(screen.getByText(/Ready for an operator event at/)).toBeTruthy();
  expect(screen.getByRole('button', {name:'Inject event'})).toBeTruthy();
  waiting.unmount();
  render(<WaitingBanner waiting={{ stream_ids: ['operator'], at_ns:'2500000000' }} terminal />);
  expect(screen.getByTestId('input-timeout-banner')).toBeTruthy();
  expect(screen.getByText(/Input wait timed out/)).toBeTruthy();
});
