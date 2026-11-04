import { useCallback, useEffect, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** the last data stays through a reload (see useApi); Async renders it inside one
      persistent wrapper so the controls inside keep their focus */
  keep?: boolean;
}

/**
 * Run an async loader whenever `deps` change, tracking loading/error/data.
 * Stale responses are dropped (cancel flag) so fast successive queries — e.g.
 * typing in the resolver search — never render an out-of-order result. `data` is
 * also cleared at the start of each run so a dep change (e.g. navigating between
 * two objects) never renders the previous result under the new identity while the
 * next one loads. A paged or sorted list passes `keep`: the last page stays in
 * `data` while the next loads (Async dims it), so the pager and sort buttons that
 * fired the load stay mounted and keep the keyboard's focus.
 */
export function useApi<T>(
  loader: () => Promise<T>,
  deps: unknown[],
  opts?: { keep?: boolean },
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // loader identity changes every render; gate the effect on caller-supplied deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (!opts?.keep) setData(null); // drop the prior identity's data so it never shows under a new dep
    run()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Request failed");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [run, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, reload, keep: opts?.keep };
}
