import '@testing-library/jest-dom';
import React, { act } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import { I18nProvider } from '../../i18n/I18nProvider';
import RelationGraph from './RelationGraph';

const SAMPLE_GRAPH = {
  nodes: [
    {
      id: 'agent:alpha',
      label: 'Agent Alpha',
      kind: 'agent',
      position: { x: 120, y: 120 },
    },
    {
      id: 'workflow:inspection',
      label: 'Inspection',
      kind: 'workflow',
      position: { x: 420, y: 220 },
    },
  ],
  edges: [
    {
      id: 'edge:alpha:inspection',
      source: 'agent:alpha',
      target: 'workflow:inspection',
      label: 'runs',
    },
  ],
  critical_path: [],
};

function renderGraph(props = {}) {
  return render(
    <I18nProvider>
      <RelationGraph graph={SAMPLE_GRAPH} selectedNodeId="" onNodeSelect={props.onNodeSelect} />
    </I18nProvider>
  );
}

function parseViewBox(svg) {
  return svg.getAttribute('viewBox').split(' ').map(Number);
}

function dispatchPointerEvent(target, type, payload) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, payload);
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

describe('RelationGraph', () => {
  test('zooms on wheel within the graph canvas and prevents default scrolling', () => {
    const { container } = renderGraph();
    const canvas = container.querySelector('.relation-graph-canvas');
    const svg = screen.getByRole('img', { name: 'Workflow-Agent-State relation graph' });

    canvas.getBoundingClientRect = () => ({
      left: 0,
      top: 0,
      width: 600,
      height: 400,
      right: 600,
      bottom: 400,
    });

    const initialViewBox = parseViewBox(svg);
    const wheelEvent = new Event('wheel', { bubbles: true, cancelable: true });
    Object.assign(wheelEvent, {
      deltaY: -120,
      clientX: 200,
      clientY: 160,
    });

    act(() => {
      canvas.dispatchEvent(wheelEvent);
    });

    const nextViewBox = parseViewBox(svg);
    expect(wheelEvent.defaultPrevented).toBe(true);
    expect(nextViewBox[2]).toBeLessThan(initialViewBox[2]);
    expect(nextViewBox[3]).toBeLessThan(initialViewBox[3]);
  });

  test('pans from empty canvas without breaking node selection', () => {
    const onNodeSelect = jest.fn();
    const { container } = renderGraph({ onNodeSelect });
    const canvas = container.querySelector('.relation-graph-canvas');
    const svg = screen.getByRole('img', { name: 'Workflow-Agent-State relation graph' });

    canvas.getBoundingClientRect = () => ({
      left: 0,
      top: 0,
      width: 600,
      height: 400,
      right: 600,
      bottom: 400,
    });
    canvas.setPointerCapture = jest.fn();
    canvas.releasePointerCapture = jest.fn();

    const initialViewBox = parseViewBox(svg);

    dispatchPointerEvent(canvas, 'pointerdown', {
      pointerId: 7,
      clientX: 100,
      clientY: 120,
    });
    dispatchPointerEvent(canvas, 'pointermove', {
      pointerId: 7,
      clientX: 150,
      clientY: 160,
    });
    dispatchPointerEvent(canvas, 'pointerup', {
      pointerId: 7,
      clientX: 150,
      clientY: 160,
    });

    const nextViewBox = parseViewBox(svg);
    expect(nextViewBox[0]).toBeLessThan(initialViewBox[0]);
    expect(nextViewBox[1]).toBeLessThan(initialViewBox[1]);

    fireEvent.click(screen.getByText('Agent Alpha'));
    expect(onNodeSelect).toHaveBeenCalledWith('agent:alpha');
  });
});
