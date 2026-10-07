import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Without this, one render error unmounts the whole app and the examiner sees a blank page with
 * no clue why. It states the error instead, and offers a reload.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI crashed:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="min-h-screen text-ink grid place-items-center p-6">
        <div className="card max-w-2xl w-full p-6">
          <div className="text-[11px] uppercase tracking-wider text-muted">Something broke on this screen</div>
          <div className="mt-1 text-lg font-semibold">The dashboard hit an error</div>
          <pre className="mt-3 text-xs whitespace-pre-wrap break-words text-muted">{error.stack || error.message}</pre>
          <button className="btn-accent mt-4" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
