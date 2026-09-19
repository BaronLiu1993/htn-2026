"use client";

import { useState } from "react";
import { Bell, ChevronDown, LayoutPanelLeft, PanelRight, Search } from "lucide-react";
import { modelModes } from "../lib/config";
import { benchmarks, findings, ledgerEvents, submissions } from "../lib/mock-data";
import { SubmissionQueue } from "./SubmissionQueue";
import { UnderwritingWorkspace } from "./UnderwritingWorkspace";
import { EvidenceLedger } from "./EvidenceLedger";
import { PerformanceBenchmarks } from "./PerformanceBenchmarks";

export function UnderwritingWorkbench() {
  const [selectedId, setSelectedId] = useState(submissions[0].id);
  const [model, setModel] = useState<(typeof modelModes)[number]["id"]>("frontier");
  const [running, setRunning] = useState(false);
  const selected = submissions.find((submission) => submission.id === selectedId) ?? submissions[0];
  const runAnalysis = () => { setRunning(true); window.setTimeout(() => setRunning(false), 1400); };

  return <div className="app-shell"><header className="topbar"><div className="brand"><span className="brand-mark" /><span>Atlas Underwriting</span><span className="workspace-name">Submission workspace</span></div><div className="topbar-actions"><button className="search-button"><Search size={16} /><span>Search submissions</span><kbd>⌘ K</kbd></button><button className="icon-button" aria-label="Notifications"><Bell size={17} /></button><button className="avatar-button" aria-label="Account menu">AP</button></div></header>
    <div className="workbench-bar"><div className="view-name"><LayoutPanelLeft size={16} /><span>Submission review</span></div><div className="model-control" aria-label="Model mode">{modelModes.map((mode) => <button className={model === mode.id ? "selected" : ""} key={mode.id} onClick={() => setModel(mode.id)}><span>{mode.label}</span><small>{mode.detail}</small></button>)}</div><button className="inspector-button"><PanelRight size={16} />Inspector<ChevronDown size={14} /></button></div>
    <div className="app-grid"><SubmissionQueue submissions={submissions} selectedId={selectedId} onSelect={setSelectedId} /><UnderwritingWorkspace submission={selected} findings={findings} running={running} onRun={runAnalysis} /><aside className="inspector"><EvidenceLedger events={ledgerEvents} /><PerformanceBenchmarks benchmarks={benchmarks} model={model} /></aside></div>
  </div>;
}
