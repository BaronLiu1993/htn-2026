"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import { Dialog } from "@base-ui/react/dialog";
import { Popover } from "@base-ui/react/popover";
import {
  Activity, AlertTriangle, BookOpen, Check, ChevronDown, CircleX,
  Database, ListFilter, LoaderCircle, Play, RefreshCw, Search, Target, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AnalysisRunError,
  analyzeSubmissions,
  fetchGuideline,
  fetchGuidelines,
  fetchHealth,
  fetchSubmissions,
} from "../../lib/api";
import type {
  AnalysisRun, Assessment, AssessmentStatus, EvidenceItem, GuidelinePackage,
  GuidelineRule, GuidelineSummary, FailedRunDetail, HealthResponse, ModelProvider,
  QueueSubmission, RuleOutcome, TraceEvent, UnderwritingConsideration,
} from "../../lib/types";
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
type DisplayEvidence = {
  id: string; sourceKey: string; claim: string; record: string; field: string; value: string;
  ruleName: string; expected: string; note?: string; preview: string;
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

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not available";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (Array.isArray(value)) return value.map(valueText).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function money(value?: number | null): string {
  return value === null || value === undefined
    ? "Not available"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
}

function dateText(value?: string | null, withTime = false): string {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
  }).format(date);
}

function adaptEvidence(item: EvidenceItem, ruleName: string, expected: string, note?: string): DisplayEvidence {
  const value = valueText(item.value);
  return {
    id: [item.resource, item.record_id, item.field, ruleName].join("::"),
    sourceKey: [item.resource, item.record_id, item.field].join("::"),
    claim: item.label || item.field,
    record: `${item.resource} ${item.record_id}`,
    field: item.field,
    value,
    ruleName,
    expected,
    note,
    preview: `${item.label || item.field}: ${value}`,
  };
}

function ruleEvidence(rule: RuleOutcome): DisplayEvidence[] {
  return rule.evidence.map((item) => adaptEvidence(item, rule.name, rule.expected, rule.note ?? undefined));
}

function copeEvidence(item: UnderwritingConsideration): DisplayEvidence[] {
  const category = `${item.category[0].toUpperCase()}${item.category.slice(1)} underwriting consideration`;
  return item.evidence.map((evidence) => adaptEvidence(
    evidence,
    category,
    item.summary,
    item.appetite_rule_applied ? "An appetite rule applies to this consideration." : "No carrier decision rule was supplied.",
  ));
}

function allEvidence(assessment: Assessment): DisplayEvidence[] {
  const items = [
    ...assessment.passed_requirements.flatMap(ruleEvidence),
    ...assessment.failed_requirements.flatMap(ruleEvidence),
    ...assessment.unresolved_rules.flatMap(ruleEvidence),
    ...assessment.matched_preferences.flatMap(ruleEvidence),
    ...assessment.cope.flatMap(copeEvidence),
  ];
  const seen = new Set<string>();
  const unique = items.filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id)));
  const covered = new Set(unique.map((item) => item.sourceKey));
  for (const item of assessment.evidence) {
    if (covered.has([item.resource, item.record_id, item.field].join("::"))) continue;
    const adapted = adaptEvidence(item, "Overall assessment", "Supports the returned underwriting assessment.");
    if (seen.has(adapted.id)) continue;
    seen.add(adapted.id);
    covered.add(adapted.sourceKey);
    unique.push(adapted);
  }
  return unique;
}

function evidenceFor(items: DisplayEvidence[], terms: string[]): DisplayEvidence[] {
  return items.filter((item) => {
    const haystack = `${item.claim} ${item.field}`.toLowerCase();
    return terms.some((term) => haystack.includes(term));
  });
}

function Badge({ assessment }: { assessment: Assessment }) {
  const tone = assessment.status === "target" ? "green" : assessment.status === "acceptable" ? "acceptable" : assessment.status === "needs_review" ? "amber" : "red";
  return <span className={`uw-badge ${tone}`}>{labels[assessment.status]}</span>;
}

