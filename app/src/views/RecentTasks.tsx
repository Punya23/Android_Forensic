/**
 * RecentTasks — the activity manager's recent-task list and its snapshot catalogue
 * (Tier 2, root; /data/system_ce/<user>/recent_tasks and /recent_images).
 *
 * Two honesty problems dominate this artifact and the view is built around them:
 *   1. system_ce is credential-encrypted. Before the first unlock it cannot be read at all, so a
 *      skipped collection must never be rendered as "no recent tasks".
 *   2. Recents are volatile by design. Absence proves nothing, and a snapshot's timestamp is a
 *      file mtime — not the moment a person looked at the screen.
 */
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, FolderOpen } from "lucide-react";
import { api } from "../lib/api";
import { useDataset, fmtTs } from "../lib/hooks";
import { StatCard, SortTh, useSort, bytes } from "../components/common";

/** One entry from the recent-task list. */
export interface RecentTaskRecord {
  task_id: number | string;
  real_activity: string;
  affinity: string;
  calling_package: string;
  task_label: string;
  last_time_moved: string | null;
  intent_action: string;
  intent_component: string;
  snapshot_file: string;
  snapshot_size: number | null;
  snapshot_modified: string | null;
  encoding: "xml" | "abx";
  volatile: boolean;
  confidence: string;
  caveats: string[];
}

/** A file in the recent-images snapshot directory. Field names vary by collector version. */
export interface TaskSnapshotRecord {
  file?: string;
  name?: string;
  path?: string;
  size?: number | null;
  modified?: string | null;
  task_id?: number | string | null;
  kind?: string | null;
}

/** Optional roll-up. `skipped` is the field the whole view branches on. */
export interface RecentTasksSummary {
  skipped?: boolean;
  reason?: string;
  note?: string;
  caveats?: string[];
  total_tasks?: number;
  total_snapshots?: number;
}

const CONF_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  live: { bg: "#e4f4ea", text: "#1c7d3f", border: "#1c7d3f" },
  recovered: { bg: "#e2ecfa", text: "#2258a8", border: "#2258a8" },
  carved: { bg: "#f6ecd4", text: "#a6741a", border: "#a6741a" },
  deletion: { bg: "#f6dedd", text: "#a5322f", border: "#a5322f" },
};

function ConfidenceBadge({ value }: { value: string }) {
  const c = CONF_COLORS[value] ?? CONF_COLORS.carved;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: c.text,
        background: c.bg,
        border: `1px solid ${c.border}`,
        whiteSpace: "nowrap",
      }}
    >
      {(value || "unknown").toUpperCase()}
    </span>
  );
}

/** Caveats render inline, always. */
function Caveats({ items }: { items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {items.map((c, i) => (
        <li key={i} className="text-[11px] text-warn leading-relaxed">
          <span className="inline-flex items-center gap-1">
            <AlertTriangle className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden /> {c}
          </span>
        </li>
      ))}
    </ul>
  );
}

function snapshotName(s: TaskSnapshotRecord): string {
  return s.file || s.name || s.path || "";
}

function Header() {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <FolderOpen className="h-4 w-4" strokeWidth={1.75} aria-hidden /> Recent Tasks
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Tier 2 — Root
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed">
        The activity manager's recent-task list and the thumbnail files it kept alongside it, read
        from <code className="font-mono">/data/system_ce/</code>.
      </p>
    </div>
  );
}

