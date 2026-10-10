import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { StudioApi } from '../api';
import { PredicatesStep, expressionLabel, predicateUses } from './PredicatesStep';

vi.mock('../../i18n/messages', async () => ({ default: { 'zh-CN': (await import('../../i18n/studio-messages')).default } }));
beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
});
function Editor({ initial }: { initial: Record<string, any> }) {
  const [scenario, setScenario] = useState(initial);
  const workspace = { id: 'workspace', name: 'Predicates', scenario };
  return <I18nProvider><PredicatesStep workspace={workspace} api={new StudioApi('/')} scenario={scenario} types={[]} fields={[]} relations={[]} save={async () => workspace} onApply={() => {}} onChange={setScenario} /></I18nProvider>;
}
it('can create the first predicate in an empty inline package', async () => {
  render(<Editor initial={{ behaviours: [{ id: 'conditions', predicates: {}, chains: {}, extension: 'retained' }] }} />);
  expect(screen.getByRole('button', { name: 'Add predicate to conditions' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('New predicate ID'), { target: { value: ' first-condition ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add predicate to conditions' }));
  expect(await screen.findByText('Edit First Condition')).toBeInTheDocument();
  expect(screen.getByLabelText('New predicate ID')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('New predicate ID'), { target: { value: 'first-condition' } });
  expect(screen.getByRole('button', { name: 'Add predicate to conditions' })).toBeDisabled();
});
it('offers a valid inline package when the draft has no behaviour packages', async () => {
  render(<Editor initial={{ extension: { keep: true } }} />);
  fireEvent.click(screen.getByRole('button', { name: 'Add inline behaviour package' }));
  expect(await screen.findByRole('button', { name: 'Add predicate to conditions' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('New predicate ID'), { target: { value: 'ready' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add predicate to conditions' }));
  await waitFor(() => expect(screen.getByText('Edit Ready')).toBeInTheDocument());
});
it('localizes empty state, first-predicate controls and summaries without translating IDs', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  render(<Editor initial={{ behaviours: [{ id: 'conditions', predicates: {}, chains: {} }] }} />);
  expect(screen.getByText('场景谓词')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '向 conditions 添加谓词' })).toBeInTheDocument();
  expect(screen.getByLabelText('新谓词 ID')).toBeInTheDocument();
  expect(expressionLabel({ parameter: 'radius' }, 'zh-CN')).toBe('参数 Radius');
  expect(predicateUses({ chains: { 'chain.id': { trigger: { predicate: 'ready', edge: 'entered' }, transitions: [] } } }, 'ready', 'zh-CN')).toEqual(['事件链 chain.id · 触发：变为真']);
});