function EvidenceCard({ evidence, number, demo }: { evidence: DisplayEvidence; number: number; demo: boolean }) {
  return (
    <Popover.Root>
      <Popover.Trigger className="uw-evidence-marker" aria-label={`Evidence ${number}: ${evidence.preview}`} openOnHover delay={140} closeDelay={160}>
        {number}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8} className="uw-evidence-positioner">
          <Popover.Popup className="uw-evidence-popup">
            <Popover.Arrow className="uw-evidence-arrow" />
            <div className="uw-popover-head"><Popover.Title>{evidence.claim}</Popover.Title><Popover.Close aria-label="Close evidence"><X size={14} /></Popover.Close></div>
            <Popover.Description>{evidence.preview}</Popover.Description>
            <dl>
              <div><dt>Source record</dt><dd>{evidence.record}</dd></div>
              <div><dt>Field</dt><dd>{evidence.field}</dd></div>
              <div><dt>Value</dt><dd>{evidence.value}</dd></div>
              <div><dt>Applicable rule</dt><dd>{evidence.ruleName}</dd></div>
              <div><dt>Expected</dt><dd>{evidence.expected}</dd></div>
              {evidence.note && <div><dt>Rule note</dt><dd>{evidence.note}</dd></div>}
            </dl>
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

function Review({ assessment, run }: { assessment: Assessment; run: AnalysisRun }) {
  const evidence = useMemo(() => allEvidence(assessment), [assessment]);
  const [actionOpen, setActionOpen] = useState(false);
  const unresolved = assessment.status === "needs_review";
  const excluded = assessment.status === "out_of_appetite";
  const eligibility = unresolved
    ? assessment.unresolved_rules.flatMap(ruleEvidence)
    : excluded ? assessment.failed_requirements.flatMap(ruleEvidence) : assessment.passed_requirements.flatMap(ruleEvidence);
  const preferences = assessment.matched_preferences.flatMap(ruleEvidence);
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
      <p>{assessment.explanation} <Bundle items={eligibility} label={unresolved ? "missing evidence" : excluded ? "exclusion evidence" : `${eligibility.length} eligibility records`} demo={run.mode === "demo"} />{preferences.length > 0 && <> · <Bundle items={preferences} label="preference evidence" demo={run.mode === "demo"} /></>}</p>
    </section>
    {(issues.length > 0 || excluded) && <section className={`uw-review-alert ${excluded ? "danger" : "warning"}`}>
      {excluded ? <CircleX size={16} /> : <AlertTriangle size={16} />}<div><strong>{excluded ? "Confirmed exclusion" : "Missing information and warnings"}</strong>{issues.length ? <ul>{issues.map((item) => <li key={item}>{item}</li>)}</ul> : <p>Target preferences cannot override a confirmed appetite exclusion.</p>}</div>
    </section>}
    <section><div className="uw-section-kicker">Supporting facts</div><Facts assessment={assessment} evidence={evidence} markerIndex={markerIndex} demo={run.mode === "demo"} /></section>
    <section className="uw-target-breakdown">
      <div className="uw-section-kicker">Target preferences</div>
      {unresolved || excluded ? <p className="uw-score-withheld">Final fit score withheld. {unresolved ? "Establish eligibility first." : "The account is excluded."}</p> : <p className="uw-fit-summary"><strong>{assessment.target_matches} of {assessment.target_preferences_total}</strong> preferences match</p>}
      <div className="uw-target-list">{assessment.matched_preferences.map((rule) => {
        const records = ruleEvidence(rule);
        return <div key={rule.rule_id}><span>{rule.name}<span className="uw-marker-group"><Markers items={records} index={markerIndex} demo={run.mode === "demo"} /></span></span><strong>Target preference</strong></div>;
      })}{assessment.matched_preferences.length === 0 && <p className="uw-empty-copy">No matched preference rules were returned.</p>}</div>
      <p className="uw-review-disclaimer">Target fit orders eligible accounts. It is not an approval probability or profit forecast.</p>
    </section>
    <section className="uw-cope"><div className="uw-section-kicker">Underwriting considerations</div>{assessment.cope.map((item) => {
      const records = copeEvidence(item);
      return <div key={item.category}><span>{item.category}</span><p>{item.summary}</p>{records.length ? <Bundle items={records} label={`${records.length} ${records.length === 1 ? "source" : "sources"}`} demo={run.mode === "demo"} /> : <small>Not available</small>}</div>;
    })}</section>
    <details className="uw-agent-activity"><summary><span><Activity size={15} />Agent activity</span><span>{trace.length} review updates <ChevronDown size={14} /></span></summary><div className="uw-activity-body">{run.agent_summary && <p>{run.agent_summary}</p>}<ActivityRail events={trace} emptyLabel="No trace events were returned for this account." /></div></details>
    <div className="uw-review-action"><button className="uw-primary" onClick={() => setActionOpen((open) => !open)}>{actionOpen ? "Hide review checklist" : assessment.recommended_action}<span>↗</span></button>{actionOpen && <div role="status" className="uw-action-result"><strong>Human review required</strong><p>Confirm the returned evidence and document the underwriting decision. This queue does not approve or decline coverage.</p></div>}</div>
  </div>;
}

function AccountReview({ assessment, run }: { assessment: Assessment; run: AnalysisRun }) {
  const label = assessment.status === "needs_review" ? "Resolve gap" : assessment.status === "out_of_appetite" ? "View exclusion" : "Review account";
  return <Dialog.Root><Dialog.Trigger className="uw-text-button">{label} <span>↗</span></Dialog.Trigger><Dialog.Portal><Dialog.Backdrop className="uw-backdrop" /><Dialog.Popup className="uw-drawer"><Dialog.Close className="uw-close" aria-label="Close review"><X size={18} /></Dialog.Close><Review assessment={assessment} run={run} /></Dialog.Popup></Dialog.Portal></Dialog.Root>;
}

function Sidebar({ view, onView, run, hasGuidelines }: { view: View; onView: (view: View) => void; run?: AnalysisRun; hasGuidelines: boolean }) {
  return <aside className="uw-sidebar" aria-label="Underwriting navigation"><nav><button aria-current={view === "queue" ? "page" : undefined} onClick={() => onView("queue")}><ListFilter size={16} />Queue</button><button aria-current={view === "activity" ? "page" : undefined} onClick={() => onView("activity")} disabled={!run}><Activity size={16} />Run activity</button></nav><div className="uw-sidebar-secondary"><button aria-current={view === "guidelines" ? "page" : undefined} onClick={() => onView("guidelines")} disabled={!hasGuidelines}><BookOpen size={16} />Guidelines</button><div className="uw-data-state"><span />{run ? run.mode === "demo" ? "Demo data" : "Live data" : "No run yet"}<small>{run ? `${run.assessments.length} assessed submissions` : "Start from the queue"}</small></div></div></aside>;
}

function Queue({ data, selectedModel, onRerun, onGuidelineChange, onModelChange, rerunning }: { data: LoadedData; selectedModel: ModelProvider; onRerun: () => void; onGuidelineChange: (id: string) => void; onModelChange: (provider: ModelProvider) => void; rerunning: boolean }) {
  const { run, guideline, guidelines } = data;
  const [group, setGroup] = useState<Group>("Pursue");
  const [status, setStatus] = useState<StatusFilter>(null);
  const [search, setSearch] = useState("");
  const assessments = run.assessments;
  const rows = useMemo(() => assessments.filter((item) =>
    groupFor(item.status) === group && (!status || item.status === status) &&
    `${item.insured_name} ${item.submission_number} ${item.primary_state ?? ""}`.toLowerCase().includes(search.trim().toLowerCase()),
  ), [assessments, group, search, status]);
  const eligible = assessments.filter((item) => item.status === "target" || item.status === "acceptable").length;
  const unresolved = assessments.filter((item) => item.status === "needs_review").length;
  const excluded = assessments.filter((item) => item.status === "out_of_appetite").length;

  function selectStatus(next: AssessmentStatus) {
    if (status === next) return setStatus(null);
    setGroup(groupFor(next));
    setStatus(next);
  }

  return <main className="uw-queue">
    <div className="uw-heading"><div><h1>Queue</h1><p>{run.in_scope_submissions} relevant · {eligible} eligible · {unresolved} need review · {excluded} outside appetite</p></div><div className="uw-heading-actions"><label className="uw-model-switch" title={data.health.baseten_error ?? undefined}><span>Model</span><select value={selectedModel} onChange={(event) => onModelChange(event.target.value as ModelProvider)} disabled={rerunning}><option value="openai" disabled={!data.health.openai_configured}>OpenAI · evidence agent</option><option value="baseten" disabled={!data.health.baseten_configured}>UnderwriteIQ · Qwen3-8B{data.health.baseten_configured ? "" : " (unavailable)"}</option></select></label><label className="uw-guideline-switch"><span className="uw-sr-only">Selected guideline</span><select value={guideline.id} onChange={(event) => onGuidelineChange(event.target.value)} disabled={rerunning}>{guidelines.map((item) => <option key={`${item.id}-${item.version}`} value={item.id}>{item.name} · {item.version}</option>)}</select></label><button className="uw-rerun" onClick={onRerun} disabled={rerunning}>{rerunning ? <LoaderCircle size={13} className="uw-spin" /> : <RefreshCw size={13} />}{rerunning ? "Running" : "Rerun"}</button></div></div>
    {run.scope_unknown_submissions > 0 && <div className="uw-inline-banner warning" role="status"><AlertTriangle size={16} /><div><strong>{run.scope_unknown_submissions} {run.scope_unknown_submissions === 1 ? "submission has" : "submissions have"} unknown guideline scope</strong><p>These submissions are not assessed until their line of business is confirmed.</p></div></div>}
    {run.status === "partial" && <div className="uw-inline-banner warning" role="status"><AlertTriangle size={16} /><div><strong>Partial analysis run</strong><p>{run.errors.join(" · ") || "Some submissions could not be assessed."}</p></div></div>}
    <section className="uw-status-cards" aria-label="Filter queue by appetite status">{cards.map((card) => {
      const Icon = card.icon;
      const count = assessments.filter((item) => item.status === card.status).length;
      return <button key={card.status} className={card.tone} aria-pressed={status === card.status} onClick={() => selectStatus(card.status)}><span><Icon size={14} />{card.label}</span><strong>{count}</strong></button>;
    })}</section>
    <div className="uw-toolbar"><div className="uw-filters" aria-label="Action groups">{groups.map((item) => <button key={item} aria-pressed={group === item && status === null} data-current-group={group === item ? "" : undefined} onClick={() => { setGroup(item); setStatus(null); }}>{item}<span>{assessments.filter((assessment) => groupFor(assessment.status) === item).length}</span></button>)}</div><label className="uw-search"><span className="uw-sr-only">Search accounts</span><Search size={14} /><input placeholder="Search accounts…" value={search} onChange={(event) => setSearch(event.target.value)} /></label></div>
    <div className="uw-table-wrap"><table><thead><tr><th>Account / assessment</th><th>Appetite</th><th>Target fit</th><th>Premium</th><th>Received</th><th>Next step</th></tr></thead><tbody>{rows.map((item) => <tr key={item.submission_id}><td><strong>{item.insured_name}</strong><small>{item.submission_number} · {item.primary_state ?? "State unavailable"} · {money(item.tiv)} TIV</small><p>{item.explanation}</p></td><td><Badge assessment={item} /></td><td>{item.status === "target" || item.status === "acceptable" ? <><strong>{item.target_matches} of {item.target_preferences_total}</strong><small>target preferences</small></> : <span className="uw-muted">Not scored</span>}</td><td>{money(item.premium)}</td><td>{dateText(item.received_date)}</td><td><AccountReview assessment={item} run={run} /></td></tr>)}</tbody></table>{!rows.length && <div className="uw-empty"><Database size={18} /><p>No accounts match this filter.</p><button onClick={() => { setSearch(""); setStatus(null); }}>Clear filters</button></div>}</div>
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

function ProcessingTimer({ startedAt }: { startedAt: number }) {
  const [elapsedSeconds, setElapsedSeconds] = useState(() => Math.floor((Date.now() - startedAt) / 1000));

  useEffect(() => {
    const update = () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    update();
    const interval = window.setInterval(update, 1_000);
    return () => window.clearInterval(interval);
  }, [startedAt]);

  const minutes = Math.floor(elapsedSeconds / 60).toString().padStart(2, "0");
  const seconds = (elapsedSeconds % 60).toString().padStart(2, "0");
  return <small className="uw-processing-timer" aria-live="off">Processing time {minutes}:{seconds}</small>;
}

function EmptyQueue({ running, startedAt, onRun }: { running: boolean; startedAt: number | null; onRun: () => void }) {
  return <main className="uw-queue" aria-busy={running}><div className="uw-heading"><div><h1>Queue</h1><p>Assess available submissions against the active underwriting guideline.</p></div><div className="uw-heading-actions"><button className="uw-rerun" onClick={onRun} disabled={running}>{running ? <LoaderCircle size={13} className="uw-spin" /> : <Play size={13} />}{running ? "Running" : "Run"}</button></div></div><div className="uw-table-wrap"><div className="uw-empty uw-run-empty" role="status"><Database size={18} />{running && startedAt !== null && <ProcessingTimer startedAt={startedAt} />}<p>{running ? "Analyzing available submissions…" : "No analysis run yet."}</p>{!running && <small>Run the queue to assess and prioritize submissions.</small>}</div></div></main>;
}

export default function UnderwritingQueue() {
  const [view, setView] = useState<View>("queue");
  const [data, setData] = useState<LoadedData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [processingStartedAt, setProcessingStartedAt] = useState<number | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [failedRun, setFailedRun] = useState<FailedRunDetail | null>(null);
  const [selectedGuidelineId, setSelectedGuidelineId] = useState("");
  const [inspectedGuidelineId, setInspectedGuidelineId] = useState("");
  const [selectedModel, setSelectedModel] = useState<ModelProvider>("openai");
  const [packages, setPackages] = useState<GuidelinePackage[]>([]);
  const loadCatalog = useCallback(async () => {
    const guidelines = await fetchGuidelines();
    const loaded = await Promise.all(guidelines.map((item) => fetchGuideline(item.id, item.version)));
    setPackages(loaded);
    setInspectedGuidelineId((current) => current || loaded[0]?.id || "");
    return { guidelines, loaded };
  }, []);
  const load = useCallback(async (rerun = false, requestedGuidelineId?: string, requestedModel: ModelProvider = "openai") => {
    if (rerun) setRerunning(true);
    else {
      setLoading(true);
      setProcessingStartedAt(Date.now());
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
      setProcessingStartedAt(null);
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

  return <div className="uw-app"><header className="uw-topbar"><div className="uw-brand"><span className="uw-brand-mark">u</span><span>underwrite</span><i />Submission review</div><div className="uw-header-right">{data && (data.health.mode === "demo" ? <span className="uw-demo">Fictional demo</span> : <span className="uw-live">Live data</span>)}<span className="uw-avatar" aria-hidden="true">UW</span></div></header>{failedRun && data && <div className="uw-global-error uw-previous-run" role="alert"><AlertTriangle size={15} /><span><strong>Rerun {failedRun.run_id} failed.</strong> Showing the previous completed run from {dateText(data.run.created_at, true)}. {failedRun.errors.join(" · ")}</span></div>}{error && <div className="uw-global-error" role="alert"><AlertTriangle size={15} /><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={14} /></button></div>}<div className="uw-shell"><Sidebar view={view} onView={(next) => void openView(next)} run={data?.run} hasGuidelines /><div className="uw-content">{view === "guidelines" ? (packages.length > 0 ? <Guidelines packages={packages} selectedId={inspectedGuidelineId || packages[0].id} onSelect={setInspectedGuidelineId} /> : <main className="uw-supporting-view"><div className="uw-heading"><div><h1>Guidelines</h1><p>Loading the installed underwriting packages…</p></div></div></main>) : !data ? <EmptyQueue running={loading} startedAt={processingStartedAt} onRun={() => void load(false, undefined, selectedModel)} /> : view === "queue" ? <Queue data={data} selectedModel={selectedModel} onRerun={() => void load(true, selectedGuidelineId, selectedModel)} onGuidelineChange={(id) => void load(true, id, selectedModel)} onModelChange={(provider) => void load(true, selectedGuidelineId, provider)} rerunning={rerunning} /> : <RunActivity run={data.run} />}</div></div></div>;
}
