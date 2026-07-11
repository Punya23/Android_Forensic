import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AuditEvent, ManifestRecord } from "../lib/types";
import { SectionHeader } from "../components/common";
import { TierBadge } from "../components/Badges";
import { bytes } from "../components/common";

export function CustodyView({ caseId }: { caseId: string }) {
  const [tab, setTab] = useState<"audit" | "manifest">("audit");
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [manifest, setManifest] = useState<ManifestRecord[]>([]);

  useEffect(() => {
    api.audit(caseId).then(setAudit).catch(() => setAudit([]));
    api.manifest(caseId).then(setManifest).catch(() => setManifest([]));
  }, [caseId]);

  return (
    <div className="p-6 h-full flex flex-col">
      <SectionHeader
        title="Chain of Custody"
        sub="Append-only audit trail + per-artifact SHA-256 manifest (NIST SP 800-101r1 / SWGDE-aligned)"
        right={
          <div className="flex gap-1 bg-panel rounded-md p-1">
            <TabBtn active={tab === "audit"} onClick={() => setTab("audit")}>Audit trail ({audit.length})</TabBtn>
            <TabBtn active={tab === "manifest"} onClick={() => setTab("manifest")}>Manifest ({manifest.length})</TabBtn>
          </div>
        }
      />

      {tab === "audit" ? (
        <div className="card overflow-auto flex-1">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th w-44">Timestamp</th>
                <th className="th w-36">Action</th>
                <th className="th">Detail</th>
                <th className="th w-24">Alters device</th>
                <th className="th w-20">Result</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((e, i) => (
                <tr key={i}>
                  <td className="td font-mono text-xs text-muted">{e.timestamp}</td>
                  <td className="td font-mono text-xs">{e.action}</td>
                  <td className="td">
                    {e.detail}
                    {e.command && <div className="text-[10px] text-muted/70 font-mono mt-0.5">$ {e.command}</div>}
                  </td>
                  <td className="td">
                    {e.alters_device ? <span className="text-deletion font-semibold">YES</span> : <span className="text-muted">no</span>}
                  </td>
                  <td className="td">
                    <span className={e.result === "ok" ? "text-live" : e.result === "error" ? "text-deletion" : "text-warn"}>{e.result}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card overflow-auto flex-1">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th w-20">ID</th>
                <th className="th">Source path</th>
                <th className="th w-20">Tier</th>
                <th className="th w-24">Size</th>
                <th className="th">SHA-256</th>
              </tr>
            </thead>
            <tbody>
              {manifest.map((a) => (
                <tr key={a.artifact_id}>
                  <td className="td font-mono text-xs">{a.artifact_id}</td>
                  <td className="td font-mono text-xs">{a.source_path}</td>
                  <td className="td"><TierBadge tier={a.tier} /></td>
                  <td className="td text-xs">{bytes(a.size_bytes)}</td>
                  <td className="td font-mono text-[10px] text-muted break-all">{a.sha256}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`px-3 py-1 rounded text-xs ${active ? "bg-panel-2 text-ink" : "text-muted"}`}>
      {children}
    </button>
  );
}
