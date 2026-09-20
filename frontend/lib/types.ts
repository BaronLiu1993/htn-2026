export type AssessmentStatus =
  | "target"
  | "acceptable"
  | "needs_review"
  | "out_of_appetite";

export type AnalysisStatus = AssessmentStatus | "not_analyzed";
export type ModelProvider = "openai" | "baseten";

export interface EvidenceItem {
  resource: string;
  record_id: string;
  field: string;
  value: unknown;
  label: string;
  source_system: string;
  source_date?: string | null;
  observed_at?: string | null;
  fact_id?: string | null;
  state: EvidenceState;
}

export type EvidenceState =
  | "verified"
  | "missing"
  | "conflicting"
  | "ambiguous"
  | "unavailable";

export interface EvidenceObservation {
  source_system: string;
  resource: string;
  record_id: string;
  field_path: string;
  value: unknown;
  source_date?: string | null;
  retrieved_at: string;
  state: EvidenceState;
}

export interface EvidenceFact {
  fact_id: string;
  label: string;
  value: unknown;
  state: EvidenceState;
  requirement_ids: string[];
  observations: EvidenceObservation[];
  note?: string | null;
}

export interface EvidenceLedger {
  submission_id: string;
  guideline_id: string;
  guideline_version: string;
  facts: EvidenceFact[];
}

export interface RuleOutcome {
  rule_id: string;
  name: string;
  kind: "requirement" | "preference";
  state: "passed" | "failed" | "matched" | "not_matched" | "unresolved";
  actual_value: unknown;
  expected: string;
  evidence: EvidenceItem[];
  note?: string | null;
}

export interface UnderwritingConsideration {
  category: string;
  label?: string | null;
  status: "available" | "partial" | "missing" | "unavailable";
  summary: string;
  appetite_rule_applied: boolean;
  evidence: EvidenceItem[];
}

export interface Assessment {
  enrichment_rank_change?: number;
  disaster_declaration_count?: number | null;
  disaster_context_since?: string | null;
  disaster_context_retrieved_at?: string | null;
  submission_id: string;
  submission_number: string;
  insured_name: string;
  insured_id?: string | null;
  received_date?: string | null;
  effective_date?: string | null;
  status: AssessmentStatus;
  target_matches: number;
  target_preferences_total: number;
  evidence_completeness: number;
  premium?: number | null;
  tiv?: number | null;
  primary_state?: string | null;
  matched_preferences: RuleOutcome[];
  passed_requirements: RuleOutcome[];
  failed_requirements: RuleOutcome[];
  unresolved_rules: RuleOutcome[];
  missing_information: string[];
  recommended_action: string;
  explanation: string;
  evidence: EvidenceItem[];
  ledger?: EvidenceLedger | null;
  profile_considerations: UnderwritingConsideration[];
  cope: UnderwritingConsideration[];
  warnings: string[];
  run_id: string;
  guideline_id: string;
  guideline_version: string;
  guideline_effective_date: string;
  explanation_source: "openai" | "deterministic";
}

export interface QueueSubmission {
  submission_id: string;
  submission_number: string;
  insured_name: string;
  received_date?: string | null;
  premium?: number | null;
  tiv?: number | null;
  primary_state?: string | null;
  line_of_business?: string | null;
  scope_status:
    | "in_scope"
    | "outside_scope"
    | "scope_unknown"
    | "not_evaluated";
  analysis_status: "not_analyzed";
}

export interface TraceEvent {
  id: string;
  submission_id?: string | null;
  tool: string;
  purpose: string;
  status: "success" | "retry" | "failure" | "cached";
  started_at: string;
  duration_ms: number;
  fields: string[];
  fact_ids: string[];
  adapter?: string | null;
  budget_remaining?: number | null;
  result_summary: string;
  error?: string | null;
  records_inspected?: number | null;
  facts_changed?: number | null;
  page_count?: number | null;
  source_resource?: string | null;
}

