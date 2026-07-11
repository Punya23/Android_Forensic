import type { Confidence, Severity } from "../lib/types";

const CONF_META: Record<Confidence, { label: string; cls: string }> = {
  live: { label: "LIVE", cls: "text-live border-live/40 bg-live/10" },
  recovered: { label: "RECOVERED", cls: "text-recovered border-recovered/40 bg-recovered/10" },
  carved: { label: "CARVED", cls: "text-carved border-carved/40 bg-carved/10" },
  deletion: { label: "DELETION", cls: "text-deletion border-deletion/40 bg-deletion/10" },
};

export function ConfidenceBadge({ c, title }: { c: Confidence; title?: string }) {
  const m = CONF_META[c] ?? CONF_META.carved;
  return (
    <span
      title={title ?? m.label}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold font-mono ${m.cls}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {m.label}
    </span>
  );
}

const SEV_META: Record<Severity, string> = {
  critical: "text-critical border-critical/40 bg-critical/10",
  warn: "text-warn border-warn/40 bg-warn/10",
  info: "text-info border-info/40 bg-info/10",
};

export function SeverityBadge({ s }: { s: Severity }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${SEV_META[s] ?? SEV_META.info}`}
    >
      {s}
    </span>
  );
}

export function TierBadge({ tier }: { tier: string }) {
  const map: Record<string, string> = {
    tier0: "text-live border-live/40 bg-live/10",
    tier1: "text-warn border-warn/40 bg-warn/10",
    tier2: "text-deletion border-deletion/40 bg-deletion/10",
  };
  const label = { tier0: "TIER 0", tier1: "TIER 1", tier2: "TIER 2" }[tier] ?? tier;
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] font-mono font-semibold ${map[tier] ?? ""}`}>
      {label}
    </span>
  );
}
