import type { ReactNode } from "react";
import type { AsyncState } from "../hooks/useApi";

export function Loading({ label = "Fetching" }: { label?: string }) {
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="state__title">{label}…</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state state--error" role="alert">
      <span className="state__title">Signal lost</span>
      <span className="state__msg">{message}</span>
      {onRetry ? (
        <button className="btn" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, message }: { title: string; message?: string }) {
  return (
    <div className="state">
      <span className="state__title">{title}</span>
      {message ? <span className="state__msg">{message}</span> : null}
    </div>
  );
}

/** Render children only once data resolves; otherwise the matching state.
    Every fetch in the app funnels through this so loading/error are uniform. */
export function Async<T>({
  state,
  loadingLabel,
  children,
}: {
  state: AsyncState<T>;
  loadingLabel?: string;
  children: (data: T) => ReactNode;
}) {
  if (state.error) return <ErrorState message={state.error} onRetry={state.reload} />;
  if (state.loading && state.data === null) return <Loading label={loadingLabel} />;
  if (state.data === null) return <EmptyState title="No data" />;
  return <>{children(state.data)}</>;
}
