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

/**
 * Palette + wording for each state. Kept in one place so badges never drift apart.
 *
 * The labels name what the examiner can *do*, not just what happened. `not_collected`
 * is the state the engine guarantees is still closable, so it reads as an opt-in rather
 * than as a flat "not collected", which told the examiner nothing about whether the gap
 * could be closed. Anything the handset could never have produced (Tier 2 with no root,
 * an app that is not installed, BFU encryption) resolves to `inaccessible` in the engine
 * and keeps the "could not check" wording; the two must never trade places, because a
 * badge offering a toggle that cannot change the outcome buys a second acquisition — a
 * second set of device-state changes on evidence — for nothing.
 *
 * The "re-run to collect" half of that promise is not the engine's for every
 * `not_collected` dataset, which is why `stateStyle()` below exists: a missing case
 * brief and an Instagram export nobody has imported yet are both closable gaps, and
 * neither is closed by re-acquiring the handset. The engine settles that per dataset in
 * `flag_actionable`; nothing here re-derives it from the state string.
 */
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
    label: "Not collected — see reason",
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

/**
 * Palette and wording for one resolved dataset.
 *
 * Two lookups used to be one: `STATE_STYLE[state]`, which reads "Opt-in — re-run to
 * collect" for every `not_collected` dataset. That is a promise about the next
 * acquisition, and the engine only makes it where `flag_actionable` is true. Where it is
 * false — no case brief was written, or the on-device pull needs root the handset never
 * gave up and an export import is the way in — the badge drops to neutral wording and
 * leaves the instruction to the reason text, which names the actual fix.
 *
 * An unrecognised state falls back to the `empty` palette, so anything that renders it
 * alongside must fall back to wording that does not contradict "Checked — nothing found".
 */
export function stateStyle(
  cap: Pick<CapabilityState, "state" | "flag_actionable">,
): { label: string; chip: string; tone: string } {
  const base = STATE_STYLE[cap.state] ?? STATE_STYLE.empty;
  if (cap.state === "not_collected" && cap.flag_actionable) {
    return { ...base, label: "Opt-in — re-run to collect" };
  }
  return base;
}

export function StateBadge({ cap }: { cap: CapabilityState }) {
  const style = stateStyle(cap);
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

  const style = stateStyle(cap);

  return (
    <div className="flex flex-col items-center justify-center h-full text-center py-16 px-6">
      <div className="max-w-lg w-full card p-6 text-left">
        <div className="flex items-center gap-2 mb-3">
          <StateBadge cap={cap} />
          <TierBadge tier={cap.tier} />
          <span className="ml-auto text-xs text-muted font-mono">{cap.label}</span>
        </div>
        {/*
          One headline per state, each tested by name, and `inaccessible` gets its own.
          It used to fall into the `not_collected` branch and read "was not collected in
          this run", which both contradicted the "Could not check" badge two lines above
          it and implied a re-run would fix something this handset cannot produce at all.

          The final branch is a genuine fallback — a state string this build does not
          know — so it asserts no mechanism at all. It cannot: the badge beside it
          renders such a state with the `empty` palette ("Checked — nothing found"), and
          a headline claiming a switched-off opt-in next to that badge would have the
          screen contradicting itself about the one distinction this layer exists to draw.
          The `not_collected` wording is likewise conditional: only the engine knows
          whether the flag is the fix, and it says so in `flag_actionable`.
        */}
        <div className={`text-sm font-medium mb-2 ${style.tone}`}>
          {cap.state === "empty"
            ? `${cap.label}: the source was read and held nothing`
            : cap.state === "planned"
              ? `${cap.label} is not built yet`
              : cap.state === "inaccessible"
                ? `${cap.label} could not be collected on this handset`
                : cap.state === "not_collected"
                  ? cap.flag_actionable
                    ? `${cap.label} is opt-in and was left off for this acquisition`
                    : `${cap.label} was not collected — the reason below says what closes the gap`
                  : `${cap.label}: nothing to show for this acquisition`}
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

  const style = stateStyle(cap);
  return (
    <div
      className={`shrink-0 flex items-start gap-3 px-5 py-2.5 border-b border-line bg-panel-2 text-xs leading-relaxed`}
    >
      <div className="flex items-center gap-1.5 shrink-0 pt-px">
        <StateBadge cap={cap} />
        <TierBadge tier={cap.tier} />
      </div>
      <p className={`${style.tone} min-w-0`}>{cap.reason}</p>
    </div>
  );
}
