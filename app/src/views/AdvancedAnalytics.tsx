/**
 * AdvancedAnalytics — the derived-metrics report from
 * engine/triage/advanced/features.py (`generate_advanced_report` /
 * `run_advanced_analysis`, written to the "advanced" dataset).
 *
 * This is best-effort statistical analysis over whatever messages (live + recovered) this
 * acquisition actually collected. It is not a new collection stage and it collects nothing itself
 * — an empty or thin section here means "not enough messages to compute this", never "nothing
 * happened". The one exception this view does NOT re-render is the social graph itself: a
 * richer, dedicated build already lives in the Social Graph view, and duplicating it here would
 * just give the two a chance to drift out of sync.
 */
import { useEffect, useState } from "react";
import { Brain, ArrowRight } from "lucide-react";
import { api } from "../lib/api";
import { fmtTs } from "../lib/hooks";
import { StatCard } from "../components/common";
import type { ViewKey } from "../components/Sidebar";

// ---------------------------------------------------------------------------
// Types — mirror engine/triage/advanced/features.py exactly (field names and
// nesting were read from the five analyzer methods, not guessed).
// ---------------------------------------------------------------------------

export interface SocialGraphNode {
  id: string;
  messages: number;
  sent: number;
  received: number;
}
export interface SocialGraphEdge {
  source: string;
  target: string;
  weight: number;
  direction: "outgoing" | "incoming";
}
export interface TopContact {
  name: string;
  messages: number;
}
export interface SocialGraph {
  nodes: SocialGraphNode[];
  edges: SocialGraphEdge[];
  stats: { unique_contacts: number; total_edges: number; most_active: string | null };
  top_contacts: TopContact[];
}

export interface Burst {
  start: string | null;
  end: string | null;
  count: number;
  senders: string[];
}
export interface ResponseTimes {
  fast: number;
  medium: number;
  slow: number;
  mean_s: number;
  samples: number;
}
export interface CommunicationPatterns {
  bursts: Burst[];
  response_times: ResponseTimes;
  hourly_distribution: Record<string, number>;
  daily_distribution: Record<string, number>;
  peak_hour: number | null;
  quiet_hours_activity: number;
}

export interface TimelineEventLite {
  timestamp: string | null;
  sender: string;
  direction: string;
  confidence: string;
  has_flags: boolean;
}
export interface AdvancedTimeline {
  events: TimelineEventLite[];
  activity_heatmap: Record<string, number>;
  date_range: { start: string | null; end: string | null };
  total_days_active: number;
  avg_messages_per_active_day: number;
}

export interface VolumeSpike {
  hour: number;
  count: number;
  zscore: number;
  timestamps: string[];
}
export interface QuietHoursEvent {
  timestamp: string | null;
  sender: string;
  direction: string;
}
export interface RapidSwitch {
  window_start: string;
  window_end: string;
  unique_senders: number;
  message_count: number;
  senders: string[];
}
export interface ConfidenceDowngrade {
  timestamp: string | null;
  confidence: string;
  provenance: string;
}
export interface Anomalies {
  volume_spikes: VolumeSpike[];
  quiet_hours_events: QuietHoursEvent[];
  rapid_switches: RapidSwitch[];
  confidence_downgrades: ConfidenceDowngrade[];
  summary: {
    total_spikes: number;
    quiet_hours_total: number;
    rapid_switch_events: number;
    confidence_downgrade_n: number;
  };
}

export interface RecoveryMetrics {
  total: number;
  by_confidence: Record<string, number>;
  with_timestamp: number;
  with_identified_sender: number;
  with_substantive_body: number;
  body_completeness_pct: number;
  recovery_rate_estimate: number;
}

export interface AdvancedMeta {
  total_messages: number;
  live_messages: number;
  recovered_messages: number;
}

