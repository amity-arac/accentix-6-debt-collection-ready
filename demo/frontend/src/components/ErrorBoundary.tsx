import { Component, type ReactNode } from "react";
import { RotateCcw, RefreshCw } from "lucide-react";

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.error("[ErrorBoundary]", error, info.componentStack);
    }
  }

  private handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary-card">
          <h2 className="error-boundary-title">Something went wrong</h2>
          <p className="error-boundary-body">
            The demo hit an unexpected error. Reload the page to recover.
          </p>
          {import.meta.env.DEV && (
            <pre className="error-boundary-stack">
              {this.state.error.message}
            </pre>
          )}
          <div className="error-boundary-actions">
            <button
              type="button"
              className="btn"
              onClick={this.handleReload}
            >
              <RefreshCw size={14} aria-hidden="true" /> Reload page
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => this.setState({ error: null })}
            >
              <RotateCcw size={14} aria-hidden="true" /> Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}
