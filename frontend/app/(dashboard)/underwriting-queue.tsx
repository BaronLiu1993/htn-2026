"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import { Dialog } from "@base-ui/react/dialog";
import { Popover } from "@base-ui/react/popover";
import {
  Activity, AlertTriangle, BookOpen, Check, ChevronDown, CircleX,
  Database, ListFilter, LoaderCircle, Play, RefreshCw, Search, Target, X,
} from "lucide-react";
import { type CSSProperties, useCallback, useEffect, useMemo, useState } from "react";
import {
  AnalysisRunError,
  analyzeSubmissions,
  fetchGuideline,
  fetchGuidelines,
  fetchHealth,
  fetchSubmissions,
} from "../../lib/api";
import type {
  AnalysisRun, Assessment, AssessmentStatus, GuidelinePackage,
  GuidelineRule, GuidelineSummary, FailedRunDetail, HealthResponse, ModelProvider,
  QueueSubmission, TraceEvent,
} from "../../lib/types";
import {
  type DisplayEvidence, type Tick, allEvidence, copeEvidence, evidenceFor, evidenceStateLabel,
  limitPhrase, money, position, ruleEvidence, ruleSummary, shortValue, verdictLabel,
} from "../../lib/evidence";
import {
  type AccountGroup, fileRole, groupAccounts, matchesAccount, roleLabel,
} from "../../lib/account-groups";
import "./underwriting-queue.css";

type View = "queue" | "activity" | "guidelines";
type Group = "Pursue" | "Investigate" | "Out of appetite";
type StatusFilter = AssessmentStatus | null;
type LoadedData = {
  health: HealthResponse;
  submissions: QueueSubmission[];
  guidelines: GuidelineSummary[];
  packages: GuidelinePackage[];
  guideline: GuidelineSummary;
  guidelinePackage: GuidelinePackage;
  run: AnalysisRun;
};
const groups: Group[] = ["Pursue", "Investigate", "Out of appetite"];
const cards = [
  { status: "target", label: "Target", tone: "target", icon: Target },
  { status: "acceptable", label: "Acceptable", tone: "acceptable", icon: Check },
  { status: "needs_review", label: "Needs review", tone: "amber", icon: AlertTriangle },
  { status: "out_of_appetite", label: "Out of appetite", tone: "red", icon: CircleX },
] as const;
const labels: Record<AssessmentStatus, string> = {
  target: "Target", acceptable: "Acceptable", needs_review: "Needs review", out_of_appetite: "Out of appetite",
};

function groupFor(status: AssessmentStatus): Group {
  if (status === "target" || status === "acceptable") return "Pursue";
  return status === "needs_review" ? "Investigate" : "Out of appetite";
}

function dateText(value?: string | null, withTime = false): string {
  if (!value) return "Not available";
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
  }).format(date);
}

function Badge({ assessment }: { assessment: Assessment }) {
  const tone = assessment.status === "target" ? "green" : assessment.status === "acceptable" ? "acceptable" : assessment.status === "needs_review" ? "amber" : "red";
  return <span className={`uw-badge ${tone}`}>{labels[assessment.status]}</span>;
}

