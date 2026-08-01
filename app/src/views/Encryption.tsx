import { useEffect, useState } from "react";
import { api } from "../lib/api";

// ---------------------------------------------------------------------------
// Types — declared locally so this view owns its own contract with the engine
// (`case.write_derived("encryption_state", …)`, pipeline.py). Every field is
// optional: a partially-captured determination must still render, and a missing
// field must read as "not captured", never as a confident negative.
// ---------------------------------------------------------------------------
export type UnlockState = "afu" | "bfu" | "not_encrypted" | "unknown";

export interface EncryptionState {
  crypto_type?: string;
  crypto_state?: string;
  sdk?: number | string;
  android_release?: string;
  metadata_encryption?: string | boolean;
  unlock_state?: UnlockState;
  unlock_evidence?: string[];
  screen_locked?: boolean | null;
  ce_accessible?: boolean | null;
  de_accessible?: boolean | null;
  fbe_mandatory?: boolean | null;
  caveats?: string[];
  probes?: Record<string, string>;
}

// Palette lifted verbatim from the confidence colours used across the app.
const TONE = {
  live: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved: { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion: { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
} as const;

type ToneName = keyof typeof TONE;

interface VerdictCopy {
  tone: ToneName;
  label: string;
  headline: string;
  body: string;
}

const VERDICTS: Record<UnlockState, VerdictCopy> = {
  afu: {
    tone: "live",
    label: "AFU",
    headline: "After First Unlock — credential-encrypted app data was reachable",
    body:
      "The device had been unlocked at least once since boot, so Credential-Encrypted (CE) " +
      "storage was mounted in the clear while this acquisition ran. Absence of an artifact " +
      "in this case is therefore evidence about the device, not about the encryption — " +
      "subject to the tier, permissions and root state actually used.",
  },
  bfu: {
    tone: "deletion",
    label: "BFU",
    headline: "Before First Unlock — credential-encrypted app data is cryptographically INACCESSIBLE",
    body:
      "Credential-encrypted app data is present on the device but was NOT decryptable during " +
      "this acquisition. Absence of app data in this case does NOT mean the data was absent " +
      "from the device. Any finding of the form \"no messages were found\" is unsupportable " +
      "on a BFU acquisition and must not be reported as such.",
  },
  not_encrypted: {
    tone: "carved",
    label: "NOT ENCRYPTED",
    headline: "The device reports /data as not encrypted",
    body:
      "Storage is reported unencrypted, so the unlock state does not gate access to app data. " +
      "This is unusual on modern Android and should be corroborated against crypto_type, the " +
      "SDK level and the raw probes below before it is relied on in a report.",
  },
  unknown: {
    tone: "carved",
    label: "UNKNOWN",
    headline: "Encryption state could not be determined — do not assume AFU",
    body:
      "The probes needed to decide between AFU and BFU did not return a usable answer. Treat " +
      "the reachability of credential-encrypted data as unknown. Do not infer that missing " +
      "artifacts were absent from the device, and do not describe this acquisition as AFU.",
  },
};

function Banner({ state }: { state: UnlockState }) {
  const v = VERDICTS[state];
  const c = TONE[v.tone];
  return (
    <div
      className="rounded-lg p-5 mb-5"
      style={{ background: c.bg, border: `2px solid ${c.border}`, color: c.text }}
    >
      <div className="flex items-center gap-3 mb-2">
        <span
          className="text-[11px] font-bold uppercase tracking-wider rounded px-2 py-0.5"
          style={{ background: c.border, color: c.bg }}
        >
          {v.label}
        </span>
        <span className="text-[11px] uppercase tracking-wider opacity-70">
          unlock_state = {state}
        </span>
      </div>
      <div className="text-lg font-bold leading-snug mb-2">{v.headline}</div>
      <p className="text-sm leading-relaxed">{v.body}</p>
    </div>
  );
}

/** Tri-state pill. `null`/`undefined` renders as UNKNOWN — never as "no". */
function TriState({ value, yes, no }: { value: boolean | null | undefined; yes: string; no: string }) {
  if (value === null || value === undefined) {
    return (
      <span className="text-warn text-sm font-medium">
        Unknown — not captured
      </span>
    );
  }
  return (
    <span className={`text-sm font-medium ${value ? "text-live" : "text-deletion"}`}>
      {value ? yes : no}
    </span>
  );
}

function Prop({
  label,
  raw,
  meaning,
}: {
  label: string;
  raw: string;
  meaning: string;
}) {
  const missing = raw === "";
  return (
    <div className="card p-3">
      <div className="text-[11px] uppercase tracking-wider text-muted mb-1">{label}</div>
      <div className={`font-mono text-sm mb-1.5 break-all ${missing ? "text-warn" : "text-ink"}`}>
        {missing ? "not captured" : raw}
      </div>
      <div className="text-xs text-muted leading-relaxed">{meaning}</div>
    </div>
  );
}

function str(v: string | number | boolean | null | undefined): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function cryptoTypeMeaning(v: string): string {
  const k = v.trim().toLowerCase();
  if (k === "file")
    return "File-Based Encryption (FBE). Every file is encrypted with a key derived from the user's credential and filenames are encrypted too — a root shell enumerating /data before first unlock sees ciphertext and scrambled directory entries.";
  if (k === "block")
    return "Full-Disk Encryption (FDE) — the legacy pre-Android-10 scheme. The whole /data volume is opaque until the boot credential is supplied; once mounted, all of it is readable.";
  if (k === "" || k === "none" || k === "unknown")
    return "The device did not report ro.crypto.type. A missing value is not a report of \"no encryption\" — it means the probe returned nothing.";
  return "Reported verbatim from ro.crypto.type. This value is outside the set this build interprets, so no meaning is asserted for it.";
}

function cryptoStateMeaning(v: string): string {
  const k = v.trim().toLowerCase();
  if (k === "encrypted") return "The platform reports /data as encrypted at rest.";
  if (k === "unencrypted")
    return "The platform reports /data as not encrypted. Corroborate against crypto_type and the SDK level before relying on this — on Android 10+ it is anomalous.";
  if (k === "unsupported")
    return "The platform reports encryption as unsupported for this device. Verify against the raw probes; this is rare on shipping hardware.";
  if (k === "") return "ro.crypto.state was not captured. Reachability of app data cannot be argued from this field.";
  return "Reported verbatim from ro.crypto.state; no interpretation is asserted for this value.";
}

function metadataMeaning(v: string): string {
  const k = v.trim().toLowerCase();
  if (k === "true" || k === "1" || k === "yes" || k === "on")
    return "Metadata encryption is enabled: the directory structure and file metadata on /data are protected even before first unlock, on top of per-file CE/DE encryption.";
  if (k === "false" || k === "0" || k === "no" || k === "off")
    return "Metadata encryption is not enabled: some filesystem metadata may be legible before first unlock. File contents in credential-encrypted storage remain encrypted regardless.";
  if (k === "") return "Not captured. No claim is made either way about metadata-partition protection.";
  return "Reported verbatim; no interpretation is asserted for this value.";
}

function fbeMeaning(v: boolean | null | undefined, sdk: number | null): string {
  if (v === true)
    return "This Android version mandates File-Based Encryption (required from Android 10 / SDK 29). Root does not bypass it: before first unlock the credential-encrypted key is not in the keyring, so there is nothing for a root shell to read.";
  if (v === false)
    return sdk !== null && sdk >= 29
      ? "Recorded as not mandatory, yet the SDK level is 29 or higher, where FBE is required. Treat this as a contradiction to resolve, not as a finding."
      : "This build predates the FBE mandate, so the device may use FDE or no encryption. Determine which from crypto_type rather than assuming.";
  return "Not captured. Do not assume the device is exempt from the FBE mandate.";
}

function sdkMeaning(sdk: number | null): string {
  if (sdk === null) return "Not captured, so the FBE mandate cannot be established from the platform level.";
  if (sdk >= 29)
    return `API level ${sdk} — File-Based Encryption is mandatory from API 29 (Android 10). Credential-encrypted storage is unreadable, including by root, until the user's credential has been entered once since boot.`;
  return `API level ${sdk} — predates the API 29 FBE mandate. The encryption scheme in force must be read from crypto_type, not assumed.`;
}

export function EncryptionView({ caseId }: { caseId: string }) {
  const [state, setState] = useState<EncryptionState | null>(null);
  const [loading, setLoading] = useState(true);
  const [showProbes, setShowProbes] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<EncryptionState>(caseId, "encryption_state")
      .then((d) => alive && setState(d ?? {}))
      .catch(() => alive && setState({}))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (loading) return <div className="p-8 text-muted text-sm">Loading encryption posture…</div>;

  const s: EncryptionState = state ?? {};
  const captured = Object.keys(s).length > 0;

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <span>🔒</span> Encryption &amp; Unlock State
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 0 — Read-only
        </span>
      </h1>
      <p className="text-sm text-muted">
        What this acquisition could possibly have reached. Determined from read-only property
        and dumpsys queries — no credential was supplied, guessed or bypassed.
      </p>
    </div>
  );

  // Honest empty state: an absent object means the determination was never made.
  if (!captured) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        {header}
        <div className="card p-6 max-w-3xl">
          <div className="text-warn font-semibold mb-2">
            Encryption state was not captured for this case
          </div>
          <p className="text-sm text-muted leading-relaxed">
            No <code className="text-ink font-mono">encryption_state</code> record was written
            during acquisition, so the device's encryption scheme and its AFU/BFU posture are{" "}
            <strong className="text-ink">unknown</strong>. This is <strong className="text-ink">not</strong>{" "}
            a finding that the device is unencrypted, and it is not a finding that data was
            reachable.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Consequence for interpretation: because the unlock state is unknown, the absence of
            any artifact in this case cannot be attributed to the device. It may equally be an
            artifact of credential-encrypted storage never having been decryptable during the
            acquisition. Re-run the acquisition on a build that captures the encryption probes
            before drawing negative conclusions.
          </p>
        </div>
      </div>
    );
  }

  const unlock: UnlockState =
    s.unlock_state === "afu" || s.unlock_state === "bfu" || s.unlock_state === "not_encrypted"
      ? s.unlock_state
      : "unknown";

  const sdkRaw = str(s.sdk);
  const sdkNum = Number.parseInt(sdkRaw, 10);
  const sdk = Number.isFinite(sdkNum) ? sdkNum : null;

  const evidence = s.unlock_evidence ?? [];
  const caveats = s.caveats ?? [];
  const probes = s.probes ?? {};
  const probeKeys = Object.keys(probes).sort();

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {header}

      <Banner state={unlock} />

      {/* The single most misunderstood point in mobile forensics. */}
      <div className="card p-4 mb-5 border-warn/40 bg-warn/5">
        <div className="text-warn font-semibold text-sm mb-2">Root is not decryption</div>
        <p className="text-xs text-muted leading-relaxed">
          A root shell grants <em>authorisation</em> to read <code className="text-ink font-mono">/data</code>;
          it does not grant the <em>key</em>. On File-Based Encryption — mandatory from Android 10
          (SDK 29) — each file is encrypted with a key derived from the user's lock credential, and
          filenames are encrypted as well. Until the credential has been entered once since boot,
          a root shell listing <code className="text-ink font-mono">/data/data</code> sees ciphertext
          under scrambled directory entries.
        </p>
        <ul className="text-xs text-muted leading-relaxed mt-2 space-y-1 list-disc list-inside">
          <li>
            <strong className="text-ink">Device-Encrypted (DE)</strong> storage —{" "}
            <code className="font-mono">/data/user_de/0</code> — unlocks at boot and{" "}
            <span className="text-ink">is readable BFU</span>. It holds only what apps chose to put
            there: alarms, some system state, no message bodies.
          </li>
          <li>
            <strong className="text-ink">Credential-Encrypted (CE)</strong> storage —{" "}
            <code className="font-mono">/data/data</code>, <code className="font-mono">/data/user/0</code>{" "}
            — holds essentially all app data and is <span className="text-ink">not readable BFU</span>,
            with or without root.
          </li>
          <li>
            Consequently a BFU acquisition can never support the statement "the app contained no
            messages". It can only support "no messages were reachable".
          </li>
        </ul>
      </div>

      {/* Reachability, stated plainly. */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted mb-1">
            Credential-Encrypted storage
          </div>
          <TriState value={s.ce_accessible} yes="Reachable" no="Not reachable" />
          <div className="text-xs text-muted mt-1.5 leading-relaxed">
            /data/data and /data/user/0 — app databases, message stores, media caches.
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted mb-1">
            Device-Encrypted storage
          </div>
          <TriState value={s.de_accessible} yes="Reachable" no="Not reachable" />
          <div className="text-xs text-muted mt-1.5 leading-relaxed">
            /data/user_de/0 — available from boot; contains little of evidential substance.
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted mb-1">
            Screen at capture time
          </div>
          <TriState value={s.screen_locked} yes="Locked" no="Unlocked" />
          <div className="text-xs text-muted mt-1.5 leading-relaxed">
            A locked screen does <em>not</em> imply BFU — a device unlocked once since boot keeps
            CE storage mounted while locked.
          </div>
        </div>
      </div>

      {/* Property grid with per-value meaning. */}
      <h2 className="text-sm font-semibold text-ink mb-2">Platform crypto properties</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-5">
        <Prop label="crypto_type" raw={str(s.crypto_type)} meaning={cryptoTypeMeaning(str(s.crypto_type))} />
        <Prop label="crypto_state" raw={str(s.crypto_state)} meaning={cryptoStateMeaning(str(s.crypto_state))} />
        <Prop label="sdk" raw={sdkRaw} meaning={sdkMeaning(sdk)} />
        <Prop
          label="android_release"
          raw={str(s.android_release)}
          meaning={
            str(s.android_release) === ""
              ? "Not captured. The marketing version is corroborating detail only; the SDK level is what governs the FBE mandate."
              : "Marketing version string reported by the device. The SDK level above, not this string, determines whether FBE is mandatory."
          }
        />
        <Prop
          label="metadata_encryption"
          raw={str(s.metadata_encryption)}
          meaning={metadataMeaning(str(s.metadata_encryption))}
        />
        <Prop
          label="fbe_mandatory"
          raw={str(s.fbe_mandatory)}
          meaning={fbeMeaning(s.fbe_mandatory, sdk)}
        />
      </div>

      {/* Auditable determination trail. */}
      <h2 className="text-sm font-semibold text-ink mb-2">How this determination was reached</h2>
      <div className="card p-4 mb-5">
        {evidence.length === 0 ? (
          <p className="text-sm text-warn leading-relaxed">
            No determination trail was recorded. The unlock state above is therefore an
            unsupported label — treat it as unverified and rely on the raw probes below rather
            than on the banner.
          </p>
        ) : (
          <>
            <ol className="list-decimal list-inside space-y-2 text-sm text-muted leading-relaxed">
              {evidence.map((e, i) => (
                <li key={i} className="pl-1">
                  <span className="text-ink">{e}</span>
                </li>
              ))}
            </ol>
            <p className="text-xs text-muted mt-3 pt-3 border-t border-line">
              Each step above is an observation, listed in the order it was made, so the verdict
              can be re-derived by a reviewer rather than taken on trust.
            </p>
          </>
        )}
      </div>

      {/* Every caveat, rendered. */}
      <h2 className="text-sm font-semibold text-ink mb-2">
        Caveats <span className="text-muted font-normal">({caveats.length})</span>
      </h2>
      <div className="card p-4 mb-5">
        {caveats.length === 0 ? (
          <p className="text-sm text-muted">
            The engine recorded no caveats against this determination. That is the absence of a
            recorded caveat, not an assurance that none apply.
          </p>
        ) : (
          <ul className="space-y-2">
            {caveats.map((c, i) => (
              <li key={i} className="flex gap-2 text-sm text-warn leading-relaxed">
                <span className="shrink-0">⚠</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Raw probe output, collapsed by default. */}
      <div className="card">
        <button
          className="w-full flex items-center justify-between px-4 py-3 text-left"
          onClick={() => setShowProbes((v) => !v)}
        >
          <span className="text-sm font-semibold text-ink">
            Raw probe output{" "}
            <span className="text-muted font-normal">({probeKeys.length} probes)</span>
          </span>
          <span className="text-xs text-accent">{showProbes ? "hide" : "show"}</span>
        </button>
        {showProbes && (
          <div className="border-t border-line px-4 py-3">
            {probeKeys.length === 0 ? (
              <p className="text-sm text-muted">
                No raw probe output was retained for this determination, so the values above
                cannot be independently checked against the device's own answers.
              </p>
            ) : (
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr>
                      <th className="th w-56">Probe</th>
                      <th className="th">Captured output (verbatim)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {probeKeys.map((k) => (
                      <tr key={k}>
                        <td className="td font-mono text-xs text-muted align-top break-all">{k}</td>
                        <td className="td font-mono text-xs text-ink whitespace-pre-wrap break-all">
                          {probes[k] === "" ? (
                            <span className="text-muted italic">
                              empty — the device answered with no output
                            </span>
                          ) : (
                            probes[k]
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
