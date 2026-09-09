import { useEffect, useState } from "react";
import { api } from "./api";

export function useDataset<T>(
  caseId: string,
  name: string,
  // Bump this (e.g. a local useState counter) to force a refetch after an action that
  // changes the dataset server-side without changing caseId/name — an import upload,
  // for instance. Optional and defaults to a stable value, so existing callers refetch
  // exactly as before.
  reloadKey: number | string = 0
): { data: T[]; loading: boolean } {
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<T[]>(caseId, name)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData([]))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId, name, reloadKey]);
  return { data, loading };
}

export function fmtTs(ts: string | null | undefined): string {
  if (!ts) return "—";
  return ts.replace("T", " ").replace("Z", "");
}
