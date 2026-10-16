import type { ReactNode } from "react";
import type { AsyncState } from "../hooks/useApi";

// ragged line lengths so a skeleton reads as text-shaped, not as a progress bar
const SKELETON_WIDTHS = [92, 78, 86, 64, 90, 72, 82, 58];

/** Loading state. With `rows`, a skeleton block about the height of what it replaces,
    so the page does not jump when data lands; without, the compact spinner. */
export function Loading({ label = "Fetching", rows }: { label?: string; rows?: number }) {
  if (rows && rows > 0) {
    return (
      <div className="state state--skeleton" role="status" aria-live="polite">
        <span className="state__title">{label}…</span>
        {Array.from({ length: rows }, (_, i) => (
          <span
            key={i}
            className="skeleton"
            aria-hidden="true"
            style={{ width: `${SKELETON_WIDTHS[i % SKELETON_WIDTHS.length]}%` }}
          />
        ))}
      </div>
    );
  }
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="state__title">{label}…</span>
    </div>
  );
}

/** Fetch failures in visitor words. The raw message (it can quote an API path) moves
    to the title attribute; anything unrecognised is shown as-is. */
function visitorMessage(raw: string): string | null {
  // Chrome "Failed to fetch", Firefox "NetworkError when attempting…", Safari "Load failed"
  if (/^(Failed to fetch|NetworkError\b|Load failed)/i.test(raw)) {
    return "The API did not answer. Check your connection, then retry.";
  }
  const timeout = /^No answer from the API within (\d+)s\b/.exec(raw);
  if (timeout) {
    return `The API took longer than ${timeout[1]} seconds to answer. It may be busy; retry in a moment.`;
  }
  return null;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const friendly = visitorMessage(message);
  return (
    <div className="state state--error" role="alert">
      <h2 className="state__title">Signal lost</h2>
      <span className="state__msg" title={friendly ? message : undefined}>
        {friendly ?? message}
      </span>
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
      <h2 className="state__title">{title}</h2>
      {message ? <span className="state__msg">{message}</span> : null}
    </div>
  );
}

/** Render children only once data resolves; otherwise the matching state.
    Every fetch in the app funnels through this so loading/error are uniform. */
export function Async<T>({
  state,
  loadingLabel,
  loadingRows,
  children,
}: {
  state: AsyncState<T>;
  loadingLabel?: string;
  /** skeleton lines while loading (see Loading); omit for the spinner */
  loadingRows?: number;
  children: (data: T) => ReactNode;
}) {
  if (state.error) return <ErrorState message={state.error} onRetry={state.reload} />;
  if (state.loading && state.data === null)
    return <Loading label={loadingLabel} rows={loadingRows} />;
  if (state.data === null) return <EmptyState title="No data" />;
  return <>{children(state.data)}</>;
}
