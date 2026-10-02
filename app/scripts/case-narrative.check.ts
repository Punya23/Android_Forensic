// Runnable check for the Overview summary: `npm run check:summary`.
// Pins the honesty rules: failed hashes and unrun verification are stated, zero counts read
// "were recorded" never "none exist", and no sentence is produced from missing data.
import assert from "node:assert/strict";
import { caseNarrative } from "../src/lib/caseNarrative.ts";
import type { CaseSummary, Flag } from "../src/lib/types.ts";

function make(over: Partial<CaseSummary> = {}): CaseSummary {
  return {
    case: {
      case_id: "C-1",
      examiner: "Sharma",
      legal_authority: "",
      scope_note: "",
      created_at: "2026-01-01",
      device: { manufacturer: "Samsung", model: "S21", android_version: "14" },
      pre_state: {},
    },
    artifact_count: 22,
    device_altering_actions: 0,
    counts: { messages: 1200, calls: 0, contacts: 5, media: 0, locations: 0, recovered: 0, browser: 0 },
    graph_stats: { participants: 0, interactions: 0, channels: [], top_contacts: [] },
    hash_verification: { status: "completed", verified: 22, failed: 0 },
    ...over,
  } as unknown as CaseSummary;
}

const text = (s: CaseSummary, f: Flag[] = []) => caseNarrative(s, f).join("\n");

let t = text(make());
assert.match(t, /Recovered 1,200 messages and 5 contacts\./);
assert.match(t, /No call records, media files, location points and browser entries were recorded\./);
assert.match(t, /All 22 verified files match/);
assert.doesNotMatch(t, /under /); // no authority recorded → no dangling clause

t = text(make({ hash_verification: { status: "completed", verified: 20, failed: 2 } }));
assert.match(t, /2 files FAILED hash verification/);

t = text(make({ hash_verification: undefined }));
assert.match(t, /Hash verification did not run/);

t = text(make(), [{ kind: "k", term: "passport", context: "", location: "", severity: "critical" } as Flag]);
assert.match(t, /1 critical keyword\/hash hit flagged, including “passport”/);

t = text(make({ case: { ...make().case, pre_state: { note: "MOCK DEVICE — synthetic fixtures" } } } as Partial<CaseSummary>));
assert.match(t, /DEMONSTRATION DATA/);
assert.match(t, /was loaded by Sharma/);
assert.doesNotMatch(text(make()), /DEMONSTRATION DATA/);

console.log("case-narrative check ok");