/** Value plotted against the rule's real threshold, with every other rule on the same axis. */
function EvidenceScale({ evidence }: { evidence: DisplayEvidence }) {
  const { domain, band, ticks, numeric } = evidence;
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setSettled(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  if (!domain) return null;
  const at = numeric === null ? 0 : position(domain, numeric);
  const room = headroomText(evidence);
  return (
    <div className="uw-ev-scale" data-unknown={numeric === null ? "" : undefined} aria-hidden="true">
      <div className="uw-ev-track">
        {band && <span className="uw-ev-band" style={{ left: `${band.left * 100}%`, width: `${band.width * 100}%` }} />}
        {ticks.map((tick) => <span key={tick.key} className={`uw-ev-tick ${tick.kind}`} style={{ left: `${tick.at * 100}%` }} />)}
        {numeric !== null && <span className="uw-ev-marker" style={{ left: `${(settled ? at : 0) * 100}%` }} />}
      </div>
      {ticks.length > 0 && (
        <div className="uw-ev-axis">
          {spacedTicks(ticks).map((tick) => (
            <span key={tick.key} className="uw-ev-axis-label" style={edgeSafe(tick.at)}>{tick.label}</span>
          ))}
        </div>
      )}
      {room && <p className="uw-ev-meta">{room}</p>}
    </div>
  );
}

/** Axis labels are centred on their tick, so drop any that would collide with the last kept one. */
function spacedTicks(ticks: Tick[]): Tick[] {
  const kept: Tick[] = [];
  for (const tick of ticks) {
    if (kept.length === 0 || tick.at - kept[kept.length - 1].at >= 0.16) kept.push(tick);
  }
  return kept;
}

function edgeSafe(at: number): CSSProperties {
  if (at <= 0.06) return { left: 0 };
  if (at >= 0.94) return { right: 0 };
  return { left: `${at * 100}%`, transform: "translateX(-50%)" };
}

/** A gap is a distance, not a value: percentage points and year counts, never $0.4 or 1,990. */
function gapText(diff: number, display: DisplayEvidence["display"]): string {
  if (display === "percent") return `${Math.round(diff * 100)} pts`;
  if (display === "year") return `${Math.round(diff)} yr`;
  return shortValue(diff, display);
}

/**
 * How much room is left, in the fact's own units. A share of a year or of a floor is
 * meaningless, so only a passing cap gets the normalised "headroom" reading.
 */
function headroomText(evidence: DisplayEvidence): string {
  const { numeric, threshold, operator, display, verdict } = evidence;
  if (numeric === null || threshold === null) return "";
  if (Array.isArray(threshold)) {
    const [low, high] = threshold;
    if (numeric < low) return `${gapText(low - numeric, display)} below the range`;
    if (numeric > high) return `${gapText(numeric - high, display)} above the range`;
    return `${gapText(Math.min(numeric - low, high - numeric), display)} inside the range`;
  }
  const gap = Math.abs(numeric - threshold);
  if (gap === 0) return "exactly at the limit";
  if (display === "year") {
    return verdict === "pass"
      ? `${gapText(gap, display)} newer than the cutoff`
      : `${gapText(gap, display)} older than the cutoff`;
  }
  if (operator === "lt" || operator === "lte") {
    return verdict === "pass" && threshold !== 0
      ? `${Math.round((1 - numeric / threshold) * 100)}% headroom`
      : `${gapText(gap, display)} over the cap`;
  }
  return verdict === "pass"
    ? `${gapText(gap, display)} above the floor`
    : `${gapText(gap, display)} short of the floor`;
}

/**
 * A string fact like "CA" carries no numeric value, so numeric-ness cannot stand in for
 * "unconfirmed" — only the rule's own state can say that.
 */
function EvidenceLine({ evidence }: { evidence: DisplayEvidence }) {
  const { numeric, display, threshold, operator, value, ruleName, expected, shape } = evidence;
  if (shape === "context") return <strong>{value}</strong>;
  if (evidence.ruleState === "unresolved") {
    return <>{value === "Not available" ? "Not found in source data" : value}, so <strong>{ruleName}</strong> is unconfirmed</>;
  }
  if (numeric !== null && threshold !== null) {
    return <><strong>{shortValue(numeric, display)}</strong> against {limitPhrase(threshold, operator, display)}</>;
  }
  return <><strong>{value}</strong> {evidence.verdict === "pass" ? "is within" : "is outside"} {expected}</>;
}

function EvidenceCard({ evidence, number, demo }: { evidence: DisplayEvidence; number: number; demo: boolean }) {
  const { verdict, ruleState } = evidence;
  const Icon = verdict === "pass" ? Check : verdict === "fail" ? CircleX : AlertTriangle;
  return (
    <Popover.Root>
      <Popover.Trigger className="uw-evidence-marker" aria-label={`Evidence ${number}: ${evidence.preview}`} openOnHover delay={140} closeDelay={160}>
        {number}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8} className="uw-evidence-positioner">
          <Popover.Popup className={`uw-evidence-popup uw-ev ${verdict}`}>
            <Popover.Arrow className="uw-evidence-arrow" />
            <Popover.Title className="uw-sr-only">{evidence.claim}</Popover.Title>

            {ruleState ? (
              <p className="uw-ev-head"><Icon size={13} aria-hidden="true" />{verdictLabel[ruleState]}</p>
            ) : (
              <p className="uw-ev-head context">{evidence.claim}</p>
            )}

            <Popover.Description className="uw-ev-line">
              <EvidenceLine evidence={evidence} />
            </Popover.Description>

            <EvidenceScale evidence={evidence} />

            <p className="uw-ev-source">
              {evidence.derivation ?? evidence.record}
              {evidence.sourceDate && <> · as of {dateText(evidence.sourceDate)}</>}
              {evidence.evidenceState !== "verified" && <> · {evidenceStateLabel[evidence.evidenceState].toLowerCase()}</>}
            </p>

            {evidence.others.map((other) => (
              <p key={other.id} className="uw-ev-also">{other.name}<span className={`uw-ev-state ${other.state}`}>{other.label}</span></p>
            ))}

            {demo && <p className="uw-fixture-label">Fictional demo record</p>}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

function Markers({ items, index, demo }: { items: DisplayEvidence[]; index: DisplayEvidence[]; demo: boolean }) {
  return items.flatMap((item) => {
    const number = index.findIndex((entry) => entry.id === item.id) + 1;
    if (number < 1) return [];
    return [<EvidenceCard key={item.id} evidence={item} number={number} demo={demo} />];
  });
}

function Bundle({ items, label, demo }: { items: DisplayEvidence[]; label: string; demo: boolean }) {
  if (!items.length) return <span className="uw-unavailable">Not available</span>;
  return (
    <Popover.Root>
      <Popover.Trigger className="uw-evidence-bundle" aria-label={`${label}. Returned evidence records.`} openOnHover delay={140} closeDelay={180}>{label}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8} className="uw-evidence-positioner uw-bundle-positioner">
          <Popover.Popup className="uw-evidence-popup uw-bundle-popup">
            <Popover.Arrow className="uw-evidence-arrow" />
            <div className="uw-popover-head"><Popover.Title>{label}</Popover.Title><Popover.Close aria-label="Close evidence"><X size={14} /></Popover.Close></div>
            <Popover.Description>Source records and applicable rules returned by the analysis.</Popover.Description>
            <div className="uw-bundle-list">{items.map((item) => (
              <section key={item.id}>
                <div><strong>{item.claim}</strong><span>{item.value}</span></div>
                <dl><div><dt>Source</dt><dd>{item.record}</dd></div><div><dt>Field</dt><dd>{item.field}</dd></div><div><dt>Rule</dt><dd>{item.ruleName}</dd></div><div><dt>Expected</dt><dd>{item.expected}</dd></div>{item.note && <div><dt>Note</dt><dd>{item.note}</dd></div>}</dl>
              </section>
            ))}</div>
            {demo && <p className="uw-fixture-label">Fictional demo records</p>}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}

function Facts({ assessment, evidence, markerIndex, demo }: { assessment: Assessment; evidence: DisplayEvidence[]; markerIndex: DisplayEvidence[]; demo: boolean }) {
  const facts = [
    { label: "Risk state", value: assessment.primary_state ?? "Not available", terms: ["state"] },
    { label: "Insured value", value: money(assessment.tiv), terms: ["tiv", "insured_value", "insured value"] },
    { label: "Premium", value: money(assessment.premium), terms: ["premium"] },
    { label: "Received", value: dateText(assessment.received_date), terms: ["received"] },
  ];
  return <div className="uw-review-facts">{facts.map((fact) => {
    const records = evidenceFor(evidence, fact.terms).slice(0, 1);
    return <div key={fact.label}><span>{fact.label}</span><strong>{fact.value}{records.length > 0 && <span className="uw-marker-group"><Markers items={records} index={markerIndex} demo={demo} /></span>}</strong></div>;
  })}</div>;
}

function activityMeta(event: TraceEvent): string[] {
  const items: string[] = [];
  if (event.page_count && event.page_count > 1) items.push(`The search used ${event.page_count} pages`);
  if (event.duration_ms >= 100) items.push(`${(event.duration_ms / 1000).toFixed(1)} sec`);
  return items;
}

function ActivityRail({ events, emptyLabel }: { events: TraceEvent[]; emptyLabel: string }) {
  if (!events.length) return <p className="uw-empty-copy">{emptyLabel}</p>;
  return <ol className="uw-trace-list">{events.map((event) => {
    const meta = activityMeta(event);
    const hasDetails = meta.length > 0 || Boolean(event.error);
    return (
      <li key={event.id}>
        <span className={`uw-trace-status ${event.status}`} aria-hidden="true" />
        <div className="uw-trace-body">
          <p>{event.purpose}</p>
          <strong>{event.result_summary}</strong>
          {hasDetails && (
            <Collapsible.Root>
              <Collapsible.Trigger className="uw-trace-trigger" nativeButton>
                <span>{event.status === "failure" ? "Failed" : "Details"}<ChevronDown size={12} /></span>
              </Collapsible.Trigger>
              <Collapsible.Panel className="uw-trace-panel">
                <div className="uw-trace-details">
                  {meta.length > 0 && <p className="uw-trace-meta">{meta.join(" · ")}</p>}
                  {event.error && <p className="uw-trace-error">{event.error}</p>}
                </div>
              </Collapsible.Panel>
            </Collapsible.Root>
          )}
        </div>
      </li>
    );
  })}</ol>;
}

function Review({ assessment, run, pkg }: { assessment: Assessment; run: AnalysisRun; pkg: GuidelinePackage | null }) {
  const evidence = useMemo(() => allEvidence(assessment, pkg), [assessment, pkg]);
  const ctx = useMemo(() => ({ pkg, assessment }), [pkg, assessment]);
  const [actionOpen, setActionOpen] = useState(false);
  const unresolved = assessment.status === "needs_review";
  const excluded = assessment.status === "out_of_appetite";
  const preferences = assessment.matched_preferences.flatMap((rule) => ruleEvidence(rule, ctx));
  // Rule names read as satisfied conditions, so a failure needs found-vs-needed spelled out.
  const checks = (unresolved ? assessment.unresolved_rules : excluded ? assessment.failed_requirements : [])
    .map((rule) => ruleSummary(rule, ctx));
  const markerIndex = [
    ...evidenceFor(evidence, ["state"]).slice(0, 1),
    ...evidenceFor(evidence, ["tiv", "insured_value", "insured value"]).slice(0, 1),
    ...evidenceFor(evidence, ["premium"]).slice(0, 1),
    ...preferences,
  ].filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index);
  const trace = run.activity.filter((event) => event.submission_id === assessment.submission_id || !event.submission_id);
  const issues = [...assessment.missing_information, ...assessment.warnings];

  return <div className="uw-review">
    <div className="uw-review-identity"><div><div className="uw-section-kicker">Submission {assessment.submission_number}</div><Dialog.Title>{assessment.insured_name}</Dialog.Title><p>{run.guideline_name ?? "Selected guideline"} · {assessment.primary_state ?? "State not available"} · USD</p></div><Badge assessment={assessment} /></div>
    <div className="uw-next-action"><span>Recommended action</span><strong>{assessment.recommended_action}</strong></div>
    <section className="uw-assessment-summary">
      <div className="uw-section-kicker">Assessment</div>
      <h3>{unresolved ? "Eligibility is not established." : excluded ? "This account is outside appetite." : assessment.status === "target" ? "Eligible with a strong target fit." : "Eligible with an acceptable target fit."}</h3>
      <p>{assessment.explanation}</p>
      {checks.length > 0 && <dl className="uw-checks">
        {checks.map((check) => <div key={check.id}><dt>{check.label}</dt><dd><strong>{check.found}</strong><span>needs {check.needs}</span></dd></div>)}
      </dl>}
    </section>
    {(issues.length > 0 || excluded) && <section className={`uw-review-alert ${excluded ? "danger" : "warning"}`}>
      {excluded ? <CircleX size={16} /> : <AlertTriangle size={16} />}<div><strong>{excluded ? "Confirmed exclusion" : "Missing information and warnings"}</strong>{issues.length ? <ul>{issues.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Target preferences cannot override a confirmed appetite exclusion.</p>}</div>
    </section>}
    <section><div className="uw-section-kicker">Supporting facts</div><Facts assessment={assessment} evidence={evidence} markerIndex={markerIndex} demo={run.mode === "demo"} /></section>
    <section className="uw-target-breakdown">
      <div className="uw-section-kicker">Target preferences</div>
      {unresolved || excluded ? <p className="uw-score-withheld">Final fit score withheld. {unresolved ? "Establish eligibility first." : "The account is excluded."}</p> : <p className="uw-fit-summary"><strong>{assessment.target_matches} of {assessment.target_preferences_total}</strong> preferences match</p>}
      <div className="uw-target-list">{assessment.matched_preferences.map((rule) => {
        const records = ruleEvidence(rule, ctx);
        return <div key={rule.rule_id}><span>{rule.name}<span className="uw-marker-group"><Markers items={records} index={markerIndex} demo={run.mode === "demo"} /></span></span><strong>Target preference</strong></div>;
      })}{assessment.matched_preferences.length === 0 && <p className="uw-empty-copy">No matched preference rules were returned.</p>}</div>
      <p className="uw-review-disclaimer">Target fit orders eligible accounts. It is not an approval probability or profit forecast.</p>
    </section>
    <section className="uw-cope uw-disaster-history"><div className="uw-section-kicker">Disaster history</div>
      <p>{assessment.disaster_declaration_count == null ? "FEMA data is unavailable. Ranking is unchanged." : `${assessment.primary_state}: ${assessment.disaster_declaration_count} disaster declarations since ${dateText(assessment.disaster_context_since)}.`}</p>
      {assessment.disaster_declaration_count != null && <p>{assessment.enrichment_rank_change ? `Moved ${assessment.enrichment_rank_change > 0 ? "up" : "down"} ${Math.abs(assessment.enrichment_rank_change)} ${Math.abs(assessment.enrichment_rank_change) === 1 ? "place" : "places"} in this queue.` : "No change to this account’s rank."} Appetite status is unchanged.</p>}
      <a href="https://www.fema.gov/openfema-data-page/disaster-declarations-summaries-v2" target="_blank" rel="noreferrer">FEMA data</a>
    </section>
    <section className="uw-cope"><div className="uw-section-kicker">Underwriting considerations</div>{assessment.cope.map((item) => {
      const records = copeEvidence(item, ctx);
      return <div key={item.category}><span>{item.category}</span><p>{item.summary}</p>{records.length ? <Bundle items={records} label={`${records.length} ${records.length === 1 ? "source" : "sources"}`} demo={run.mode === "demo"} /> : <small>Not available</small>}</div>;
    })}</section>
    <details className="uw-agent-activity"><summary><span><Activity size={15} />Agent activity</span><span>{trace.length} review updates <ChevronDown size={14} /></span></summary><div className="uw-activity-body">{run.agent_summary && <p>{run.agent_summary}</p>}<ActivityRail events={trace} emptyLabel="No trace events were returned for this account." /></div></details>
    <div className="uw-review-action"><button className="uw-primary" onClick={() => setActionOpen((open) => !open)}>{actionOpen ? "Hide review checklist" : assessment.recommended_action}<span>↗</span></button>{actionOpen && <div role="status" className="uw-action-result"><strong>Review checklist</strong><p>{assessment.recommended_action}.</p><ul>{(assessment.failed_requirements.length ? assessment.failed_requirements.map((rule) => `${rule.name}: ${rule.expected}`) : assessment.missing_information.length ? assessment.missing_information : ["Confirm the cited Policy and Claim records before underwriting review."]).map((item) => <li key={item}>{item}</li>)}</ul></div>}</div>
  </div>;
}

function AccountReview({ assessment, run, pkg }: { assessment: Assessment; run: AnalysisRun; pkg: GuidelinePackage | null }) {
  const label = assessment.status === "needs_review" ? "Resolve gap" : assessment.status === "out_of_appetite" ? "View exclusion" : "Review account";
  return <Dialog.Root><Dialog.Trigger className="uw-text-button">{label} <span>↗</span></Dialog.Trigger><Dialog.Portal><Dialog.Backdrop className="uw-backdrop" /><Dialog.Popup className="uw-drawer"><Dialog.Close className="uw-close" aria-label="Close review"><X size={18} /></Dialog.Close><Review assessment={assessment} run={run} pkg={pkg} /></Dialog.Popup></Dialog.Portal></Dialog.Root>;
}

function Sidebar({ view, onView, run, hasGuidelines }: { view: View; onView: (view: View) => void; run?: AnalysisRun; hasGuidelines: boolean }) {
  return <aside className="uw-sidebar" aria-label="Underwriting navigation"><nav><button aria-current={view === "queue" ? "page" : undefined} onClick={() => onView("queue")}><ListFilter size={16} />Queue</button><button aria-current={view === "activity" ? "page" : undefined} onClick={() => onView("activity")} disabled={!run}><Activity size={16} />Run activity</button></nav><div className="uw-sidebar-secondary"><button aria-current={view === "guidelines" ? "page" : undefined} onClick={() => onView("guidelines")} disabled={!hasGuidelines}><BookOpen size={16} />Guidelines</button><div className="uw-data-state"><span />{run ? run.mode === "demo" ? "Demo data" : "Live data" : "No run yet"}<small>{run ? `${run.assessments.length} assessed submissions` : "Start from the queue"}</small></div></div></aside>;
}

function TargetFit({ assessment }: { assessment: Assessment }) {
  return assessment.status === "target" || assessment.status === "acceptable"
    ? <><strong>{assessment.target_matches} of {assessment.target_preferences_total}</strong><small>target preferences</small></>
    : <span className="uw-muted">Not scored</span>;
}

function AccountBlock({
  account, open, onToggle, run, pkg,
}: {
  account: AccountGroup;
  open: boolean;
  onToggle: () => void;
  run: AnalysisRun;
  pkg: GuidelinePackage | null;
}) {
  const current = account.current;
  const expandable = account.files.length > 1;
  const showFiles = expandable && open;
  return <>
    <tr className={expandable ? "uw-parent" : undefined} data-open={showFiles ? "" : undefined}>
      <td>
        {expandable ? (
          <button type="button" className="uw-expand" onClick={onToggle} aria-expanded={open}>
            <ChevronDown size={14} />
            <span>
              <strong>{current.insured_name}</strong>
              <small>{account.files.length} submissions · {current.primary_state ?? "State unavailable"} · {money(current.tiv)} TIV</small>
            </span>
          </button>
        ) : (
          <>
            <strong>{current.insured_name}</strong>
            <small>{current.submission_number} · {current.primary_state ?? "State unavailable"} · {money(current.tiv)} TIV</small>
          </>
        )}
        {!showFiles && <p>{current.explanation}</p>}
      </td>
      <td><Badge assessment={current} /></td>
      <td><TargetFit assessment={current} /></td>
      <td>{money(current.premium)}</td>
      <td>{dateText(current.received_date)}</td>
      <td><AccountReview assessment={current} run={run} pkg={pkg} /></td>
    </tr>
    {showFiles && account.files.map((file) => (
      <tr key={file.submission_id} className="uw-child">
        <td>
          <span className="uw-role">{roleLabel(fileRole(file, current))}</span>
          <strong>{file.submission_number}</strong>
          <p>{file.explanation}</p>
        </td>
        <td><Badge assessment={file} /></td>
        <td><TargetFit assessment={file} /></td>
        <td>{money(file.premium)}</td>
        <td>{dateText(file.received_date)}</td>
        <td><AccountReview assessment={file} run={run} pkg={pkg} /></td>
      </tr>
    ))}
  </>;
}

function Queue({ data, selectedModel, onRerun, onGuidelineChange, onModelChange, rerunning }: { data: LoadedData; selectedModel: ModelProvider; onRerun: () => void; onGuidelineChange: (id: string) => void; onModelChange: (provider: ModelProvider) => void; rerunning: boolean }) {
  const { run, guideline, guidelines } = data;
  const [group, setGroup] = useState<Group>(() => groups.find((item) => data.run.assessments.some((assessment) => groupFor(assessment.status) === item)) ?? "Pursue");
  const [status, setStatus] = useState<StatusFilter>(null);
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState("");
  const accounts = useMemo(() => groupAccounts(run.assessments), [run.assessments]);
  const rows = useMemo(() => accounts.filter((account) =>
    groupFor(account.current.status) === group && (!status || account.current.status === status) &&
    matchesAccount(account, search),
  ), [accounts, group, search, status]);
  const eligible = accounts.filter((account) => account.current.status === "target" || account.current.status === "acceptable").length;
  const unresolved = accounts.filter((account) => account.current.status === "needs_review").length;
  const excluded = accounts.filter((account) => account.current.status === "out_of_appetite").length;

  function selectStatus(next: AssessmentStatus) {
    if (status === next) return setStatus(null);
    setGroup(groupFor(next));
    setStatus(next);
  }

  return <main className="uw-queue">
    <div className="uw-heading"><div><h1>Queue</h1><p>{accounts.length} relevant accounts · {run.assessments.length} submissions · {eligible} eligible · {unresolved} need review · {excluded} outside appetite</p></div><div className="uw-heading-actions"><label className="uw-model-switch" title={data.health.baseten_error ?? undefined}><span>Model</span><select value={selectedModel} onChange={(event) => onModelChange(event.target.value as ModelProvider)} disabled={rerunning}><option value="openai" disabled={!data.health.openai_configured}>OpenAI · evidence agent</option><option value="baseten" disabled={!data.health.baseten_configured}>UnderwriteIQ · Qwen3-8B{data.health.baseten_configured ? "" : " (unavailable)"}</option></select></label><label className="uw-guideline-switch"><span className="uw-sr-only">Selected guideline</span><select value={guideline.id} onChange={(event) => onGuidelineChange(event.target.value)} disabled={rerunning}>{guidelines.map((item) => <option key={`${item.id}-${item.version}`} value={item.id}>{item.name} · {item.version}</option>)}</select></label><button className="uw-rerun" onClick={onRerun} disabled={rerunning}>{rerunning ? <LoaderCircle size={13} className="uw-spin" /> : <RefreshCw size={13} />}{rerunning ? "Running" : "Rerun"}</button></div></div>
    {run.scope_unknown_submissions > 0 && <div className="uw-inline-banner warning" role="status"><AlertTriangle size={16} /><div><strong>{run.scope_unknown_submissions} {run.scope_unknown_submissions === 1 ? "submission has" : "submissions have"} unknown guideline scope</strong><p>These submissions are not assessed until their line of business is confirmed.</p></div></div>}
    {run.status === "partial" && <div className="uw-inline-banner warning" role="status"><AlertTriangle size={16} /><div><strong>Partial analysis run</strong><p>{run.errors.join(" · ") || "Some submissions could not be assessed."}</p></div></div>}
    <section className="uw-status-cards" aria-label="Filter queue by appetite status">{cards.map((card) => {
      const Icon = card.icon;
      const count = accounts.filter((account) => account.current.status === card.status).length;
      return <button key={card.status} className={card.tone} aria-pressed={status === card.status} onClick={() => selectStatus(card.status)}><span><Icon size={14} />{card.label}</span><strong>{count}</strong></button>;
    })}</section>
    <div className="uw-toolbar"><div className="uw-filters" aria-label="Action groups">{groups.map((item) => <button key={item} aria-pressed={group === item && status === null} data-current-group={group === item ? "" : undefined} onClick={() => { setGroup(item); setStatus(null); }}>{item}<span>{accounts.filter((account) => groupFor(account.current.status) === item).length}</span></button>)}</div><label className="uw-search"><span className="uw-sr-only">Search accounts</span><Search size={14} /><input placeholder="Search accounts…" value={search} onChange={(event) => setSearch(event.target.value)} /></label></div>
    <div className="uw-table-wrap"><table className="uw-nested"><thead><tr><th>Account / assessment</th><th>Appetite</th><th>Target fit</th><th>Premium</th><th>Received</th><th>Next step</th></tr></thead><tbody>{rows.map((account) => {
      const query = search.trim().toLowerCase();
      const hitPrior = query.length > 0 && account.files.some((file) => file.submission_id !== account.current.submission_id && `${file.submission_number} ${file.insured_name}`.toLowerCase().includes(query));
      return <AccountBlock key={account.key} account={account} open={openId === account.key || hitPrior} onToggle={() => setOpenId((current) => current === account.key ? "" : account.key)} run={run} pkg={data.guidelinePackage} />;
    })}</tbody></table>{!rows.length && <div className="uw-empty"><Database size={18} /><p>No accounts match this filter.</p><button onClick={() => { setSearch(""); setStatus(null); }}>Clear filters</button></div>}</div>
    <div className="uw-footnote"><span>{guideline.name} · effective {dateText(guideline.effective_from)}</span><span>Recommendations require human review.</span></div>
  </main>;
}

function RunActivity({ run }: { run: AnalysisRun }) {
  return <main className="uw-supporting-view"><div className="uw-heading"><div><h1>Run activity</h1><p>Evidence checks and decisions for this queue.</p></div><span className="uw-rubric-state">{run.status}</span></div><div className="uw-run-summary"><div><span>Created</span><strong>{dateText(run.created_at, true)}</strong></div><div><span>Agent</span><strong>{run.agent_model ?? run.agent_mode}</strong></div><div><span>Records checked</span><strong>{run.query_metrics.records_found ?? run.query_count}</strong></div>{run.model_latency_ms > 0 && <div><span>Model time</span><strong>{(run.model_latency_ms / 1000).toFixed(1)} sec</strong></div>}{(run.model_prompt_tokens + run.model_completion_tokens) > 0 && <div><span>Tokens</span><strong>{(run.model_prompt_tokens + run.model_completion_tokens).toLocaleString("en-US")}</strong></div>}{run.model_agreement_rate != null && <div><span>Rule-engine agreement</span><strong>{(run.model_agreement_rate * 100).toFixed(1)}%</strong></div>}</div>{run.agent_summary && <p className="uw-support-note">{run.agent_summary}</p>}{run.agent_adaptations.length > 0 && <ul className="uw-adaptations">{run.agent_adaptations.map((item) => <li key={item}>{item}</li>)}</ul>}<ActivityRail events={run.activity} emptyLabel="This run did not return trace events." />{run.errors.length > 0 && <div className="uw-inline-banner warning"><AlertTriangle size={16} /><div><strong>Run errors</strong><p>{run.errors.join(" · ")}</p></div></div>}</main>;
}

function RuleList({ title, rules }: { title: string; rules: GuidelineRule[] }) {
  return <section className="uw-guideline-section"><h2>{title}</h2><ol>{rules.map((rule) => (
    <li key={rule.id}><strong>{rule.name}</strong><p>{rule.expected}{rule.note ? ` ${rule.note}` : ""}</p></li>
  ))}</ol></section>;
}

function Guidelines({ packages, selectedId, onSelect }: { packages: GuidelinePackage[]; selectedId: string; onSelect: (id: string) => void }) {
  const guideline = packages.find((item) => item.id === selectedId) ?? packages[0];
  if (!guideline) return <main className="uw-supporting-view"><p className="uw-empty-copy">No guideline package is installed.</p></main>;
  return <main className="uw-supporting-view"><div className="uw-heading"><div><h1>Guidelines</h1><p>Scope, eligibility gates, and target preferences for the selected package.</p></div><label className="uw-guideline-switch"><span className="uw-sr-only">Inspect guideline</span><select value={guideline.id} onChange={(event) => onSelect(event.target.value)}>{packages.map((item) => <option key={`${item.id}-${item.version}`} value={item.id}>{item.name}</option>)}</select></label></div><section className="uw-guideline-section"><h2>Scope</h2><p>{guideline.scope.description}. These criteria apply only to matching submissions.</p></section><RuleList title="Eligibility gates" rules={guideline.requirements} /><RuleList title="Target preferences" rules={guideline.preferences} /><p className="uw-support-note">{guideline.source}. Version {guideline.version}, effective {dateText(guideline.effective_from)}. Final coverage decisions remain human-owned.</p></main>;
}

function EmptyQueue({ running, onRun, health, guidelines, model, guidelineId, onModelChange, onGuidelineChange }: {
  running: boolean; onRun: () => void; health: HealthResponse | null;
  guidelines: GuidelinePackage[]; model: ModelProvider; guidelineId: string;
  onModelChange: (provider: ModelProvider) => void; onGuidelineChange: (id: string) => void;
}) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    const start = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return <main className="uw-queue" aria-busy={running}>
    <div className="uw-heading"><div><h1>Queue</h1><p>Choose a guideline and model, then assess the relevant submissions.</p></div>
      <div className="uw-heading-actions">
        <label className="uw-model-switch"><span>Model</span><select value={model} onChange={(event) => onModelChange(event.target.value as ModelProvider)} disabled={running || !health}>
          <option value="openai" disabled={!health?.openai_configured}>OpenAI · evidence agent</option>
          <option value="baseten" disabled={!health?.baseten_configured}>UnderwriteIQ · Qwen3-8B{health?.baseten_configured ? "" : " (unavailable)"}</option>
        </select></label>
        <label className="uw-guideline-switch"><span className="uw-sr-only">Selected guideline</span><select value={guidelineId} onChange={(event) => onGuidelineChange(event.target.value)} disabled={running || !guidelines.length}>
          {guidelines.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.version}</option>)}
        </select></label>
        <button className="uw-rerun" onClick={onRun} disabled={running || !health || !guidelineId}>{running ? <LoaderCircle size={13} className="uw-spin" /> : <Play size={13} />}{running ? "Running" : "Run"}</button>
      </div>
    </div>
    <div className="uw-table-wrap"><div className="uw-empty uw-run-empty" role="status"><Database size={18} />
      <p>{running ? `Evidence search is running · ${seconds}s elapsed` : "No analysis run yet."}</p>
      <small>{running ? "The run selects relevant submissions, searches for missing evidence, and explains the results. Activity will show the completed search steps." : "Run the queue to assess and prioritize submissions."}</small>
    </div></div>
  </main>;
}

