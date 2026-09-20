// Turns an assessment's rule outcomes and evidence items into what an underwriter needs to
// read at a glance: the verdict, the value measured against the rule's actual threshold, how
// the value was derived, and how fresh it is.
//
// Numeric thresholds and derivations are not on the analysis response. They come from the
// guideline package, joined by rule id and fact id.

import type {
  Assessment,
  EvidenceItem,
  EvidenceState,
  GuidelineFactSource,
  GuidelineOperator,
  GuidelinePackage,
  GuidelineRule,
  RuleOutcome,
  UnderwritingConsideration,
} from "./types";

export type EvidenceDisplay = "money" | "percent" | "year" | "plain";
export type Verdict = "pass" | "fail" | "unresolved";
export type Threshold = number | [number, number];

export type Tick = {
  key: string;
  ruleId: string;
  at: number;
  label: string;
  kind: "requirement" | "preference";
};

export type DisplayEvidence = {
  id: string;
  sourceKey: string;
  claim: string;
  record: string;
  field: string;
  value: string;
  ruleId: string | null;
  ruleName: string;
  expected: string;
  note?: string;
  preview: string;
  /** "rule" carries a verdict; "context" is supporting evidence with no pass/fail. */
  shape: "rule" | "context";
  ruleState: RuleOutcome["state"] | null;
  verdict: Verdict;
  display: EvidenceDisplay;
  numeric: number | null;
  threshold: Threshold | null;
  operator: GuidelineOperator | null;
  domain: [number, number] | null;
  band: { left: number; width: number } | null;
  ticks: Tick[];
  derivation: string | null;
  evidenceState: EvidenceState;
  sourceDate?: string | null;
  observedAt?: string | null;
  others: { id: string; name: string; state: RuleOutcome["state"]; label: string }[];
};

// ---------- formatting ----------

export function money(value?: number | null): string {
  return value === null || value === undefined
    ? "Not available"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(value);
}

export function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not available";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (Array.isArray(value)) return value.map(valueText).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Compact form for a headline: $65.8M, $150M, $267K, 0%, 1972. */
export function shortValue(value: number, display: EvidenceDisplay): string {
  if (display === "percent") return `${Math.round(value * 100)}%`;
  if (display === "year") return String(value);
  if (display === "money") {
    const size = Math.abs(value);
    if (size >= 1_000_000_000) return `$${trim(value / 1_000_000_000)}B`;
    if (size >= 1_000_000) return `$${trim(value / 1_000_000)}M`;
    if (size >= 10_000) return `$${Math.round(value / 1_000)}K`;
    if (size >= 1_000) return `$${trim(value / 1_000)}K`;
    return money(value);
  }
  if (Math.abs(value) >= 1_000_000) return `${trim(value / 1_000_000)}M`;
  return value.toLocaleString("en-US");
}

