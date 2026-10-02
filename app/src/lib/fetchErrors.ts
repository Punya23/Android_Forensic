/**
 * Failed-GET registry. Dozens of views do `.catch(() => setX(empty))`, which made a
 * dead engine / 500 / dropped request render identically to "no data" — the examiner
 * read an outage as a clean result. `api.ts` records every failed GET here and the
 * shell shows them in one banner, so no call site has to remember to surface errors.
 */
import { useSyncExternalStore } from "react";

export interface FetchFailure {
  path: string;
  message: string;
  at: number;
}

let failures: FetchFailure[] = [];
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function recordFetchFailure(path: string, err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  // One row per path: a retry loop must not grow the list without bound.
  failures = [...failures.filter((f) => f.path !== path), { path, message, at: Date.now() }];
  emit();
}

/** A later success on the same path means the earlier failure no longer applies. */
export function clearFetchFailure(path: string): void {
  if (!failures.some((f) => f.path === path)) return;
  failures = failures.filter((f) => f.path !== path);
  emit();
}

export function dismissFetchFailures(): void {
  failures = [];
  emit();
}

export function useFetchFailures(): FetchFailure[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => failures,
    () => failures
  );
}
