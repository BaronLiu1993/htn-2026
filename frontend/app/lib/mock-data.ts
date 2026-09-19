import type { AgentEvent, Benchmark, Submission, UnderwritingFinding } from "./contracts";

export const submissions: Submission[] = [
  { id: "SUB-20841", insured: "Northline Foods LLC", line: "Commercial property", location: "Fresno, CA", premium: 184200, tiv: 12800000, riskScore: 72, appetite: "needs review", missing: 2, sla: "01:42", underwriter: "A. Patel", agentState: "Evidence complete", statusTone: "warning", updatedAt: "8m ago" },
  { id: "SUB-20839", insured: "Ridgeview Warehousing", line: "Commercial property", location: "Reno, NV", premium: 246500, tiv: 19700000, riskScore: 84, appetite: "within appetite", missing: 0, sla: "03:18", underwriter: "M. Chen", agentState: "Ready for decision", statusTone: "success", updatedAt: "12m ago" },
  { id: "SUB-20838", insured: "Harbor Street Retail", line: "BOP", location: "Tampa, FL", premium: 96800, tiv: 6300000, riskScore: 41, appetite: "outside appetite", missing: 1, sla: "00:35", underwriter: "J. Williams", agentState: "Escalated", statusTone: "danger", updatedAt: "18m ago" },
  { id: "SUB-20834", insured: "Summit Fabrication", line: "Commercial property", location: "Boise, ID", premium: 131400, tiv: 9100000, riskScore: 79, appetite: "within appetite", missing: 0, sla: "04:04", underwriter: "A. Patel", agentState: "Running checks", statusTone: "info", updatedAt: "23m ago" },
  { id: "SUB-20831", insured: "Cobalt Auto Group", line: "Garage", location: "Mesa, AZ", premium: 222100, tiv: 16100000, riskScore: 63, appetite: "needs review", missing: 3, sla: "02:10", underwriter: "S. Lee", agentState: "Awaiting documents", statusTone: "warning", updatedAt: "31m ago" },
];

export const findings: UnderwritingFinding[] = [
  { label: "Wildfire exposure", detail: "Moderate exposure, 1.8 mi from high-risk zone", tone: "warning" },
  { label: "Roof condition", detail: "2018 TPO roof; no open inspection issues", tone: "success" },
  { label: "Protection class", detail: "Class 3, hydrant within 500 ft", tone: "success" },
  { label: "Loss history", detail: "Two water losses in five years", tone: "warning" },
];

export const ledgerEvents: AgentEvent[] = [
  { id: "evt_01J9M4", timestamp: "10:42:13", type: "intake", status: "complete", summary: "Parsed ACORD application and 14 submitted documents.", duration: "2.1s", metadata: { documentsProcessed: 14, extractionVersion: "2.4.1" } },
  { id: "evt_01J9M5", timestamp: "10:42:16", type: "tool", status: "complete", summary: "Enriched the property location with hazard and protection data.", duration: "3.8s", tool: { name: "property_enrichment", input: { address: "1248 E Central Ave, Fresno, CA" }, output: { wildfireScore: 54, protectionClass: 3, roofYear: 2018 }, metadata: { provider: "RiskMap", sourceVersion: "2026.03" } }, evidence: [{ label: "Hazard profile", source: "RiskMap", confidence: 0.96 }, { label: "Protection class", source: "ISO", confidence: 0.92 }] },
  { id: "evt_01J9M6", timestamp: "10:42:20", type: "tool", status: "complete", summary: "Compared the risk against commercial property appetite v3.2.", duration: "1.4s", tool: { name: "appetite_guidelines", input: { program: "Commercial Property", version: "3.2" }, output: { result: "refer", rulesMatched: ["WF-17", "LH-04"] } }, evidence: [{ label: "Appetite guideline v3.2", source: "Carrier policy library", confidence: 1 }] },
  { id: "evt_01J9M7", timestamp: "10:42:22", type: "tool", status: "complete", summary: "Retrieved prior loss signals and normalized loss dates.", duration: "1.1s", tool: { name: "loss_history", input: { entity: "Northline Foods LLC" }, output: { claims: 2, incurred: 84000 } }, evidence: [{ label: "Five-year loss run", source: "Claims exchange", confidence: 0.88 }] },
  { id: "evt_01J9M8", timestamp: "10:42:24", type: "recommendation", status: "complete", summary: "Generated a conditional referral recommendation.", duration: "0.7s", metadata: { model: "frontier-v1", confidence: 0.86, decision: "refer" } },
];

export const benchmarks: Benchmark[] = [
  { id: "agreement", label: "Decision agreement", value: "91.8%", change: "+2.4 pts", direction: "up", tone: "success", detail: "vs. senior underwriter labels" },
  { id: "grounding", label: "Evidence grounding", value: "97.2%", change: "+0.8 pts", direction: "up", tone: "success", detail: "claims linked to source evidence" },
  { id: "latency", label: "Run latency", value: "8.7s", change: "-1.3s", direction: "up", tone: "info", detail: "p50 across comparable property submissions" },
  { id: "cost", label: "Estimated cost", value: "$0.42", change: "-38%", direction: "up", tone: "neutral", detail: "frontier model estimate" },
];
