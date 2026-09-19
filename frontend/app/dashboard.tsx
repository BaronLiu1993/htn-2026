"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  analyzeSubmissions,
  fetchAppetiteStatus,
  fetchHealth,
  fetchSubmissions,
} from "@/lib/api";
import type {
  AnalysisRun,
  AnalysisStatus,
  AppetiteStatus,
  Assessment,
  EvidenceItem,
  QueueSubmission,
  RuleOutcome,
  TraceEvent,
  UnderwritingConsideration,
} from "@/lib/types";

import {
  ActivityIcon,
  AlertIcon,
  CheckIcon,
  ChevronIcon,
  ClockIcon,
  CloseIcon,
  DatabaseIcon,
  QueueIcon,
  SearchIcon,
  SettingsIcon,
  ShieldIcon,
  SparkIcon,
} from "./icons";

type DisplayItem = QueueSubmission | Assessment;
type Filter = "all" | AnalysisStatus;
type DetailTab = "decision" | "evidence" | "trace";
const PAGE_SIZE = 25;

const STATUS_META: Record<
  AnalysisStatus,
  { label: string; shortLabel: string; tone: string }
> = {
  target: { label: "Target", shortLabel: "Target", tone: "positive" },
  acceptable: {
    label: "Acceptable",
    shortLabel: "Acceptable",
    tone: "informative",
  },
  needs_review: {
    label: "Needs review",
    shortLabel: "Review",
    tone: "warning",
  },
  out_of_appetite: {
    label: "Out of appetite",
    shortLabel: "Out",
    tone: "negative",
  },
  not_analyzed: {
    label: "Not analyzed",
    shortLabel: "Pending",
    tone: "neutral",
  },
};

const FILTERS: Filter[] = [
  "all",
  "target",
  "acceptable",
  "needs_review",
  "out_of_appetite",
];

function isAssessment(item: DisplayItem): item is Assessment {
  return "status" in item;
}

function itemStatus(item: DisplayItem): AnalysisStatus {
  return isAssessment(item) ? item.status : item.analysis_status;
}

