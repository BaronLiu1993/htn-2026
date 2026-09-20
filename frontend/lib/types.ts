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
  submission_id: string;
  submission_number: string;
  insured_name: string;
  received_date?: string | null;
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
  scope_status: "applicable" | "not_applicable" | "not_evaluated";
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
  errors: string[];
  guideline_id: string;
  guideline_name: string;
  guideline_version: string;
  guideline_effective_date: string;
  profile_id?: string | null;
  total_submissions: number;
  applicable_submissions: number;
  not_applicable_submissions: number;
  scope_unknown_submissions: number;
  duration_ms: number;
  tool_call_count: number;
  unresolved_fact_count: number;
  agent_mode: "openai" | "baseten" | "openai_required" | "baseten_required";
  agent_model?: string | null;
  agent_summary?: string | null;
  agent_adaptations: string[];
  model_latency_ms: number;
  model_prompt_tokens: number;
  model_completion_tokens: number;
  model_valid_output_rate?: number | null;
  model_agreement_rate?: number | null;
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
