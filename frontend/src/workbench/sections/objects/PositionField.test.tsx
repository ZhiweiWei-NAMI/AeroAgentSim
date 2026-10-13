import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PositionField } from './PositionField';
import { StagedFormWorkspace } from '../useStagedForm';

beforeEach(() => sessionStorage.clear());
afterEach(cleanup);
const L = (en: string, _zh: string) => en;
const path = '/entities/0/facts/lowalt.position';

it('restores incomplete coordinate text and commits only a real complete vector (T1-R3-003)', () => {
  const onChange = vi.fn(), reportValidity = vi.fn();
  const element = <StagedFormWorkspace.Provider value={{ id: 'w', revision: 7 }}>
    <PositionField value={[1, 2, 3]} path={path} onChange={onChange} reportValidity={reportValidity} L={L} />
  </StagedFormWorkspace.Provider>;
  const first = render(element);
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '' } });
  fireEvent.change(screen.getByLabelText('north (m)'), { target: { value: '1e' } });
  fireEvent.blur(screen.getByLabelText('east (m)'));
  expect(onChange).not.toHaveBeenCalled();
  first.unmount();
  render(element);
  expect(screen.getByLabelText('east (m)')).toHaveValue('');
  expect(screen.getByLabelText('north (m)')).toHaveValue('1e');
  expect(screen.getByText('Restored unsaved inputs')).toBeTruthy();
  expect(screen.getByRole('alert')).toHaveTextContent('three finite numbers');
  expect(reportValidity).toHaveBeenCalledWith(path, false);
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '42' } });
  fireEvent.change(screen.getByLabelText('north (m)'), { target: { value: '-25' } });
  fireEvent.blur(screen.getByLabelText('north (m)'));
  expect(onChange).toHaveBeenCalledOnce();
  expect(onChange).toHaveBeenCalledWith(path, [42, -25, 3]);
  expect(Object.keys(sessionStorage)).toHaveLength(0);
});