/** One decimal, but only when it says something: 65.8, 150, 2.5. */
function trim(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

export function exactValue(value: number, display: EvidenceDisplay): string {
  if (display === "percent") return `${Math.round(value * 100)}%`;
  if (display === "year") return String(value);
  if (display === "money") return money(value);
  return value.toLocaleString("en-US");
}

/** "a $90M maximum", "a 1990 minimum", "the $50K to $175K range". */
export function limitPhrase(
  threshold: Threshold,
  operator: GuidelineOperator | null,
  display: EvidenceDisplay,
): string {
  if (Array.isArray(threshold)) {
    return `the ${shortValue(threshold[0], display)} to ${shortValue(threshold[1], display)} range`;
  }
  const bound = operator === "lt" || operator === "lte" ? "maximum" : "minimum";
  return `a ${shortValue(threshold, display)} ${bound}`;
}

export const verdictLabel: Record<RuleOutcome["state"], string> = {
  passed: "Within limit",
  matched: "Target match",
  failed: "Outside limit",
  not_matched: "No match",
  unresolved: "Not confirmed",
};

export const evidenceStateLabel: Record<EvidenceState, string> = {
  verified: "Verified",
  missing: "Missing",
  conflicting: "Conflicting",
  ambiguous: "Ambiguous",
  unavailable: "Unavailable",
};

export function verdictOf(state: RuleOutcome["state"]): Verdict {
  if (state === "passed" || state === "matched") return "pass";
  if (state === "failed") return "fail";
  return "unresolved";
}

// ---------- scale geometry ----------

export function position(domain: [number, number], value: number): number {
  const [min, max] = domain;
  if (max === min) return 0;
  return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

function bandFor(
  domain: [number, number],
  operator: GuidelineOperator | null,
  threshold: Threshold,
): { left: number; width: number } | null {
  if (Array.isArray(threshold)) {
    const left = position(domain, threshold[0]);
    return { left, width: position(domain, threshold[1]) - left };
  }
  if (!operator) return null;
  const at = position(domain, threshold);
  if (operator === "lt" || operator === "lte") return { left: 0, width: at };
  if (operator === "gt" || operator === "gte") return { left: at, width: 1 - at };
  return null;
}

function domainFor(
  display: EvidenceDisplay,
  points: number[],
): [number, number] | null {
  if (points.length === 0) return null;
  if (display === "percent") return [0, 1];
  const max = Math.max(...points);
  const min = Math.min(...points);
  if (display === "year") {
    const pad = Math.max(6, Math.round((max - min) * 0.25));
    return [min - pad, max + pad];
  }
  if (max <= 0) return null;
  return [Math.min(0, min), max * 1.2];
}

// ---------- guideline package lookups ----------

function numericThreshold(value: unknown): Threshold | null {
  if (typeof value === "number") return value;
  if (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every((entry) => typeof entry === "number")
  ) {
    return [value[0] as number, value[1] as number];
  }
  return null;
}

function isYearish(factId: string, value: number | null): boolean {
  if (value === null) return false;
  return /year/.test(factId) && value > 1500 && value < 2500;
}

const MONEY_FACT = /tiv|premium|loss|limit|value|deductible|payroll|revenue|cost/i;

function displayFor(
  pkg: GuidelinePackage | null,
  factId: string | null,
  value: number | null,
  expected?: string,
): EvidenceDisplay {
  const declared = factId
    ? pkg?.required_facts.find((fact) => fact.id === factId)?.display
    : undefined;
  if (declared === "money" || declared === "percent") return declared;
  if (factId && isYearish(factId, value)) return "year";
  // Guideline packages leave most money facts on the default "plain", so fall back to the
  // signals that are actually reliable: the authored expected string, then the fact name.
  if (expected?.includes("$")) return "money";
  if (factId && MONEY_FACT.test(factId)) return "money";
  return "plain";
}

const COLLECTION_FALLBACK = "records";

function derivationFor(source: GuidelineFactSource | undefined, count: number): string | null {
  if (!source) return null;
  const collection = source.collection ?? COLLECTION_FALLBACK;
  const noun = count === 1 ? collection.replace(/s$/, "") : collection;
  switch (source.operation) {
    case "minimum":
      return `Lowest value across ${count} ${noun}`;
    case "maximum":
      return `Highest value across ${count} ${noun}`;
    case "sum":
      return `Summed across ${count} ${noun}`;
    case "weighted_match_share":
      return `Value-weighted share across ${count} ${noun}`;
    case "rolling_sum":
    case "rolling_component_sum":
      return source.window_years
        ? `Rolling ${source.window_years}-year total across ${count} ${noun}`
        : `Rolling total across ${count} ${noun}`;
    default:
      return `Read from ${source.resource}`;
  }
}

function packageRules(pkg: GuidelinePackage | null): GuidelineRule[] {
  return pkg ? [...pkg.requirements, ...pkg.preferences] : [];
}

function kindOf(pkg: GuidelinePackage | null, ruleId: string): "requirement" | "preference" {
  return pkg?.preferences.some((rule) => rule.id === ruleId) ? "preference" : "requirement";
}

// ---------- adapters ----------

export type EvidenceContext = {
  pkg: GuidelinePackage | null;
  assessment: Assessment;
};

type RuleFacts = {
  ruleId: string | null;
  ruleName: string;
  ruleState: RuleOutcome["state"] | null;
  expected: string;
  note?: string;
};

function adaptEvidence(
  item: EvidenceItem,
  rule: RuleFacts,
  ctx: EvidenceContext,
): DisplayEvidence {
  const value = valueText(item.value);
  const factId = item.fact_id ?? null;
  const ledgerFact = ctx.assessment.ledger?.facts.find((fact) => fact.fact_id === factId);
  const numeric = typeof ledgerFact?.value === "number" ? ledgerFact.value : null;
  const display = displayFor(ctx.pkg, factId, numeric, rule.expected);

  const packageRule = rule.ruleId
    ? packageRules(ctx.pkg).find((entry) => entry.id === rule.ruleId)
    : undefined;
  const threshold = packageRule ? numericThreshold(packageRule.value) : null;
  const operator = packageRule?.operator ?? null;

  // Every rule reading the same fact, so one number never looks like two unrelated checks.
  const siblings = factId
    ? packageRules(ctx.pkg).filter((entry) => entry.fact === factId)
    : [];
  const outcomes = [
    ...ctx.assessment.passed_requirements,
    ...ctx.assessment.failed_requirements,
    ...ctx.assessment.unresolved_rules,
    ...ctx.assessment.matched_preferences,
  ];

  const marks = siblings
    .flatMap((entry) => {
      const bound = numericThreshold(entry.value);
      if (bound === null) return [];
      return (Array.isArray(bound) ? bound : [bound]).map((at) => ({
        key: `${entry.id}-${at}`,
        ruleId: entry.id,
        at,
        kind: kindOf(ctx.pkg, entry.id),
      }));
    })
    .sort((a, b) => a.at - b.at);

  const domain =
    threshold !== null || marks.length > 0
      ? domainFor(display, [...marks.map((mark) => mark.at), ...(numeric !== null ? [numeric] : [])])
      : null;

  const placedTicks: Tick[] = domain
    ? marks.map((mark) => ({
        key: mark.key,
        ruleId: mark.ruleId,
        at: position(domain, mark.at),
        label: shortValue(mark.at, display),
        kind: mark.kind,
      }))
    : [];

  const others = siblings
    .filter((entry) => entry.id !== rule.ruleId)
    .flatMap((entry) => {
      const outcome = outcomes.find((candidate) => candidate.rule_id === entry.id);
      if (!outcome) return [];
      return [
        {
          id: entry.id,
          name: entry.name,
          state: outcome.state,
          label: verdictLabel[outcome.state],
        },
      ];
    });

  return {
    id: [item.resource, item.record_id, item.field, rule.ruleName].join("::"),
    sourceKey: [item.resource, item.record_id, item.field].join("::"),
    claim: item.label || item.field,
    record: `${item.resource} ${item.record_id}`,
    field: item.field,
    value,
    ruleId: rule.ruleId,
    ruleName: rule.ruleName,
    expected: rule.expected,
    note: rule.note,
    preview: `${item.label || item.field}: ${value}`,
    shape: rule.ruleState ? "rule" : "context",
    ruleState: rule.ruleState,
    verdict: rule.ruleState ? verdictOf(rule.ruleState) : "unresolved",
    display,
    numeric,
    threshold,
    operator,
    domain,
    band: domain && threshold !== null ? bandFor(domain, operator, threshold) : null,
    ticks: placedTicks,
    derivation: derivationFor(
      ctx.pkg?.required_facts.find((fact) => fact.id === factId)?.source,
      ledgerFact?.observations.length ?? 0,
    ),
    evidenceState: item.state,
    sourceDate: item.source_date,
    observedAt: item.observed_at,
    others,
  };
}

export function ruleEvidence(rule: RuleOutcome, ctx: EvidenceContext): DisplayEvidence[] {
  return rule.evidence.map((item) =>
    adaptEvidence(
      item,
      {
        ruleId: rule.rule_id,
        ruleName: rule.name,
        ruleState: rule.state,
        expected: rule.expected,
        note: rule.note ?? undefined,
      },
      ctx,
    ),
  );
}

export function copeEvidence(
  item: UnderwritingConsideration,
  ctx: EvidenceContext,
): DisplayEvidence[] {
  const category = `${item.category[0].toUpperCase()}${item.category.slice(1)} underwriting consideration`;
  return item.evidence.map((evidence) =>
    adaptEvidence(
      evidence,
      {
        ruleId: null,
        ruleName: category,
        ruleState: null,
        expected: item.summary,
        note: item.appetite_rule_applied
          ? "An appetite rule applies to this consideration."
          : "No carrier decision rule was supplied.",
      },
      ctx,
    ),
  );
}

export function allEvidence(
  assessment: Assessment,
  pkg: GuidelinePackage | null,
): DisplayEvidence[] {
  const ctx: EvidenceContext = { pkg, assessment };
  const items = [
    ...assessment.passed_requirements.flatMap((rule) => ruleEvidence(rule, ctx)),
    ...assessment.failed_requirements.flatMap((rule) => ruleEvidence(rule, ctx)),
    ...assessment.unresolved_rules.flatMap((rule) => ruleEvidence(rule, ctx)),
    ...assessment.matched_preferences.flatMap((rule) => ruleEvidence(rule, ctx)),
    ...assessment.cope.flatMap((item) => copeEvidence(item, ctx)),
  ];
  const seen = new Set<string>();
  const unique = items.filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id)));
  const covered = new Set(unique.map((item) => item.sourceKey));
  for (const item of assessment.evidence) {
    if (covered.has([item.resource, item.record_id, item.field].join("::"))) continue;
    const adapted = adaptEvidence(
      item,
      {
        ruleId: null,
        ruleName: "Overall assessment",
        ruleState: null,
        expected: "Supports the returned underwriting assessment.",
      },
      ctx,
    );
    if (seen.has(adapted.id)) continue;
    seen.add(adapted.id);
    covered.add(adapted.sourceKey);
    unique.push(adapted);
  }
  return unique;
}

export function evidenceFor(items: DisplayEvidence[], terms: string[]): DisplayEvidence[] {
  return items.filter((item) => {
    const haystack = `${item.claim} ${item.field}`.toLowerCase();
    return terms.some((term) => haystack.includes(term));
  });
}
