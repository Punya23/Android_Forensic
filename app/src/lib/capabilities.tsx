/**
 * Per-case capability state, shared by every dataset view.
 *
 * Before this, an empty view rendered a blank panel no matter *why* it was empty —
 * whether the engine looked and found nothing, was told not to look, could not look
 * without root, or has no such feature yet. The engine draws those distinctions
 * (`triage/capabilities.py`); this carries them to the screen so a view never implies
 * "checked and clean" when the truthful answer is "not checked".
 *
 * Loaded once per case and read by `DatasetEmpty`. A failed load is not an error state:
 * views fall back to their own generic empty text, which is what they did before.
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { CapabilityState, CaseCapabilities } from "./types";

const CapabilityContext = createContext<CaseCapabilities | null>(null);

export function CapabilityProvider({ caseId, children }: { caseId: string; children: ReactNode }) {
  const [caps, setCaps] = useState<CaseCapabilities | null>(null);

  useEffect(() => {
    let live = true;
    setCaps(null);
    api
      .capabilities(caseId)
      .then((data) => {
        if (live) setCaps(data);
      })
      .catch(() => {
        if (live) setCaps(null);
      });
    return () => {
      live = false;
    };
  }, [caseId]);

  return <CapabilityContext.Provider value={caps}>{children}</CapabilityContext.Provider>;
}

/** The resolved state for one dataset, or null while loading / if unknown. */
export function useCapability(dataset?: string): CapabilityState | null {
  const caps = useContext(CapabilityContext);
  return useMemo(() => {
    if (!caps || !dataset) return null;
    return caps.by_dataset[dataset] ?? null;
  }, [caps, dataset]);
}

export function useCapabilities(): CaseCapabilities | null {
  return useContext(CapabilityContext);
}

/** Palette + wording for each state. Kept in one place so badges never drift apart. */
export const STATE_STYLE: Record<
  string,
  { label: string; chip: string; tone: string }
> = {
  populated: {
    label: "Collected",
    chip: "bg-live/15 text-live border-live/30",
    tone: "text-live",
  },
  empty: {
    label: "Checked — nothing found",
    chip: "bg-panel-2 text-muted border-line",
    tone: "text-muted",
  },
  not_collected: {
    label: "Not collected",
    chip: "bg-warn/15 text-warn border-warn/30",
    tone: "text-warn",
  },
  inaccessible: {
    label: "Could not check",
    chip: "bg-deletion/15 text-deletion border-deletion/30",
    tone: "text-deletion",
  },
  planned: {
    label: "Coming soon",
    chip: "bg-accent/15 text-accent border-accent/30",
    tone: "text-accent",
  },
};

export function TierBadge({ tier }: { tier: number }) {
  if (tier < 0) return <span className="chip">Derived</span>;
  const style =
    tier === 0
      ? "bg-live/15 text-live border-live/30"
      : tier === 1
        ? "bg-warn/15 text-warn border-warn/30"
        : "bg-deletion/15 text-deletion border-deletion/30";
  return (
    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${style}`}>
      Tier {tier}
    </span>
  );
}

export function StateBadge({ state }: { state: string }) {
  const style = STATE_STYLE[state] ?? STATE_STYLE.empty;
  return (
    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${style.chip}`}>
      {style.label}
    </span>
  );
}

/**
 * The empty state for a dataset view. Pass the dataset name the view fetches and it
 * explains, in the engine's own words, why there is nothing to show — including which
 * acquisition flag to turn on if the answer is "you didn't ask for it".
 *
 * `title` / `detail` are the view's own fallback wording, used when the engine has no
 * opinion about this dataset.
 */
export function DatasetEmpty({
  dataset,
  title,
  detail,
}: {
  dataset?: string;
  title: string;
  detail?: string;
}) {
  const cap = useCapability(dataset);

  if (!cap || cap.state === "populated") {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center py-16 px-6">
        <div className="text-muted text-sm max-w-md">
          <div className="text-ink font-medium mb-1">{title}</div>
          {detail && <p className="leading-relaxed">{detail}</p>}
        </div>
      </div>
    );
  }

  const style = STATE_STYLE[cap.state] ?? STATE_STYLE.empty;

  return (
    <div className="flex flex-col items-center justify-center h-full text-center py-16 px-6">
      <div className="max-w-lg w-full card p-6 text-left">
        <div className="flex items-center gap-2 mb-3">
          <StateBadge state={cap.state} />
          <TierBadge tier={cap.tier} />
          <span className="ml-auto text-xs text-muted font-mono">{cap.label}</span>
        </div>
        <div className={`text-sm font-medium mb-2 ${style.tone}`}>
          {cap.state === "empty"
            ? `${cap.label}: the source was read and held nothing`
            : cap.state === "planned"
              ? `${cap.label} is not built yet`
              : `${cap.label} was not collected in this run`}
        </div>
        <p className="text-sm text-muted leading-relaxed">{cap.reason || detail}</p>
        {cap.requires && cap.state !== "empty" && (
          <p className="text-xs text-muted/80 leading-relaxed mt-3 pt-3 border-t border-line">
            <span className="text-ink/70 font-medium">Requires: </span>
            {cap.requires}
          </p>
        )}
        {cap.state !== "empty" && cap.state !== "planned" && (
          <p className="text-[11px] text-warn/90 leading-relaxed mt-3">
            This is a fact about the acquisition, not about the device. It is not evidence
            that nothing was there.
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * A one-line strip above a view when the data behind it is absent for a reason worth
 * naming. Views keep their own bodies; this only adds the sentence the body cannot
 * know — which acquisition flag was off, why root was needed, or that the feature is
 * not built. Nothing renders for a populated dataset.
 */
export function CapabilityBanner({ dataset }: { dataset?: string }) {
  const cap = useCapability(dataset);
  if (!cap || cap.state === "populated") return null;

  const style = STATE_STYLE[cap.state] ?? STATE_STYLE.empty;
  return (
    <div
      className={`shrink-0 flex items-start gap-3 px-5 py-2.5 border-b border-line bg-panel-2 text-xs leading-relaxed`}
    >
      <div className="flex items-center gap-1.5 shrink-0 pt-px">
        <StateBadge state={cap.state} />
        <TierBadge tier={cap.tier} />
      </div>
      <p className={`${style.tone} min-w-0`}>{cap.reason}</p>
    </div>
  );
}
