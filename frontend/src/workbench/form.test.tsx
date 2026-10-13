import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { JsonEditor, Leaf, TreeForm, unitHint } from './form';
import { stringifyLossless } from '../feeds/lossless-json';

afterEach(cleanup);
describe('draft form values', () => {
  it('commits numeric and tagged leaves on blur or Enter, keeping the raw text while typing', () => {
    const change = vi.fn();
    render(<Leaf path="/run/until_ns" label="until_ns" value={{ $integer: '12345678901234567890' }} onChange={change} />);
    const input = screen.getByRole('textbox', { name: /until_ns/ });
    // Typing a partial value never commits or reverts (T4-02/T1-019).
    fireEvent.change(input, { target: { value: '1234567890123456789' } });
    expect(change).not.toHaveBeenCalled();
    expect(input).toHaveValue('1234567890123456789');
    fireEvent.change(input, { target: { value: '12345678901234567891' } });
    fireEvent.blur(input);
    expect(change).toHaveBeenLastCalledWith('/run/until_ns', { $integer: '12345678901234567891' });
    change.mockClear();
    fireEvent.change(input, { target: { value: '1.5' } });
    fireEvent.blur(input);
    expect(change).not.toHaveBeenCalled();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toBeVisible();
    expect(input).toHaveValue('1.5'); // the invalid raw text stays for correction
  });
  it('keeps a leading minus while typing a negative number and commits it on blur', () => {
    const change = vi.fn(); render(<Leaf path="/east" label="east_m" value={0} onChange={change} />);
    const input = screen.getByLabelText(/east_m/);
    fireEvent.change(input, { target: { value: '-' } });
    expect(change).not.toHaveBeenCalled();
    expect(input).toHaveValue('-'); // no coercion to 0, no drop (T4-02)
    fireEvent.change(input, { target: { value: '-12.5' } });
    fireEvent.blur(input);
    expect(change).toHaveBeenLastCalledWith('/east', -12.5);
  });
  it('rejects overflowing numbers and leaves the last valid value untouched', () => {
    const change = vi.fn(); render(<Leaf path="/rate" label="rate" value={0.08} onChange={change} />);
    const input = screen.getByLabelText('rate');
    fireEvent.change(input, { target: { value: '1e999' } });
    fireEvent.blur(input);
    expect(change).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '0.25' } });
    fireEvent.blur(input);
    expect(change).toHaveBeenLastCalledWith('/rate', 0.25);
  });
  it('never turns empty vector members into zero and applies typed coordinates on commit', () => {
    const change = vi.fn(); render(<TreeForm value={[12, 23, 34]} path="/position" label="position" onChange={change} />);
    const input = screen.getByRole('textbox', { name: /position/ });
    fireEvent.change(input, { target: { value: '12, , 34' } });
    expect(change).not.toHaveBeenCalled();
    expect(input).toHaveValue('12, , 34'); // raw text kept while typing; error waits for commit
    fireEvent.change(input, { target: { value: '-, 23, 34' } });
    expect(input).toHaveValue('-, 23, 34'); // raw text survives; no silent rewrite
    fireEvent.blur(input);
    expect(change).not.toHaveBeenCalled();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    fireEvent.change(input, { target: { value: '12.5, -23, 34' } });
    fireEvent.blur(input);
    expect(change).toHaveBeenLastCalledWith('/position', [12.5, -23, 34]);
  });
  it('does not offer an input for an empty collection', () => {
    render(<TreeForm value={[]} path="/commands" label="commands" onChange={vi.fn()} />);
    expect(screen.queryByRole('textbox')).toBeNull(); expect(screen.getByText('[ ]')).toBeVisible();
  });
  it('uses rates and speed units before matching seconds', () => {
    expect(unitHint('rate_per_s')).toBe('1/s'); expect(unitHint('speed_m_s')).toBe('m/s');
    expect(unitHint('acceleration_m_s2')).toBe('m/s²'); expect(unitHint('until_ns')).toBe('ns');
  });
  it('keeps large integer and behaviour floating kinds through JSON editing', () => {
    const apply = vi.fn(); render(<JsonEditor label="behaviours" value={[]} onApply={apply} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '[{"parameters":{"n":12345678901234567890,"speed":12.0}}]' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply JSON' }));
    expect(stringifyLossless(apply.mock.calls[0][0])).toBe('[{"parameters":{"n":12345678901234567890,"speed":12.0}}]');
  });
  it('creates closed branch fields only when opened and retains invalid text when collapsed', async () => {
    const view = render(<TreeForm value={[1, 2, 3, 4, 5]} path="/geometry" label="geometry" depth={1} onChange={vi.fn()} />);
    const branch = view.container.querySelector('details')!;
    expect(screen.queryByRole('textbox')).toBeNull();
    branch.open = true; fireEvent(branch, new Event('toggle'));
    const input = await screen.findByLabelText('0');
    fireEvent.change(input, { target: { value: '' } });
    expect(input).toHaveValue(''); // kept raw while typing
    fireEvent.blur(input);
    expect(input).toHaveAttribute('aria-invalid', 'true'); // empty is not a number: error on commit
    branch.open = false; fireEvent(branch, new Event('toggle'));
    branch.open = true; fireEvent(branch, new Event('toggle'));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });
});