export interface AdvancedReport {
  social_graph?: SocialGraph;
  communication_patterns?: CommunicationPatterns;
  timeline?: AdvancedTimeline;
  anomalies?: Anomalies;
  recovery_metrics?: RecoveryMetrics;
  meta?: AdvancedMeta;
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function Card({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="card p-4 mb-5">
      <h2 className="text-sm font-semibold text-ink mb-0.5">{title}</h2>
      {sub && <p className="text-xs text-muted mb-3 leading-relaxed">{sub}</p>}
      <div className={sub ? "" : "mt-3"}>{children}</div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted leading-relaxed italic">{children}</p>;
}

function Chip({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return (
    <span
      className={`text-[10px] font-mono px-1.5 py-0.5 rounded border border-line ${
        tone ?? "bg-panel text-muted"
      }`}
    >
      {children}
    </span>
  );
}

function MiniStat({ n, label }: { n: React.ReactNode; label: string }) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold text-ink">{n}</div>
      <div className="text-[10px] uppercase tracking-wider text-muted mt-0.5">{label}</div>
    </div>
  );
}

function CappedList<T>({
  items,
  cap,
  render,
  emptyText,
  noun,
}: {
  items: T[];
  cap: number;
  render: (item: T, i: number) => React.ReactNode;
  emptyText: string;
  noun: string;
}) {
  if (items.length === 0) return <Empty>{emptyText}</Empty>;
  const shown = items.slice(0, cap);
  return (
    <div className="space-y-1.5">
      {shown.map((it, i) => render(it, i))}
      {items.length > cap && (
        <div className="text-[11px] text-muted italic pt-1">
          +{items.length - cap} more {noun} not shown here.
        </div>
      )}
    </div>
  );
}

/** 24-hour bar strip. `dist` keys are hour numbers as strings ("0".."23"). */
function HourBars({ dist, peakHour }: { dist: Record<string, number>; peakHour: number | null }) {
  const max = Math.max(1, ...Object.values(dist));
  return (
    <div className="flex items-end gap-[2px] h-16">
      {Array.from({ length: 24 }, (_, h) => {
        const v = dist[String(h)] ?? 0;
        const pct = Math.round((v / max) * 100);
        const isPeak = peakHour === h;
        return (
          <div key={h} className="flex-1 flex flex-col items-center justify-end h-full" title={`${h}:00 — ${v}`}>
            <div
              className={`w-full rounded-t ${isPeak ? "bg-accent" : "bg-recovered/60"}`}
              style={{ height: `${Math.max(pct, v > 0 ? 4 : 0)}%` }}
            />
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function AdvancedAnalyticsView({
  caseId,
  setView,
}: {
  caseId: string;
  setView: (v: ViewKey) => void;
}) {
  const [report, setReport] = useState<AdvancedReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .dataset<AdvancedReport>(caseId, "advanced")
      .then((d) => alive && setReport(d ?? {}))
      .catch(() => alive && setReport({}))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [caseId]);

  if (loading) return <div className="p-8 text-muted text-sm animate-pulse">Loading advanced analysis…</div>;

  const rec = report ?? {};
  const meta = rec.meta;
  const social = rec.social_graph;
  const patterns = rec.communication_patterns;
  const timeline = rec.timeline;
  const anomalies = rec.anomalies;
  const recovery = rec.recovery_metrics;

  const header = (
    <div className="mb-5">
      <h1 className="text-xl font-bold mb-1 flex items-center gap-2">
        <Brain className="h-4 w-4" strokeWidth={1.75} aria-hidden /> Advanced Analytics
        <span className="text-xs font-normal text-muted bg-panel-2 border border-line rounded px-2 py-0.5 ml-1">
          Derived — best effort
        </span>
      </h1>
      <p className="text-sm text-muted leading-relaxed">
        Statistical analysis run over the messages this acquisition actually collected — bursts,
        response times, timeline shape and outliers. It computes nothing new about the device and
        collects no additional evidence; it re-describes what {"'"}Messages{"'"} and {"'"}
        Recovered{"'"} already contain.
      </p>
    </div>
  );

  const totalMessages = meta?.total_messages ?? 0;

  if (!meta || totalMessages === 0) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        {header}
        <div className="card p-6 border-warn/40 bg-warn/5">
          <div className="text-warn font-semibold mb-2">No advanced analysis available</div>
          <p className="text-sm text-muted leading-relaxed">
            This report is only produced when there are enough live and recovered messages to
            analyze. An empty page here means{" "}
            <strong className="text-ink">not enough messages were collected to run this
            analysis</strong> — it is not a finding that no communication occurred. Check the{" "}
            <button className="text-accent underline underline-offset-2" onClick={() => setView("messages")}>
              Messages
            </button>{" "}
            and{" "}
            <button className="text-accent underline underline-offset-2" onClick={() => setView("recovered")}>
              Recovered
            </button>{" "}
            views for what was actually acquired.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {header}

      {/* Meta */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-5">
        <StatCard n={meta.total_messages} label="Messages analyzed" />
        <StatCard n={meta.live_messages} label="Live" tone="text-live" />
        <StatCard n={meta.recovered_messages} label="Recovered / carved" tone="text-recovered" />
      </div>

      {/* Recovery metrics */}
      <Card
        title="Recovery quality"
        sub="How complete the recovered/carved message set is — not whether its contents are correct."
      >
        {!recovery || recovery.total === 0 ? (
          <Empty>
            No recovered (non-live) messages to score. This appears both when every collected
            message is live and when nothing was collected at all — it does not distinguish the two.
          </Empty>
        ) : (
          <>
            <div className="flex flex-col md:flex-row gap-4 md:items-center mb-4">
              <div className="shrink-0 text-center px-4 py-2 rounded-lg bg-panel-2 border border-line">
                <div className="text-3xl font-bold text-accent">{recovery.body_completeness_pct}%</div>
                <div className="text-[10px] uppercase tracking-wider text-muted mt-0.5">Body completeness</div>
              </div>
              <p className="text-xs text-muted leading-relaxed">
                The share of recovered rows with a substantive (non-trivial) body string. This
                measures how much text survived recovery, <strong className="text-ink">not</strong>{" "}
                whether that text is accurate, complete, or correctly attributed — a "complete"
                recovered message can still be garbled, truncated mid-sentence, or misattributed to
                the wrong sender.
              </p>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              <MiniStat n={recovery.total} label="Recovered rows" />
              <MiniStat n={recovery.with_timestamp} label="With timestamp" />
              <MiniStat n={recovery.with_identified_sender} label="With identified sender" />
              <MiniStat n={recovery.with_substantive_body} label="With substantive body" />
            </div>
            <div className="border-t border-line pt-3">
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">By confidence tier</div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(recovery.by_confidence).map(([k, v]) => (
                  <Chip key={k}>
                    {k}: {v}
                  </Chip>
                ))}
              </div>
            </div>
          </>
        )}
      </Card>

      {/* Social graph summary — the full graph lives elsewhere. */}
      <Card
        title="Social graph (summary)"
        sub="A quick summary only. The full interactive social-graph build — nodes, edges, laid out — lives in its own view."
      >
        <div className="flex items-center justify-between mb-3">
          <div />
          <button
            className="text-xs text-accent underline underline-offset-2 shrink-0"
            onClick={() => setView("graph")}
          >
            <span className="inline-flex items-center gap-1">
              Open Social Graph <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
            </span>
          </button>
        </div>
        {!social || social.stats.unique_contacts === 0 ? (
          <Empty>No contacts could be resolved from the collected messages.</Empty>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-3">
              <MiniStat n={social.stats.unique_contacts} label="Unique contacts" />
              <MiniStat n={social.stats.total_edges} label="Edges" />
              <MiniStat n={social.stats.most_active ?? "—"} label="Most active contact" />
            </div>
            <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
              Top contacts by message count
            </div>
            <CappedList
              items={social.top_contacts}
              cap={10}
              noun="contacts"
              emptyText="No contacts ranked."
              render={(c, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="text-ink truncate flex-1">{c.name}</span>
                  <span className="text-muted font-mono">{c.messages}</span>
                </div>
              )}
            />
          </>
        )}
      </Card>

      {/* Communication patterns */}
      <Card title="Communication patterns" sub="Message bursts, response latency and time-of-day shape.">
        {!patterns || (patterns.bursts.length === 0 && patterns.response_times.samples === 0 && Object.keys(patterns.hourly_distribution).length === 0) ? (
          <Empty>
            Not enough timestamped messages to compute bursts, response times or a time-of-day
            distribution.
          </Empty>
        ) : (
          <div className="space-y-4">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                Hourly distribution (device clock){" "}
                {patterns.peak_hour !== null && (
                  <span className="text-accent">— peak {patterns.peak_hour}:00</span>
                )}
              </div>
              {Object.keys(patterns.hourly_distribution).length === 0 ? (
                <Empty>No hour-of-day distribution available.</Empty>
              ) : (
                <HourBars dist={patterns.hourly_distribution} peakHour={patterns.peak_hour} />
              )}
              <div className="text-[11px] text-muted mt-1">
                Quiet-hours (00:00–05:00) activity: <span className="text-ink font-mono">{patterns.quiet_hours_activity}</span>{" "}
                message{patterns.quiet_hours_activity === 1 ? "" : "s"} — not inherently suspicious; night-shift
                work, time-zone travel and insomnia produce the same signal.
              </div>
            </div>

            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">Day-of-week distribution</div>
              {Object.keys(patterns.daily_distribution).length === 0 ? (
                <Empty>No day-of-week distribution available.</Empty>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {Object.entries(patterns.daily_distribution).map(([day, count]) => (
                    <Chip key={day}>
                      {day}: {count}
                    </Chip>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                  Response times ({patterns.response_times.samples} sample
                  {patterns.response_times.samples === 1 ? "" : "s"})
                </div>
                {patterns.response_times.samples === 0 ? (
                  <Empty>
                    No{" "}
                    <span className="inline-flex items-center gap-1">
                      incoming <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden /> outgoing
                    </span>{" "}
                    reply pairs could be timed.
                  </Empty>
                ) : (
                  <>
                    <div className="flex gap-2 mb-1">
                      <Chip tone="bg-live/15 text-live">fast: {patterns.response_times.fast}</Chip>
                      <Chip tone="bg-recovered/15 text-recovered">medium: {patterns.response_times.medium}</Chip>
                      <Chip tone="bg-warn/15 text-warn">slow: {patterns.response_times.slow}</Chip>
                    </div>
                    <div className="text-[11px] text-muted">
                      Mean reply time: <span className="text-ink font-mono">{patterns.response_times.mean_s}s</span>
                    </div>
                  </>
                )}
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                  Bursts ({patterns.bursts.length})
                </div>
                <CappedList
                  items={patterns.bursts}
                  cap={8}
                  noun="bursts"
                  emptyText="No message bursts (3+ messages within 5 minutes) were detected."
                  render={(b, i) => (
                    <div key={i} className="text-[11px] text-muted flex flex-wrap gap-x-2">
                      <span className="text-ink font-mono">{b.count} msgs</span>
                      <span className="inline-flex items-center gap-1">
                        {fmtTs(b.start)} <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden /> {fmtTs(b.end)}
                      </span>
                      <span className="text-muted/80">({b.senders.length} sender{b.senders.length === 1 ? "" : "s"})</span>
                    </div>
                  )}
                />
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Timeline */}
      <Card title="Timeline shape" sub="Chronological span and activity density of the analyzed messages.">
        {!timeline || timeline.events.length === 0 ? (
          <Empty>No timestamped messages were available to build a timeline.</Empty>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              <MiniStat n={fmtTs(timeline.date_range.start)} label="Earliest" />
              <MiniStat n={fmtTs(timeline.date_range.end)} label="Latest" />
              <MiniStat n={timeline.total_days_active} label="Active days" />
              <MiniStat n={timeline.avg_messages_per_active_day} label="Avg msgs / active day" />
            </div>
            <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
              Busiest time buckets ({Object.keys(timeline.activity_heatmap).length} total, 1-hour buckets)
            </div>
            <CappedList
              items={Object.entries(timeline.activity_heatmap).sort((a, b) => b[1] - a[1])}
              cap={10}
              noun="time buckets"
              emptyText="No activity buckets recorded."
              render={([bucket, count], i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="text-ink font-mono">{fmtTs(bucket)}</span>
                  <span className="text-muted">{count} msg{count === 1 ? "" : "s"}</span>
                </div>
              )}
            />
            <div className="text-[10px] uppercase tracking-wider text-muted mt-4 mb-1.5">
              Most recent events ({timeline.events.length} total)
            </div>
            <CappedList
              items={[...timeline.events].reverse()}
              cap={15}
              noun="earlier events"
              emptyText="No events recorded."
              render={(e, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="text-ink font-mono">{fmtTs(e.timestamp)}</span>
                  <span className="text-muted truncate flex-1">{e.sender || "unknown sender"}</span>
                  <span className="text-muted/80">{e.direction}</span>
                  <Chip>{e.confidence}</Chip>
                  {e.has_flags && <Chip tone="bg-warn/15 text-warn">flagged</Chip>}
                </div>
              )}
            />
          </>
        )}
      </Card>

      {/* Anomalies */}
      <Card
        title="Anomalies"
        sub="Statistical outliers only — a flag here is a reason to look closer, not a conclusion."
      >
        {!anomalies ? (
          <Empty>No anomaly detection was run.</Empty>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              <MiniStat n={anomalies.summary.total_spikes} label="Volume spikes" />
              <MiniStat n={anomalies.summary.quiet_hours_total} label="Quiet-hours msgs" />
              <MiniStat n={anomalies.summary.rapid_switch_events} label="Rapid switches" />
              <MiniStat n={anomalies.summary.confidence_downgrade_n} label="Confidence downgrades" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                  Volume spikes (hourly z-score ≥ threshold)
                </div>
                <CappedList
                  items={anomalies.volume_spikes}
                  cap={10}
                  noun="spikes"
                  emptyText="No hour's message volume was a statistical outlier."
                  render={(s, i) => (
                    <div key={i} className="text-[11px] text-muted flex gap-2 flex-wrap">
                      <span className="text-ink font-mono">{s.hour}:00</span>
                      <span>{s.count} msgs</span>
                      <span className="text-muted/80">z={s.zscore}</span>
                    </div>
                  )}
                />
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                  Rapid sender switches (&gt;2 senders in a short window)
                </div>
                <CappedList
                  items={anomalies.rapid_switches}
                  cap={10}
                  noun="switch events"
                  emptyText="No rapid multi-sender switching windows were detected."
                  render={(r, i) => (
                    <div key={i} className="text-[11px] text-muted flex gap-2 flex-wrap">
                      <span className="text-ink font-mono">{r.unique_senders} senders</span>
                      <span>{r.message_count} msgs</span>
                      <span className="inline-flex items-center gap-1">
                        {fmtTs(r.window_start)} <ArrowRight className="inline h-3.5 w-3.5" strokeWidth={1.75} aria-hidden /> {fmtTs(r.window_end)}
                      </span>
                    </div>
                  )}
                />
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                  Quiet-hours activity (00:00–05:00)
                </div>
                <CappedList
                  items={anomalies.quiet_hours_events}
                  cap={10}
                  noun="quiet-hour events"
                  emptyText="No messages fell in the quiet-hours window."
                  render={(q, i) => (
                    <div key={i} className="text-[11px] text-muted flex gap-2 flex-wrap">
                      <span className="text-ink font-mono">{fmtTs(q.timestamp)}</span>
                      <span className="truncate">{q.sender || "unknown"}</span>
                      <span className="text-muted/80">{q.direction}</span>
                    </div>
                  )}
                />
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted mb-1.5">
                  Confidence downgrades (carved / recovered amid live data)
                </div>
                <CappedList
                  items={anomalies.confidence_downgrades}
                  cap={10}
                  noun="downgrade events"
                  emptyText="No confidence downgrades were recorded among the analyzed messages."
                  render={(c, i) => (
                    <div key={i} className="text-[11px] text-muted flex gap-2 flex-wrap">
                      <span className="text-ink font-mono">{fmtTs(c.timestamp)}</span>
                      <Chip tone="bg-carved/15 text-carved">{c.confidence}</Chip>
                      {c.provenance && <span className="text-muted/80 truncate">{c.provenance}</span>}
                    </div>
                  )}
                />
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
