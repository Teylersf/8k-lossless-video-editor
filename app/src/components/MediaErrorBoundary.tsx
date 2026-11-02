import { Component } from "react";
import type { ReactNode } from "react";
import { RefreshCw, TriangleAlert } from "lucide-react";

type Props = { children: ReactNode; resetKey: string; onRetry: () => void };
type State = { failed: boolean };

/** Keep the project, media bin and timeline alive if a viewer operation fails. */
export class MediaErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidUpdate(previous: Props) {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <section className="viewer panel viewer-recovery" role="alert">
        <TriangleAlert size={30} />
        <strong>The viewer needs to reload</strong>
        <p>A media operation failed. Your timeline is still available.</p>
        <button
          onClick={() => {
            this.props.onRetry();
            this.setState({ failed: false });
          }}
        >
          <RefreshCw size={14} /> Reload viewer
        </button>
      </section>
    );
  }
}
