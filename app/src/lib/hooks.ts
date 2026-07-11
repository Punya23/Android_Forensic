import { useEffect, useState } from "react";
import { api } from "./api";

export function useDataset<T>(caseId: string, name: string): { data: T[]; loading: boolean } {
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
  }, [caseId, name]);
  return { data, loading };
}

export function fmtTs(ts: string | null | undefined): string {
  if (!ts) return "—";
  return ts.replace("T", " ").replace("Z", "");
}
