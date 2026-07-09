import { Component, StrictMode, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./theme.css";

/** Screen-level error boundary: a single React fault anywhere in the tree used to unmount the
    whole SPA and leave a black page. Instead, catch it here and render a visible 'terminal fault'
    panel carrying the error message, so a failure degrades to a readable stop rather than a void. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Terminal fault:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="fault" role="alert">
          <div className="fault__box">
            <span className="fault__title">Terminal fault</span>
            <p className="fault__msg">
              The interface hit an unrecoverable error and halted to avoid showing stale or
              partial data.
            </p>
            <pre className="fault__detail">{this.state.error.message}</pre>
            <button className="btn" onClick={() => window.location.reload()}>
              Reload terminal
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const container = document.getElementById("root");
if (!container) throw new Error("root element missing");

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>,
);
