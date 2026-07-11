import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { ViewKey } from "./Sidebar";
import type { CallRecord, Contact, Message, RecoveredRow } from "../lib/types";

interface Hit {
  view: ViewKey;
  category: string;
  text: string;
  sub: string;
}

// Cross-artifact search across messages, contacts, calls, and recovered data — the
// "search everything" bar every commercial suite has. Loads the datasets once and filters
// client-side (fast, offline).
export function GlobalSearch({ caseId, setView }: { caseId: string; setView: (v: ViewKey) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{
    messages: Message[];
    contacts: Contact[];
    calls: CallRecord[];
    recovered: RecoveredRow[];
  } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    Promise.all([
      api.dataset<Message[]>(caseId, "messages"),
      api.dataset<Contact[]>(caseId, "contacts"),
      api.dataset<CallRecord[]>(caseId, "calls"),
      api.dataset<RecoveredRow[]>(caseId, "recovered"),
    ])
      .then(([messages, contacts, calls, recovered]) => setData({ messages, contacts, calls, recovered }))
      .catch(() => setData(null));
  }, [caseId]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const hits: Hit[] = [];
  if (q.length >= 2 && data) {
    const needle = q.toLowerCase();
    for (const m of data.messages) {
      if (m.body?.toLowerCase().includes(needle) || m.sender?.toLowerCase().includes(needle)) {
        hits.push({ view: "messages", category: m.app, text: m.body, sub: `${m.sender} · ${m.confidence}` });
      }
      if (hits.length > 40) break;
    }
    for (const c of data.contacts) {
      if (c.name?.toLowerCase().includes(needle) || c.number?.includes(needle))
        hits.push({ view: "contacts", category: "contact", text: c.name, sub: c.number });
    }
    for (const c of data.calls) {
      if (c.number?.includes(needle) || c.name?.toLowerCase().includes(needle))
        hits.push({ view: "calls", category: "call", text: `${c.name || c.number}`, sub: c.call_type });
    }
    for (const r of data.recovered) {
      const t = r.values.filter((v) => typeof v === "string").join(" ");
      if (t.toLowerCase().includes(needle))
        hits.push({ view: "recovered", category: "recovered", text: t, sub: r.provenance });
      if (hits.length > 60) break;
    }
  }

  return (
    <div ref={ref} className="relative flex-1 max-w-lg">
      <input
        className="input py-1.5"
        placeholder="Search all artifacts…  (messages, contacts, calls, recovered)"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open && q.length >= 2 && (
        <div className="absolute top-full mt-1 w-full max-h-96 overflow-auto card z-50 shadow-2xl">
          {hits.length === 0 ? (
            <div className="p-3 text-sm text-muted">No matches for “{q}”.</div>
          ) : (
            <>
              <div className="px-3 py-1.5 text-[11px] uppercase tracking-wider text-muted border-b border-line">
                {hits.length} match{hits.length === 1 ? "" : "es"}
              </div>
              {hits.slice(0, 50).map((h, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setView(h.view);
                    setOpen(false);
                  }}
                  className="w-full text-left px-3 py-2 hover:bg-panel border-b border-line/50 last:border-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase text-accent font-mono shrink-0">{h.category}</span>
                    <span className="text-sm truncate">{highlight(h.text, q)}</span>
                  </div>
                  <div className="text-xs text-muted truncate">{h.sub}</div>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function highlight(text: string, q: string) {
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-accent/40 text-ink rounded px-0.5">{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  );
}
