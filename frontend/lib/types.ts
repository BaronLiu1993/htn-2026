export type AssessmentStatus =
  | "target"
  | "acceptable"
  | "needs_review"
  | "out_of_appetite";

export type AnalysisStatus = AssessmentStatus | "not_analyzed";

export interface EvidenceItem {
  resource: string;
  record_id: string;
  field: string;
  value: unknown;
  label: string;
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
  category: "construction" | "occupancy" | "protection" | "exposure";
  status: "available" | "partial" | "missing";
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
  cope: UnderwritingConsideration[];
  warnings: string[];
  run_id: string;
  appetite_id: string;
  appetite_version: string;
  appetite_effective_date: string;
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
  errors: string[];
  appetite_id: string;
  appetite_version: string;
  appetite_effective_date: string;
  agent_mode: "openai" | "deterministic_fallback";
  agent_model?: string | null;
  agent_summary?: string | null;
  agent_adaptations: string[];
}

export interface AppetiteStatus {
  id: string;
  name: string;
  version: string;
  effective_from: string;
  source: string;
  requirement_count: number;
  preference_count: number;
}

export interface HealthResponse {
  status: "ok";
  service: string;
  mode: "demo" | "live";
  federato_configured: boolean;
  openai_configured: boolean;
}
