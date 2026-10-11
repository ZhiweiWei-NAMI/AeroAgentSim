import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { Markdown } from './Markdown';

afterEach(cleanup);
it('keeps wrapped guide bullets and blank-separated bullets in one list', () => {
  const { container } = render(<Markdown source={'## What happens\n\n- One spraying task crosses the field via the `coverage` work route\n  (boustrophedon rows).\n- The battery capacity is sized for a partial treatment; when the state of\n  charge falls below the low threshold the drone returns to `home-pad`.\n\n- A wind gust pauses spraying.\n\nA separate paragraph.'} />);
  const items = within(screen.getByRole('list')).getAllByRole('listitem');
  expect(items).toHaveLength(3);
  expect(items[0]).toHaveTextContent('coverage work route (boustrophedon rows).');
  expect(items[1]).toHaveTextContent('state of charge falls below');
  expect(container.querySelectorAll('p')).toHaveLength(1);
  expect(screen.getByText('A separate paragraph.')).toBeVisible();
});
it('preserves ordered steps, readable code and table cells without rendering raw HTML', () => {
  render(<Markdown source={'1. Validate the draft\n   before saving.\n2. Save version\n\n```json\n{"count":2}\n```\n\n| 控制项 | 默认值 |\n| --- | --- |\n| `fleet_size` | 2 |\n\n<script>bad()</script>'} />);
  expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('Validate the draft before saving.');
  expect(screen.getByRole('table')).toHaveTextContent('控制项');
  expect(screen.getByText('{"count":2}').closest('pre')).not.toBeNull();
  expect(document.querySelector('script')).toBeNull();
});