export function RecentTasksView({ caseId }: { caseId: string }) {
  const { data: tasks, loading: tasksLoading } = useDataset<RecentTaskRecord>(caseId, "recent_tasks");
  const { data: snapshots, loading: snapsLoading } = useDataset<TaskSnapshotRecord>(
    caseId,
    "task_snapshots"
  );
  const [summary, setSummary] = useState<RecentTasksSummary | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    setSummary(null);
    api
      .dataset<RecentTasksSummary>(caseId, "recent_tasks_summary")
      .then((s) => alive && setSummary(s || {}))
      .catch(() => alive && setSummary({}));
    return () => {
      alive = false;
    };
  }, [caseId]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks
      .filter(
        (t) =>
          !q ||
          (t.real_activity || "").toLowerCase().includes(q) ||
          (t.affinity || "").toLowerCase().includes(q) ||
          (t.calling_package || "").toLowerCase().includes(q) ||
          (t.task_label || "").toLowerCase().includes(q) ||
          (t.intent_component || "").toLowerCase().includes(q)
      )
      .sort((a, b) => (b.last_time_moved || "").localeCompare(a.last_time_moved || ""));
  }, [tasks, query]);
  const namedSnapshots = useMemo(
    () => snapshots.filter((s) => snapshotName(s)),
    [snapshots]
  );
  // Hooks must run unconditionally on every render — computed here, before either early
  // return below, rather than after the loading/BFU checks. Each table gets its own
  // useSort instance; with no column clicked yet, `sorted` is just the array as given, so
  // the existing default orders (last_time_moved descending / dataset order) are unchanged.
  const tasksSort = useSort<RecentTaskRecord>(filtered);
  const snapshotsSort = useSort<TaskSnapshotRecord>(namedSnapshots);

  // Wait for the summary before rendering anything — a skipped collection must never flash an
  // empty table, because that reads as "there were no recent tasks".
  if (tasksLoading || snapsLoading || summary === null) {
    return <div className="p-8 text-muted text-sm animate-pulse">Loading recent tasks…</div>;
  }

  /* ------------------------------------------------------------------ */
  /* BFU / skipped: this is the entire view.                             */
  /* ------------------------------------------------------------------ */
  if (summary.skipped === true) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        <Header />
        <div className="card p-6 border-warn/40 bg-warn/5">
          <div className="text-warn font-semibold mb-2">
            Recent tasks could not be read — this is not "no recent tasks"
          </div>
          <p className="text-sm text-muted leading-relaxed">
            Collection was skipped. The recent-task list and its thumbnails live under{" "}
            <code className="text-ink">/data/system_ce/</code> — the{" "}
            <strong>credential-encrypted</strong> half of the data partition. Its keys are derived
            from the user's PIN, pattern or password and do not exist in memory until the device is
            unlocked once after boot (Before First Unlock, "BFU"). Root does not help: the bytes on
            disk are ciphertext, and the directory is not merely permission-protected.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Report this as <strong>not acquired / unreadable at the time of acquisition</strong>.
            Do not report it as an absence of recent activity — no inference of any kind about what
            the device's owner had open can be drawn from this page.
          </p>
          {summary.reason && (
            <div className="mt-3 rounded-md border border-line bg-panel-2 p-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Reason recorded by the collector (verbatim)
              </div>
              <p className="text-sm text-ink whitespace-pre-line">{summary.reason}</p>
            </div>
          )}
          <p className="text-xs text-muted leading-relaxed mt-3">
            To obtain this artifact, the device must be in an After-First-Unlock state at
            acquisition time. Re-acquiring after a reboot will put it back into BFU and lose it
            again.
          </p>
          {summary.caveats && summary.caveats.length > 0 && <Caveats items={summary.caveats} />}
        </div>
      </div>
    );
  }

  /* ------------------------------------------------------------------ */
  /* Normal render.                                                      */
  /* ------------------------------------------------------------------ */
  const volatileCount = tasks.filter((t) => t.volatile).length;
  const abxCount = tasks.filter((t) => t.encoding === "abx").length;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <Header />

      {/* Volatility banner — governs every reading of this page. */}
      <div className="card p-4 mb-4 border-warn/40 bg-warn/5">
        <div className="text-sm font-semibold text-warn mb-1">
          Recents are volatile — absence proves nothing
        </div>
        <ul className="text-xs text-warn leading-relaxed space-y-1 list-disc pl-4">
          <li>
            A task disappears from this list when the user swipes it away, when the app is
            force-stopped, on every reboot, and whenever the OS trims memory under pressure. An app
            that is missing here may have been used minutes earlier.
          </li>
          <li>
            A snapshot's timestamp is the <strong>file's mtime</strong> — the moment the system
            wrote or rewrote the thumbnail. It is not the moment a person looked at that screen, and
            the OS re-writes snapshots on rotation, theme change and task reordering.
          </li>
          <li>
            Windows marked <code className="font-mono">FLAG_SECURE</code> (banking apps, password
            managers, many messengers' privacy modes) are captured blank or substituted with a
            placeholder. An empty-looking snapshot is <strong>not</strong> evidence of an empty
            screen, and its size tells you nothing about the content.
          </li>
          <li>
            The list is per-user. On a device with a work profile or second user, other users'
            recents are stored separately and are not represented here.
          </li>
        </ul>
      </div>

      {tasks.length === 0 && snapshots.length === 0 ? (
        <div className="card p-6 max-w-3xl">
          <div className="text-warn font-semibold mb-2">No recent-task records in this case</div>
          <p className="text-sm text-muted leading-relaxed">
            The recent-task list is a <strong>Tier-2 (root)</strong> artifact read from{" "}
            <code className="text-ink">/data/system_ce/&lt;user&gt;/recent_tasks</code>. A
            Tier-0/Tier-1 acquisition cannot reach it, so on a non-root collection this page is
            empty by definition.
          </p>
          <p className="text-sm text-muted leading-relaxed mt-2">
            Where root was available, an empty list is genuinely ambiguous and must be reported as
            such: it is equally consistent with a device that was rebooted before seizure, with
            recents having been swiped away, and with the collector having been unable to parse the
            store. This view cannot distinguish those, and neither should the report.
          </p>
          {summary.reason && (
            <div className="mt-3 rounded-md border border-line bg-panel-2 p-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Collector note (verbatim)
              </div>
              <p className="text-sm text-ink whitespace-pre-line">{summary.reason}</p>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <StatCard n={tasks.length} label="Recent tasks" />
            <StatCard n={namedSnapshots.length} label="Snapshot files" tone="text-accent" />
            <StatCard n={volatileCount} label="Marked volatile" tone="text-deletion" />
            <StatCard n={abxCount} label="ABX-encoded" tone="text-recovered" />
          </div>

          {/* Filter */}
          <div className="mb-3">
            <input
              className="input max-w-sm"
              placeholder="Filter by activity, package, affinity or label…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {/* Task table */}
          <h2 className="text-sm font-semibold text-ink mb-2">Task list</h2>
          <div className="card overflow-auto mb-2">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <SortTh className="th w-20" label="Task" sortKeyName="task_id" getValue={(t: RecentTaskRecord) => t.task_id} sort={tasksSort} />
                  <SortTh className="th" label="Activity / label" sortKeyName="real_activity" getValue={(t: RecentTaskRecord) => t.real_activity} sort={tasksSort} />
                  <SortTh className="th w-44" label="Calling package" sortKeyName="calling_package" getValue={(t: RecentTaskRecord) => t.calling_package} sort={tasksSort} />
                  <SortTh className="th w-44" label="Last moved" sortKeyName="last_time_moved" getValue={(t: RecentTaskRecord) => t.last_time_moved} sort={tasksSort} />
                  <SortTh className="th" label="Intent" sortKeyName="intent_action" getValue={(t: RecentTaskRecord) => t.intent_action} sort={tasksSort} />
                  <SortTh className="th w-44" label="Snapshot" sortKeyName="snapshot_file" getValue={(t: RecentTaskRecord) => t.snapshot_file} sort={tasksSort} />
                  <SortTh className="th w-28" label="Encoding" sortKeyName="encoding" getValue={(t: RecentTaskRecord) => t.encoding} sort={tasksSort} />
                  <SortTh className="th w-28" label="Confidence" sortKeyName="confidence" getValue={(t: RecentTaskRecord) => t.confidence} sort={tasksSort} />
                </tr>
              </thead>
              <tbody>
                {tasksSort.sorted.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="text-center py-8 text-muted text-xs">
                      No tasks match your filter.
                    </td>
                  </tr>
                ) : (
                  tasksSort.sorted.map((t, i) => (
                    <tr key={i}>
                      <td className="td font-mono text-xs align-top">
                        {t.task_id}
                        {t.volatile && (
                          <div className="mt-1">
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-deletion/20 text-deletion">
                              volatile
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="td align-top">
                        <div className="text-ink text-xs font-mono break-all">
                          {t.real_activity || <span className="text-muted italic">—</span>}
                        </div>
                        {t.task_label && <div className="text-[11px] text-muted">{t.task_label}</div>}
                        {t.affinity && (
                          <div className="text-[10px] text-muted font-mono break-all">
                            affinity: {t.affinity}
                          </div>
                        )}
                        <Caveats items={t.caveats} />
                      </td>
                      <td className="td align-top font-mono text-xs break-all">
                        {t.calling_package || <span className="text-muted italic">—</span>}
                      </td>
                      <td className="td align-top font-mono text-xs">
                        {t.last_time_moved ? (
                          fmtTs(t.last_time_moved)
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                        <div className="text-[10px] text-muted">device clock</div>
                      </td>
                      <td className="td align-top">
                        <div className="text-[11px] font-mono text-muted break-all">
                          {t.intent_action || "—"}
                        </div>
                        <div className="text-[10px] font-mono text-muted break-all">
                          {t.intent_component || ""}
                        </div>
                      </td>
                      <td className="td align-top">
                        {t.snapshot_file ? (
                          <>
                            <div className="text-[11px] font-mono text-ink break-all">
                              {t.snapshot_file}
                            </div>
                            <div className="text-[10px] text-muted">
                              {t.snapshot_size != null ? bytes(t.snapshot_size) : "size unknown"}
                              {t.snapshot_modified ? ` · mtime ${fmtTs(t.snapshot_modified)}` : ""}
                            </div>
                          </>
                        ) : (
                          <span className="text-muted text-xs italic">none retained</span>
                        )}
                      </td>
                      <td className="td align-top">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-panel border border-line text-muted font-mono">
                          {t.encoding || "unknown"}
                        </span>
                      </td>
                      <td className="td align-top">
                        <ConfidenceBadge value={t.confidence} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted mb-6 leading-relaxed">
            <strong className="text-ink">"Last moved"</strong> is when the activity manager last
            reordered the task, taken from the device's own clock — it is not a user-interaction
            timestamp, and it shifts when the OS restores or reshuffles tasks without anyone
            touching the screen.
            {abxCount > 0 && (
              <>
                {" "}
                {abxCount} task{abxCount === 1 ? " was" : "s were"} stored in Android Binary XML
                (ABX) and had to be decoded before parsing; decoding is deterministic but any
                decoder disagreement would surface as a parse caveat on the row.
              </>
            )}
          </p>

          {/* Snapshot catalogue — metadata only, deliberately no image rendering. */}
          <h2 className="text-sm font-semibold text-ink mb-2">Snapshot catalogue</h2>
          {namedSnapshots.length === 0 ? (
            <div className="card p-4 text-xs text-muted leading-relaxed">
              No snapshot files were catalogued. Thumbnails are aggressively pruned by the OS, and
              FLAG_SECURE windows may never have produced one — so this is not an indication that
              the tasks above were never displayed.
            </div>
          ) : (
            <div className="card overflow-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <SortTh className="th" label="File" sortKeyName="file" getValue={(s: TaskSnapshotRecord) => snapshotName(s)} sort={snapshotsSort} />
                    <SortTh className="th w-24" label="Task" sortKeyName="task_id" getValue={(s: TaskSnapshotRecord) => s.task_id} sort={snapshotsSort} />
                    <SortTh className="th w-28" label="Size" sortKeyName="size" getValue={(s: TaskSnapshotRecord) => s.size} sort={snapshotsSort} />
                    <SortTh className="th w-48" label="Modified (file mtime)" sortKeyName="modified" getValue={(s: TaskSnapshotRecord) => s.modified} sort={snapshotsSort} />
                    <SortTh className="th w-28" label="Kind" sortKeyName="kind" getValue={(s: TaskSnapshotRecord) => s.kind} sort={snapshotsSort} />
                  </tr>
                </thead>
                <tbody>
                  {snapshotsSort.sorted.map((s, i) => (
                    <tr key={i}>
                      <td className="td font-mono text-xs break-all">{snapshotName(s)}</td>
                      <td className="td font-mono text-xs">
                        {s.task_id != null && s.task_id !== "" ? (
                          s.task_id
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td className="td font-mono text-xs">
                        {s.size != null ? bytes(s.size) : <span className="text-muted">—</span>}
                      </td>
                      <td className="td font-mono text-xs">
                        {s.modified ? fmtTs(s.modified) : <span className="text-muted">—</span>}
                      </td>
                      <td className="td text-xs text-muted">{s.kind || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-muted mt-2 leading-relaxed">
            Snapshot images are <strong>not displayed</strong> in this view. They are catalogued by
            name, size and mtime only: a thumbnail is a partial, downscaled and possibly substituted
            rendering of a screen, a small file size is fully explained by a blanked FLAG_SECURE
            window, and displaying them invites reading content into an image the OS may never have
            captured. The files themselves remain in the case's evidence store with their hashes.
          </p>

          {summary.note && (
            <div className="card p-3 mt-4 text-xs leading-relaxed">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Engine summary (verbatim)
              </div>
              <p className="text-ink whitespace-pre-line">{summary.note}</p>
            </div>
          )}
          {summary.caveats && summary.caveats.length > 0 && (
            <div className="card p-3 mt-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1">
                Dataset-level caveats
              </div>
              <Caveats items={summary.caveats} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