export default function UnderwritingQueue() {
  const [view, setView] = useState<View>("queue");
  const [data, setData] = useState<LoadedData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [failedRun, setFailedRun] = useState<FailedRunDetail | null>(null);
  const [selectedGuidelineId, setSelectedGuidelineId] = useState("");
  const [inspectedGuidelineId, setInspectedGuidelineId] = useState("");
  const [selectedModel, setSelectedModel] = useState<ModelProvider>("openai");
  const [initialHealth, setInitialHealth] = useState<HealthResponse | null>(null);
  const [packages, setPackages] = useState<GuidelinePackage[]>([]);
  const loadCatalog = useCallback(async () => {
    const guidelines = await fetchGuidelines();
    const loaded = await Promise.all(guidelines.map((item) => fetchGuideline(item.id, item.version)));
    setPackages(loaded);
    setInspectedGuidelineId((current) => current || loaded[0]?.id || "");
    return { guidelines, loaded };
  }, []);
  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => Promise.all([fetchHealth(), loadCatalog()])).then(([health, catalog]) => {
      if (!active) return;
      setInitialHealth(health);
      setSelectedGuidelineId((current) => current || catalog.guidelines[0]?.id || "");
    }).catch((caught) => {
      if (active) setError(caught instanceof Error ? caught.message : "Could not load run options.");
    });
    return () => { active = false; };
  }, [loadCatalog]);
  const load = useCallback(async (rerun = false, requestedGuidelineId?: string, requestedModel: ModelProvider = "openai") => {
    if (rerun) setRerunning(true);
    else {
      setLoading(true);
      setError(null);
    }
    try {
      const [health, submissions, catalog] = await Promise.all([fetchHealth(), fetchSubmissions(), loadCatalog()]);
      const guidelines = catalog.guidelines;
      const loadedPackages = catalog.loaded;
      const guideline = guidelines.find((item) => item.id === requestedGuidelineId) ?? guidelines[0];
      if (!guideline) throw new Error("No underwriting guideline is installed.");
      if (requestedModel === "baseten" && !health.baseten_configured) {
        throw new Error(health.baseten_error || "The UnderwriteIQ model is unavailable.");
      }
      const run = await analyzeSubmissions(guideline, requestedModel);
      const guidelinePackage = loadedPackages.find((item) => item.id === guideline.id) ?? loadedPackages[0];
      if (!guidelinePackage) throw new Error("The selected guideline package could not be loaded.");
      setSelectedGuidelineId(guideline.id);
      setInspectedGuidelineId(guideline.id);
      setSelectedModel(requestedModel);
      setData({ health, submissions, guidelines, packages: loadedPackages, guideline, guidelinePackage, run });
      setFailedRun(null);
      setError(null);
    } catch (caught) {
      if (rerun && caught instanceof AnalysisRunError) {
        setFailedRun(caught.detail);
        setError(null);
      } else {
        setError(caught instanceof Error ? caught.message : "The underwriting queue could not be loaded.");
      }
    } finally {
      setLoading(false);
      setRerunning(false);
    }
  }, [loadCatalog]);
  const openView = useCallback((next: View) => {
    setView(next);
    if (next === "guidelines" && packages.length === 0) {
      void loadCatalog().catch((caught) => {
        setError(caught instanceof Error ? caught.message : "Guideline packages could not be loaded.");
      });
    }
  }, [loadCatalog, packages.length]);

  return <div className="uw-app"><header className="uw-topbar"><div className="uw-brand"><span className="uw-brand-mark">u</span><span>underwrite</span><i />Submission review</div><div className="uw-header-right">{data && (data.health.mode === "demo" ? <span className="uw-demo">Fictional demo</span> : <span className="uw-live">Live data</span>)}<span className="uw-avatar" aria-hidden="true">UW</span></div></header>{failedRun && data && <div className="uw-global-error uw-previous-run" role="alert"><AlertTriangle size={15} /><span><strong>Rerun {failedRun.run_id} failed.</strong> Showing the previous completed run from {dateText(data.run.created_at, true)}. {failedRun.errors.join(" · ")}</span></div>}{error && <div className="uw-global-error" role="alert"><AlertTriangle size={15} /><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={14} /></button></div>}<div className="uw-shell"><Sidebar view={view} onView={(next) => void openView(next)} run={data?.run} hasGuidelines /><div className="uw-content">{view === "guidelines" ? (packages.length > 0 ? <Guidelines packages={packages} selectedId={inspectedGuidelineId || packages[0].id} onSelect={setInspectedGuidelineId} /> : <main className="uw-supporting-view"><div className="uw-heading"><div><h1>Guidelines</h1><p>Loading the installed underwriting packages…</p></div></div></main>) : !data ? <EmptyQueue running={loading} health={initialHealth} guidelines={packages} model={selectedModel} guidelineId={selectedGuidelineId} onModelChange={setSelectedModel} onGuidelineChange={setSelectedGuidelineId} onRun={() => void load(false, selectedGuidelineId, selectedModel)} /> : view === "queue" ? <Queue key={data.run.run_id} data={data} selectedModel={selectedModel} onRerun={() => void load(true, selectedGuidelineId, selectedModel)} onGuidelineChange={(id) => void load(true, id, selectedModel)} onModelChange={(provider) => void load(true, selectedGuidelineId, provider)} rerunning={rerunning} /> : <RunActivity run={data.run} />}</div></div></div>;
}
