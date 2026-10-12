import { Component, type ReactNode } from 'react';

/** Keeps a failure inside the plan board from blanking the workbench; a new plan version retries. */
export class Contained extends Component<{ resetKey: unknown; fallback: (retry: () => void) => ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.error('Plan board failed', error); }
  componentDidUpdate(previous: { resetKey: unknown }) { if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ failed: false }); }
  render() { return this.state.failed ? this.props.fallback(() => this.setState({ failed: false })) : this.props.children; }
}