export interface AnalysisRun {
  run_id: string;
  mode: "demo" | "live";
  status: "completed" | "partial" | "failed";
  created_at: string;
  schema_source: "demo" | "live" | "cache";
  assessments: Assessment[];
  trace: TraceEvent[];
  activity: TraceEvent[];
  useful_fact_changes: number;
  query_count: number;
  query_metrics: Record<string, number>;
  errors: string[];
  guideline_id: string;
  guideline_name: string;
  guideline_version: string;
  guideline_effective_date: string;
  profile_id?: string | null;
  available_submissions: number;
  in_scope_submissions: number;
  outside_scope_submissions: number;
  scope_unknown_submissions: number;
  assessed_submissions: number;
  total_submissions: number;
  applicable_submissions: number;
  not_applicable_submissions: number;
  duration_ms: number;
  tool_call_count: number;
  unresolved_fact_count: number;
  agent_mode: "openai" | "baseten" | "openai_required" | "baseten_required";
  agent_model?: string | null;
  agent_summary?: string | null;
  agent_adaptations: string[];
  agent_stop_reason?: string | null;
  unresolved_facts_by_reason: Record<string, number>;
  model_latency_ms: number;
  model_prompt_tokens: number;
  model_completion_tokens: number;
  model_valid_output_rate?: number | null;
  model_agreement_rate?: number | null;
}

export interface FailedRunDetail {
  run_id: string;
  errors: string[];
  trace: TraceEvent[];
}

export interface GuidelineSummary {
  id: string;
  name: string;
  version: string;
  effective_from: string;
  effective_to?: string | null;
  source: string;
  scope: string;
  required_fact_count: number;
  requirement_count: number;
  preference_count: number;
  investigation_profile_id?: string | null;
  allowed_tools: string[];
}

export type GuidelineOperator =
  | "equals"
  | "in"
  | "in_normalized"
  | "contains_normalized"
  | "lt"
  | "lte"
  | "gt"
  | "gte"
  | "between";

export interface GuidelineRule {
  id: string;
  name: string;
  fact: string;
  operator: GuidelineOperator;
  value: unknown;
  expected: string;
  review_values?: unknown[];
  missing_note?: string | null;
  note?: string | null;
}

export interface GuidelineFactSource {
  resource: string;
  path?: string | null;
  collection?: string | null;
  field?: string | null;
  fields?: string[];
  date_field?: string | null;
  operation?: string;
  weight_field?: string | null;
  match_values?: string[];
  window_years?: number | null;
  require_all?: boolean;
  record_filter?: Record<string, string>;
  relationship_path?: string[];
}

export interface GuidelineRequiredFact {
  id: string;
  label: string;
  source: GuidelineFactSource;
  display?: "plain" | "percent" | "money";
}

export interface GuidelineScope {
  fact: string;
  operator: GuidelineOperator;
  value: unknown;
  description: string;
  source: {
    resource: string;
    field: string;
    required?: boolean;
  };
}

export interface GuidelinePackage {
  id: string;
  name: string;
  version: string;
  effective_from: string;
  effective_to?: string | null;
  source: string;
  scope: GuidelineScope;
  required_facts: GuidelineRequiredFact[];
  requirements: GuidelineRule[];
  preferences: GuidelineRule[];
  investigation_profile_id?: string | null;
}

export interface ProfileDomain {
  id: string;
  label: string;
  fact_ids: string[];
  questions: string[];
}

export interface InvestigationProfile {
  id: string;
  name: string;
  version: string;
  source: string;
  domains: ProfileDomain[];
  source_guidance: string[];
  tool_suggestions: string[];
}

export interface HealthResponse {
  status: "ok";
  service: string;
  mode: "demo" | "live";
  federato_configured: boolean;
  openai_configured: boolean;
  baseten_configured: boolean;
  baseten_error?: string | null;
}
