import type { Risk } from "../lib/types";

const META = {
  red: { ring: "border-deletion", bg: "bg-deletion/10", dot: "bg-deletion", text: "text-deletion", label: "HIGH PRIORITY" },
  amber: { ring: "border-warn", bg: "bg-warn/10", dot: "bg-warn", text: "text-warn", label: "REVIEW" },
  green: { ring: "border-live", bg: "bg-live/10", dot: "bg-live", text: "text-live", label: "LOW" },
};

// The traffic-light triage verdict — the seconds-to-results signal a field officer reads
// first. Transparent: every point in the score is itemised with its evidence.
export function RiskCard({ risk }: { risk: Risk }) {
  const m = META[risk.level] ?? META.amber;
  return (
    <div className={`card ${m.ring} ${m.bg} border p-4 mb-5`}>
      <div className="flex items-start gap-4">
        {/* Traffic light */}
        <div className="flex flex-col gap-1.5 items-center pt-1 shrink-0">
          <Light on={risk.level === "red"} color="bg-deletion" />
          <Light on={risk.level === "amber"} color="bg-warn" />
          <Light on={risk.level === "green"} color="bg-live" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <span className={`text-xl font-bold ${m.text}`}>TRIAGE VERDICT: {m.label}</span>
            <span className="font-mono text-sm text-muted">score {risk.score}/100</span>
          </div>
          <p className="text-sm mt-1">{risk.headline}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 mt-3">
            {risk.reasons.map((r, i) => (
              <div key={i} className="flex items-baseline gap-2 text-sm">
                <span className={`font-mono text-xs shrink-0 ${r.severity === "critical" ? "text-deletion" : "text-warn"}`}>
                  +{r.points}
                </span>
                <span className="text-ink/90">{r.label}</span>
                <span className="text-muted text-xs truncate">— {r.detail}</span>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-muted/80 mt-3">{risk.disclaimer}</p>
        </div>
      </div>
    </div>
  );
}

function Light({ on, color }: { on: boolean; color: string }) {
  return <span className={`h-4 w-4 rounded-full ${on ? color : "bg-line"} ${on ? "" : "opacity-40"}`} />;
}
