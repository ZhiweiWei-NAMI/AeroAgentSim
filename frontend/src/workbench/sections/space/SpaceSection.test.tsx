import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { WorkbenchApi } from '../../api';
import { SpaceSection } from '../SpaceSection';
import type { SectionProps } from '../types';
import { forceSvgRenderer } from './leafletTestSetup';

beforeAll(forceSvgRenderer);
afterEach(cleanup);

function props(zh = false): SectionProps {
  return { api: Object.assign(new WorkbenchApi('http://api.test'), { request: vi.fn(async () => ({ extracts: [] })) }),
    project: { id: 'w1', revision: 3 }, draft: { presentation: [] }, edit: vi.fn(),
    issues: [], L: (en: string, cn: string) => zh ? cn : en, zh, locale: zh ? 'zh-CN' : 'en-US',
  } as unknown as SectionProps;
}

describe('Scene & space copy and Advanced (D1 / D2)', () => {
  it('keeps presentation under a collapsed Advanced disclosure with one label and clear city choices', async () => {
    const { container } = render(<SpaceSection {...props()} />);
    const advanced = screen.getByText('Advanced').closest('details')!;
    expect(advanced).not.toHaveAttribute('open');
    expect(screen.getAllByText('Presentation')).toHaveLength(1);
    expect(screen.getByText('Presentation')).not.toBeVisible();
    expect(screen.getByRole('option', { name: 'Selected region' })).toBeVisible();
    expect(screen.getByRole('option', { name: 'Huangpu city model' })).toBeVisible();
    expect(container.textContent).not.toContain('descriptor');
    advanced.open = true; fireEvent(advanced, new Event('toggle'));
    expect(screen.getByText('Presentation')).toBeVisible();
    await waitFor(() => expect(screen.queryByText('Loading the extract catalog…')).toBeNull());
  });

  it('renders only Chinese labels/help in the Chinese locale', async () => {
    render(<SpaceSection {...props(true)} />);
    expect(screen.getByText('高级')).toBeVisible();
    expect(screen.getByRole('option', { name: '所选区域' })).toBeVisible();
    expect(screen.queryByText('Advanced')).toBeNull();
    expect(screen.queryByText(/bundled Huangpu city model/)).toBeNull();
    await waitFor(() => expect(screen.queryByText('正在加载提取数据目录…')).toBeNull());
  });
});
