/**
 * Plain-language case summary for the Overview, built only from numbers the engine already
 * computed — no model, no inference. Every sentence states what was recorded; "nothing
 * recorded" is never phrased as "nothing exists" (absent ≠ checked clean), and the closing
 * line keeps the triage-aid caveat the rest of the dashboard carries.
 */
import type { CaseSummary, Flag } from "./types";

const n = (x: number) => x.toLocaleString("en-IN");
const plural = (x: number, one: string, many = `${one}s`) => `${n(x)} ${x === 1 ? one : many}`;

function list(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** The engine stamps mock acquisitions with a "MOCK DEVICE — synthetic fixtures…" pre-state note. */
export function isDemoCase(s: CaseSummary): boolean {
  const note = s.case.pre_state?.note;
  return typeof note === "string" && /mock|synthetic/i.test(note);
}

export function caseNarrative(s: CaseSummary, flags: Flag[]): string[] {
  const c = s.case;
  const d = c.device;
  const k = s.counts;
  const out: string[] = [];

  const device = [d.manufacturer, d.model].filter(Boolean).join(" ") || "an Android device";
  const authority = c.legal_authority ? `under ${c.legal_authority}` : "";
  if (isDemoCase(s)) {
    out.push("DEMONSTRATION DATA: this case was built from a synthetic test corpus, not a real device. Do not treat it as evidence.");
  }
  out.push(
    `Case ${c.case_id}: ${device}${d.android_version ? ` (Android ${d.android_version})` : ""} ` +
      `was ${isDemoCase(s) ? "loaded" : "acquired"} by ${c.examiner || "an unnamed examiner"}${authority ? ` ${authority}` : ""}.`
  );

  const hv = s.hash_verification;
  const integrity =
    !hv || hv.status === "error" || hv.status === "skipped"
      ? "Hash verification did not run, so file integrity is unconfirmed."
      : hv.failed
        ? `${plural(hv.failed, "file")} FAILED hash verification.`
        : `All ${n(hv.verified ?? 0)} verified files match their hashes recorded at collection.`;
  out.push(`${plural(s.artifact_count, "artifact")} collected. ${integrity}`);

  const found: [number, string, string?][] = [
    [k.messages, "message"],
    [k.calls, "call record"],
    [k.contacts, "contact"],
    [k.media, "media file"],
    [k.locations, "location point"],
    [k.browser ?? 0, "browser entry", "browser entries"],
  ];
  const present = found.filter(([x]) => x > 0).map(([x, one, many]) => plural(x, one, many));
  const absent = found.filter(([x]) => x === 0).map(([, one, many]) => many ?? `${one}s`);
  out.push(
    present.length
      ? `Recovered ${list(present)}.` + (absent.length ? ` No ${list(absent)} were recorded.` : "")
      : "No messages, calls, contacts, media, locations or browser history were recorded."
  );

  if (k.recovered > 0) {
    out.push(`${plural(k.recovered, "deleted or carved item")} recovered.`);
  }

  const g = s.graph_stats;
  if (g && g.participants > 0) {
    const top = g.top_contacts[0];
    out.push(
      `${plural(g.participants, "person", "people")} appear in ${plural(g.interactions, "recorded interaction")}` +
        (top ? `; the most frequent contact is ${top.label}.` : ".")
    );
  }

  const critical = flags.filter((f) => f.severity === "critical");
  if (critical.length) {
    const terms = [...new Set(critical.map((f) => f.term))].slice(0, 5);
    out.push(
      `${plural(critical.length, "critical keyword/hash hit")} flagged, including ${list(terms.map((t) => `“${t}”`))}.`
    );
  }
  if (s.device_altering_actions > 0) {
    out.push(`${plural(s.device_altering_actions, "action")} altered the device during acquisition.`);
  }

  if (s.risk) out.push(`Triage priority: ${s.risk.level.toUpperCase()} — ${s.risk.headline}`);
  out.push("Automated triage summary; verify against the underlying evidence.");
  return out;
}
