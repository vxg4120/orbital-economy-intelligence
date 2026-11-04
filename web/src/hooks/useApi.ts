import { useCallback, useEffect, useRef, useState } from "react";

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
 * next one loads — unless `keep` is set: a paged or sorted list keeps its last page
 * in `data` while the next loads (Async dims it), so the pager and sort buttons that
 * fired the load stay mounted and keep the keyboard's focus. `keepKey` names the list
 * the rows belong to (the tab, the group, the filter): they are kept only while it is
 * unchanged, so a tab change shows the loader like any other change of identity
 * instead of the old tab's rows under the new tab's headers. A run that fails drops
 * them too: the retry then starts from the loader like a first fetch.
 */
export function useApi<T>(
  loader: () => Promise<T>,
  deps: unknown[],
  opts?: { keep?: boolean; keepKey?: unknown },
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const keep = Boolean(opts?.keep);
  const keepKey = opts?.keepKey;
  // the key the rows on screen were fetched under
  const keyRef = useRef(keepKey);

  // loader identity changes every render; gate the effect on caller-supplied deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const same = Object.is(keyRef.current, keepKey);
    keyRef.current = keepKey;
    // drop the prior identity's data so it never shows under a new dep
    if (!keep || !same) setData(null);
    run()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          // the rows kept through the run answered the previous request, not this one: a retry
          // shows the loader instead of the previous page, dimmed, under the new range label
          if (keep) setData(null);
          setError(err instanceof Error ? err.message : "Request failed");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [run, nonce, keep, keepKey]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, loading, error, reload, keep };
}
