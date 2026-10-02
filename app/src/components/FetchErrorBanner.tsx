import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { dismissFetchFailures, useFetchFailures } from "../lib/fetchErrors";

/** Shown whenever an engine request failed, so an empty panel is never mistaken for
 * "checked and found nothing". Clears itself as the failed paths later succeed. */
export function FetchErrorBanner() {
  const failures = useFetchFailures();
  const [open, setOpen] = useState(false);
  if (failures.length === 0) return null;

  return (
    <div className="shrink-0 border-b border-critical/40 bg-critical/10 px-5 py-2 text-xs">
      <div className="flex items-center gap-2 text-critical">
        <AlertTriangle size={14} className="shrink-0" />
        <span className="font-medium">
          {failures.length} engine request{failures.length > 1 ? "s" : ""} failed — empty panels may be an
          outage, not "no data".
        </span>
        <button className="underline" onClick={() => setOpen((o) => !o)}>
          {open ? "hide" : "details"}
        </button>
        <button className="ml-auto" aria-label="Dismiss" onClick={dismissFetchFailures}>
          <X size={14} />
        </button>
      </div>
      {open && (
        <ul className="mt-1.5 space-y-0.5 font-mono text-[11px] text-muted">
          {failures.map((f) => (
            <li key={f.path}>
              {f.path} — {f.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
