import type { StatusTone } from "./contracts";

export const modelModes = [
  { id: "frontier", label: "Frontier model", detail: "Deep analysis" },
  { id: "distilled", label: "Distilled model", detail: "Fast path" },
] as const;

export const toolDisplay: Record<string, { label: string; kind: "search" | "file" | "database" | "policy" }> = {
  property_enrichment: { label: "Property enrichment", kind: "search" },
  appetite_guidelines: { label: "Appetite guidelines", kind: "policy" },
  loss_history: { label: "Loss history", kind: "database" },
};

export const statusLabel: Record<StatusTone, string> = {
  success: "Passed",
  warning: "Needs review",
  danger: "Outside appetite",
  info: "Running",
  neutral: "Queued",
};
