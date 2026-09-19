export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral";

export type Submission = {
  id: string;
  insured: string;
  line: string;
  location: string;
  premium: number;
  tiv: number;
  riskScore: number;
  appetite: "within appetite" | "needs review" | "outside appetite";
  missing: number;
  sla: string;
  underwriter: string;
  agentState: string;
  statusTone: StatusTone;
  updatedAt: string;
};

export type FlexibleRecord = Record<string, unknown>;

export type AgentEvent = {
  id: string;
  timestamp: string;
  type: string;
  status: string;
  summary: string;
  duration?: string;
  tool?: {
    name: string;
    input?: unknown;
    output?: unknown;
    metadata?: FlexibleRecord;
  };
  evidence?: Array<{
    label: string;
    source: string;
    confidence?: number;
  }>;
  metadata?: FlexibleRecord;
};

export type Benchmark = {
  id: string;
  label: string;
  value: string;
  change?: string;
  direction?: "up" | "down" | "neutral";
  tone: StatusTone;
  detail: string;
  metadata?: FlexibleRecord;
};

export type UnderwritingFinding = {
  label: string;
  detail: string;
  tone: StatusTone;
};