function formatMoney(value?: number | null, compact = false) {
  if (value === null || value === undefined) return "—";
  if (compact && Math.abs(value) >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(value >= 100_000_000 ? 0 : 1)}M`;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "Not available";
  if (typeof value === "number") {
    if (value >= 1_000) return formatMoney(value, value >= 1_000_000);
    return String(value);
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).replaceAll("_", " ");
}

function formatRuleValue(rule: RuleOutcome) {
  if (rule.actual_value === null || rule.actual_value === undefined) {
    return "Not available";
  }
  if (["R4", "R5", "R8", "P2", "P3"].includes(rule.rule_id)) {
    return typeof rule.actual_value === "number"
      ? formatMoney(rule.actual_value, rule.actual_value >= 1_000_000)
      : formatValue(rule.actual_value);
  }
  if (rule.rule_id === "R7" && typeof rule.actual_value === "number") {
    return `${rule.actual_value}%`;
  }
  if (["R6", "P4"].includes(rule.rule_id)) {
    return String(rule.actual_value);
  }
  return formatValue(rule.actual_value);
}

function formatEvidenceValue(item: EvidenceItem) {
  if (item.field.includes("year")) return String(item.value ?? "Not available");
  if (
    typeof item.value === "number" &&
    ["tiv", "premium", "loss_value"].some((field) => item.field.includes(field))
  ) {
    return formatMoney(item.value, item.value >= 1_000_000);
  }
  return formatValue(item.value);
}

function StatusBadge({ status }: { status: AnalysisStatus }) {
  const meta = STATUS_META[status];
  return (
    <Badge variant="outline" className={`status-badge status-${meta.tone}`}>
      <span className="status-dot" />
      {meta.label}
    </Badge>
  );
}

function RuleStateIcon({ rule }: { rule: RuleOutcome }) {
  if (rule.state === "passed" || rule.state === "matched") {
    return (
      <span className="rule-icon rule-icon-success">
        <CheckIcon size={14} />
      </span>
    );
  }
  if (rule.state === "failed") {
    return (
      <span className="rule-icon rule-icon-failure">
        <CloseIcon size={14} />
      </span>
    );
  }
  if (rule.state === "unresolved") {
    return (
      <span className="rule-icon rule-icon-warning">
        <AlertIcon size={14} />
      </span>
    );
  }
  return <span className="rule-icon rule-icon-muted">—</span>;
}

function RuleList({ rules, empty }: { rules: RuleOutcome[]; empty: string }) {
  if (!rules.length) return <p className="empty-inline">{empty}</p>;
  return (
    <div className="rule-list">
      {rules.map((rule) => (
        <div className="rule-row" key={`${rule.rule_id}-${rule.state}`}>
          <RuleStateIcon rule={rule} />
          <div className="rule-copy">
            <div className="rule-title-line">
              <strong>{rule.name}</strong>
            </div>
            <p>
              {formatRuleValue(rule)} <span>· Expected {rule.expected}</span>
            </p>
            {rule.note ? <small>{rule.note}</small> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function CopeGrid({ items }: { items: UnderwritingConsideration[] }) {
  return (
    <div className="cope-grid">
      {items.map((item) => (
        <article className={`cope-card cope-${item.status}`} key={item.category}>
          <div>
            <strong>{item.category}</strong>
            <span>{item.status}</span>
          </div>
          <p>{item.summary}</p>
          <small>
            {item.appetite_rule_applied
              ? "Approved appetite rule applied"
              : "Informational only — no carrier rule supplied"}
          </small>
        </article>
      ))}
    </div>
  );
}

function TraceList({ events }: { events: TraceEvent[] }) {
  if (!events.length) {
    return <p className="empty-inline">No activity has been recorded yet.</p>;
  }
  return (
    <div className="trace-list">
      {events.map((event, index) => (
        <div className="trace-row" key={event.id}>
          <div className="trace-rail">
            <span className={`trace-node trace-${event.status}`}>
              {event.tool === "discover_schema" ? (
                <DatabaseIcon size={13} />
              ) : event.tool === "evaluate_appetite" ? (
                <SparkIcon size={13} />
              ) : event.tool === "openai_agent" ? (
                <SparkIcon size={13} />
              ) : (
                <ActivityIcon size={13} />
              )}
            </span>
            {index < events.length - 1 ? <span className="trace-line" /> : null}
          </div>
          <div className="trace-copy">
            <div className="trace-heading">
              <strong>{event.purpose}</strong>
              <span>{event.duration_ms} ms</span>
            </div>
            <p>{event.result_summary}</p>
            <small>
              {event.tool.replaceAll("_", " ")} · {event.status}
            </small>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [queue, setQueue] = useState<QueueSubmission[]>([]);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [mode, setMode] = useState<"demo" | "live">("demo");
  const [agentConfigured, setAgentConfigured] = useState(false);
  const [appetite, setAppetite] = useState<AppetiteStatus | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Assessment | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("decision");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoadingQueue(true);
      try {
        const [health, submissions, appetiteStatus] = await Promise.all([
          fetchHealth(),
          fetchSubmissions(),
          fetchAppetiteStatus(),
        ]);
        if (!cancelled) {
          setMode(health.mode);
          setAgentConfigured(health.openai_configured);
          setQueue(submissions);
          setAppetite(appetiteStatus);
          setError(null);
        }
      } catch (cause) {
        if (!cancelled) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Unable to connect to the UnderwriteIQ API.",
          );
        }
      } finally {
        if (!cancelled) setLoadingQueue(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const items: DisplayItem[] = run?.assessments ?? queue;
  const filteredItems = useMemo(() => {
    const query = search.trim().toLowerCase();
    return items.filter((item) => {
      const matchesFilter = filter === "all" || itemStatus(item) === filter;
      const matchesSearch =
        !query ||
        item.insured_name.toLowerCase().includes(query) ||
        item.submission_number.toLowerCase().includes(query) ||
        item.primary_state?.toLowerCase().includes(query);
      return matchesFilter && matchesSearch;
    });
  }, [filter, items, search]);
  const totalPages = Math.max(1, Math.ceil(filteredItems.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageStart = (safePage - 1) * PAGE_SIZE;
  const visibleItems = filteredItems.slice(pageStart, pageStart + PAGE_SIZE);

  const counts = useMemo(() => {
    const result = {
      target: 0,
      acceptable: 0,
      needs_review: 0,
      out_of_appetite: 0,
      not_analyzed: 0,
    };
    if (run) {
      for (const item of run.assessments) result[item.status] += 1;
    }
    return result;
  }, [run]);

  async function handleAnalyze() {
    setAnalyzing(true);
    setError(null);
    try {
      const result = await analyzeSubmissions();
      setRun(result);
      setMode(result.mode);
      setFilter("all");
      setPage(1);
      setSelected(result.assessments[0] ?? null);
      setDetailTab("decision");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "The analysis run could not be completed.",
      );
    } finally {
      setAnalyzing(false);
    }
  }

  function openDetail(item: DisplayItem) {
    if (!isAssessment(item)) return;
    setSelected(item);
    setDetailTab("decision");
  }

  const selectedTrace = selected
    ? (run?.trace ?? []).filter(
        (event) => !event.submission_id || event.submission_id === selected.submission_id,
      )
    : [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark">
            <ShieldIcon size={22} />
          </span>
          <div>
            <strong>UnderwriteIQ</strong>
            <span>Risk triage agent</span>
          </div>
        </div>

        <nav className="primary-nav" aria-label="Primary navigation">
          <p>Workspace</p>
          <button className="nav-item nav-item-active" type="button">
            <QueueIcon size={18} />
            Submission queue
            <span>{items.length || "—"}</span>
          </button>
          <button className="nav-item" type="button">
            <ActivityIcon size={18} />
            Run activity
          </button>
          <p>System</p>
          <button className="nav-item" type="button">
            <SettingsIcon size={18} />
            Configuration
          </button>
        </nav>

        <div className="sidebar-source">
          <div className="source-heading">
            <span className={`connection-dot ${error ? "connection-error" : ""}`} />
            Federato connection
          </div>
          <strong>{mode === "live" ? "Live challenge API" : "Demo dataset"}</strong>
          <span>
            {mode === "live"
              ? "OAuth connected"
              : "Add credentials for live mode"}
          </span>
          {appetite ? (
            <span>Appetite v{appetite.version} · {appetite.requirement_count} requirements</span>
          ) : null}
          <span>
            {agentConfigured ? "OpenAI agent configured" : "OpenAI agent required"}
          </span>
        </div>

        <div className="sidebar-foot">
          <span className="avatar">HK</span>
          <div>
            <strong>Hack the North</strong>
            <span>Federato challenge</span>
          </div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <span className="mobile-logo">
              <ShieldIcon size={19} />
            </span>
            <p>Commercial property</p>
            <h1>Submission intelligence</h1>
          </div>
          <div className="topbar-actions">
            <Badge variant="outline" className={`agent-pill agent-${run?.agent_mode ?? (agentConfigured ? "ready" : "required")}`}>
              <SparkIcon size={13} />
              {run?.agent_mode === "openai"
                ? `OpenAI · ${run.agent_model || "agent"}`
                : agentConfigured
                  ? "OpenAI ready"
                  : "OpenAI required"}
            </Badge>
            <Badge variant="outline" className={`mode-pill mode-${mode}`}>
              <span />
              {mode === "live" ? "Federato live" : "Demo mode"}
            </Badge>
            <Button
              className="analyze-button"
              disabled={analyzing || loadingQueue || !queue.length}
              onClick={() => void handleAnalyze()}
              type="button"
            >
              <SparkIcon size={17} />
              {analyzing ? "Agent analyzing…" : run ? "Run agent again" : "Run underwriting agent"}
            </Button>
          </div>
        </header>

        <section className="content-wrap">
          <div className="overview-row">
            <div className="overview-copy">
              <span className="eyebrow">Today&apos;s intake</span>
              <h2>Focus attention where appetite is strongest.</h2>
              <p>
                UnderwriteIQ checks each submission against your 2025 property
                guidelines, then shows the evidence behind every recommendation.
              </p>
            </div>
            <div className="overview-metric">
              <div>
                <span>Queue coverage</span>
                <strong>{run ? "100%" : "0%"}</strong>
              </div>
              <div className="coverage-track">
                <span style={{ width: run ? "100%" : "0%" }} />
              </div>
              <small>
                {run
                  ? `${run.assessments.length} of ${queue.length} analyzed`
                  : `${queue.length || 0} submissions waiting`}
              </small>
            </div>
          </div>

          {error ? (
            <div className="error-banner" role="alert">
              <AlertIcon size={18} />
              <div>
                <strong>Could not complete that request</strong>
                <span>{error}</span>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setError(null)} type="button" aria-label="Dismiss error">
                <CloseIcon size={17} />
              </Button>
            </div>
          ) : null}

          <section className="stat-grid" aria-label="Queue summary">
            <Card className="stat-card stat-target">
              <div className="stat-icon"><SparkIcon size={18} /></div>
              <div><span>Target</span><strong>{run ? counts.target : "—"}</strong><small>Strongest appetite fit</small></div>
            </Card>
            <Card className="stat-card stat-acceptable">
              <div className="stat-icon"><CheckIcon size={18} /></div>
              <div><span>Acceptable</span><strong>{run ? counts.acceptable : "—"}</strong><small>Requirements passed</small></div>
            </Card>
            <Card className="stat-card stat-review">
              <div className="stat-icon"><ClockIcon size={18} /></div>
              <div><span>Needs review</span><strong>{run ? counts.needs_review : "—"}</strong><small>Evidence unresolved</small></div>
            </Card>
            <Card className="stat-card stat-out">
              <div className="stat-icon"><AlertIcon size={18} /></div>
              <div><span>Out of appetite</span><strong>{run ? counts.out_of_appetite : "—"}</strong><small>Hard requirement failed</small></div>
            </Card>
          </section>

          <Card className="queue-card">
            <div className="queue-toolbar">
              <div>
                <div className="section-title-line">
                  <h3>Ranked submission queue</h3>
                  {run ? <span>Run {run.run_id.slice(-6)}</span> : null}
                </div>
                <p>
                  {run
                    ? `${run.agent_summary || "Analysis completed."} Ordered using appetite ${run.appetite_version}.`
                    : "Analyze the queue to calculate appetite fit and ranking."}
                </p>
              </div>
              <label className="search-box">
                <SearchIcon size={17} />
                <Input
                  aria-label="Search submissions"
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Search account or ID"
                  type="search"
                  value={search}
                />
              </label>
            </div>

            <div className="filter-row" aria-label="Filter submissions">
              {FILTERS.map((item) => (
                <Button
                  variant="ghost"
                  size="sm"
                  className={filter === item ? "filter-active" : ""}
                  key={item}
                  onClick={() => {
                    setFilter(item);
                    setPage(1);
                  }}
                  type="button"
                >
                  {item === "all" ? "All submissions" : STATUS_META[item].label}
                  {run && item !== "all" ? <span>{counts[item]}</span> : null}
                </Button>
              ))}
            </div>

            <div className="table-wrap">
              <table className="queue-table">
                <thead>
                  <tr>
                    <th>Rank</th><th>Account</th><th>Status</th><th>Target match</th><th>Premium</th><th>TIV</th><th>State</th><th>Evidence</th><th aria-label="Open details" />
                  </tr>
                </thead>
                <tbody>
                  {loadingQueue
                    ? Array.from({ length: 6 }, (_, index) => (
                        <tr className="skeleton-row" key={index}>
                          <td><span /></td><td><span /></td><td><span /></td><td><span /></td><td><span /></td><td><span /></td><td><span /></td><td><span /></td><td />
                        </tr>
                      ))
                    : visibleItems.map((item) => {
                        const assessment = isAssessment(item) ? item : null;
                        const rank = run
                          ? run.assessments.findIndex(
                              (candidate) => candidate.submission_id === item.submission_id,
                            ) + 1
                          : null;
                        return (
                          <tr
                            className={assessment ? "clickable-row" : ""}
                            key={item.submission_id}
                            onClick={() => openDetail(item)}
                          >
                            <td><span className="rank-number">{rank || "—"}</span></td>
                            <td>
                              <div className="account-cell">
                                <span className="account-monogram">
                                  {item.insured_name.split(" ").slice(0, 2).map((part) => part[0]).join("")}
                                </span>
                                <div><strong>{item.insured_name}</strong><span>{item.submission_number}</span></div>
                              </div>
                            </td>
                            <td><StatusBadge status={itemStatus(item)} /></td>
                            <td>
                              <strong className="score-value">{assessment ? assessment.target_matches : "—"}</strong>
                              {assessment ? <span className="score-max">/{assessment.target_preferences_total}</span> : null}
                            </td>
                            <td>{formatMoney(item.premium)}</td>
                            <td>{formatMoney(item.tiv, true)}</td>
                            <td><span className="state-chip">{item.primary_state || "—"}</span></td>
                            <td>
                              {assessment ? (
                                <div className="evidence-meter">
                                  <span><i style={{ width: `${assessment.evidence_completeness * 100}%` }} /></span>
                                  <small>{Math.round(assessment.evidence_completeness * 100)}%</small>
                                </div>
                              ) : "—"}
                            </td>
                            <td>{assessment ? <ChevronIcon className="row-chevron" size={17} /> : null}</td>
                          </tr>
                        );
                      })}
                </tbody>
              </table>

              {!loadingQueue && !filteredItems.length ? (
                <div className="empty-state">
                  <SearchIcon size={22} /><strong>No submissions match</strong><span>Try changing the filter or search term.</span>
                </div>
              ) : null}
            </div>

            <div className="queue-footer">
              <span>
                {filteredItems.length
                  ? `Showing ${pageStart + 1}–${Math.min(pageStart + PAGE_SIZE, filteredItems.length)} of ${filteredItems.length} submissions`
                  : `Showing 0 of ${items.length} submissions`}
              </span>
              <div className="pagination-controls" aria-label="Queue pagination">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={safePage === 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  Previous
                </Button>
                <span>Page {safePage} of {totalPages}</span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={safePage === totalPages}
                  onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                >
                  Next
                </Button>
              </div>
              <span>
                {run
                  ? `Last analyzed ${new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(new Date(run.created_at))}`
                  : "Waiting for analysis"}
              </span>
            </div>
          </Card>

          <p className="disclaimer">
            UnderwriteIQ prioritizes submissions for human review. It does not approve,
            price, quote, or bind coverage.
          </p>
        </section>
      </main>

      {selected ? (
        <div className="drawer-layer" role="presentation">
          <button aria-label="Close submission details" className="drawer-scrim" onClick={() => setSelected(null)} type="button" />
          <aside aria-label="Submission details" className="detail-drawer">
            <div className="drawer-header">
              <div className="drawer-title-row">
                <div><span className="eyebrow">{selected.submission_number}</span><h2>{selected.insured_name}</h2></div>
                <Button variant="outline" size="icon" aria-label="Close details" className="icon-button" onClick={() => setSelected(null)} type="button"><CloseIcon size={19} /></Button>
              </div>
              <div className="drawer-summary">
                <StatusBadge status={selected.status} />
                <span>{selected.target_matches}/{selected.target_preferences_total} target preferences</span>
                <span>{Math.round(selected.evidence_completeness * 100)}% evidence</span>
                <span>Appetite v{selected.appetite_version}</span>
              </div>
            </div>

            <Tabs
              className="drawer-tabs-shell"
              onValueChange={(value) => setDetailTab(value as DetailTab)}
              value={detailTab}
            >
            <TabsList className="drawer-tabs">
              {(["decision", "evidence", "trace"] as DetailTab[]).map((tab) => (
                <TabsTrigger
                  className={detailTab === tab ? "drawer-tab-active" : ""}
                  key={tab}
                  value={tab}
                >
                  {tab === "decision" ? "Decision" : tab === "evidence" ? "Evidence" : "Agent activity"}
                </TabsTrigger>
              ))}
            </TabsList>

            <div className="drawer-body">
              {detailTab === "decision" ? (
                <>
                  <section className={`recommendation-card recommendation-${STATUS_META[selected.status].tone}`}>
                    <span>Recommended action</span><strong>{selected.recommended_action}</strong><p>{selected.explanation}</p>
                    <small className="explanation-source">
                      {selected.explanation_source === "openai"
                        ? "Explanation written by OpenAI from verified evidence"
                        : "Rule-engine explanation"}
                    </small>
                  </section>
                  <section className="fact-grid">
                    <div><span>Premium</span><strong>{formatMoney(selected.premium)}</strong></div>
                    <div><span>Total insured value</span><strong>{formatMoney(selected.tiv, true)}</strong></div>
                    <div><span>Primary state</span><strong>{selected.primary_state || "—"}</strong></div>
                    <div><span>Received</span><strong>{formatDate(selected.received_date)}</strong></div>
                  </section>
                  {selected.failed_requirements.length ? (
                    <section className="drawer-section">
                      <div className="drawer-section-title"><h3>Failed requirements</h3><span>{selected.failed_requirements.length}</span></div>
                      <RuleList rules={selected.failed_requirements} empty="No hard requirements failed." />
                    </section>
                  ) : null}
                  {selected.unresolved_rules.length ? (
                    <section className="drawer-section">
                      <div className="drawer-section-title"><h3>Requires attention</h3><span>{selected.unresolved_rules.length}</span></div>
                      <RuleList rules={selected.unresolved_rules} empty="No unresolved rules." />
                    </section>
                  ) : null}
                  {selected.warnings.length || selected.missing_information.length ? (
                    <section className="drawer-section attention-panel">
                      <div className="drawer-section-title">
                        <h3>Contradictions &amp; follow-up</h3>
                        <span>{selected.warnings.length + selected.missing_information.length}</span>
                      </div>
                      <ul>
                        {selected.warnings.map((item) => (
                          <li key={`warning-${item}`}><strong>Contradiction</strong><span>{item}</span></li>
                        ))}
                        {selected.missing_information.map((item) => (
                          <li key={`missing-${item}`}><strong>Request</strong><span>{item}</span></li>
                        ))}
                      </ul>
                    </section>
                  ) : null}
                  <section className="drawer-section">
                    <div className="drawer-section-title"><h3>Target preferences</h3><span>{selected.matched_preferences.length}/{selected.target_preferences_total} matched</span></div>
                    <RuleList rules={selected.matched_preferences} empty="No target preferences matched." />
                  </section>
                  <section className="drawer-section">
                    <div className="drawer-section-title"><h3>COPE underwriting evidence</h3><span>Research factors</span></div>
                    <CopeGrid items={selected.cope} />
                  </section>
                  <section className="drawer-section">
                    <div className="drawer-section-title"><h3>Requirements passed</h3><span>{selected.passed_requirements.length}</span></div>
                    <RuleList rules={selected.passed_requirements} empty="No requirements recorded." />
                  </section>
                </>
              ) : null}
              {detailTab === "evidence" ? (
                <section className="drawer-section drawer-section-flush">
                  <div className="evidence-intro"><DatabaseIcon size={19} /><div><strong>Verified source evidence</strong><span>Values used by the deterministic rule evaluator.</span></div></div>
                  <div className="evidence-table-wrap">
                    <table className="evidence-table">
                      <thead><tr><th>Source</th><th>Field</th><th>Value</th></tr></thead>
                      <tbody>
                        {selected.evidence.map((item) => (
                          <tr key={`${item.resource}-${item.record_id}-${item.field}`}>
                            <td><strong>{item.resource}</strong><span>{item.record_id}</span></td>
                            <td>{item.label}<span>{item.field}</span></td>
                            <td>{formatEvidenceValue(item)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : null}
              {detailTab === "trace" ? (
                <section className="drawer-section drawer-section-flush">
                  <div className="evidence-intro"><ActivityIcon size={19} /><div><strong>Auditable activity trace</strong><span>Tool actions and evidence summaries, without private chain-of-thought.</span></div></div>
                  {run?.agent_adaptations.length ? (
                    <div className="adaptation-panel">
                      <strong>How the agent adapted</strong>
                      <ul>{run.agent_adaptations.map((item) => <li key={item}>{item}</li>)}</ul>
                    </div>
                  ) : null}
                  <TraceList events={selectedTrace} />
                </section>
              ) : null}
            </div>
            </Tabs>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
