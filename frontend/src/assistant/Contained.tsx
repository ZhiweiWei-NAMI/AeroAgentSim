import { Component, type ReactNode } from 'react';

/** Keeps a failed lazy import or render inside its panel; changing the identity retries. */
export class Contained extends Component<{ resetKey: unknown; label?: string; fallback: (retry: () => void) => ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.error(`${this.props.label ?? 'Plan board'} failed`, error); }
  componentDidUpdate(previous: { resetKey: unknown }) { if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ failed: false }); }
  render() { return this.state.failed ? this.props.fallback(() => this.setState({ failed: false })) : this.props.children; }
}
