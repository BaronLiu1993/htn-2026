import type { Submission } from "../lib/contracts";
import { StatusBadge } from "./StatusBadge";

export function SubmissionQueue({ submissions, selectedId, onSelect }: { submissions: Submission[]; selectedId: string; onSelect: (id: string) => void }) {
  return <aside className="queue-panel" aria-label="Submission queue">
    <div className="panel-heading"><div><p className="eyebrow">Active queue</p><h2>Submissions <span>{submissions.length}</span></h2></div><button className="icon-button" aria-label="Filter submissions">+</button></div>
    <div className="queue-filters"><button className="filter active">All <span>24</span></button><button className="filter">Review <span>8</span></button><button className="filter">Referred <span>5</span></button></div>
    <div className="queue-table" role="list">
      {submissions.map((submission) => <button key={submission.id} className={`submission-row ${selectedId === submission.id ? "selected" : ""}`} onClick={() => onSelect(submission.id)} role="listitem">
        <div className="submission-row-top"><span className="submission-id">{submission.id}</span><span className="row-time">{submission.updatedAt}</span></div>
        <strong>{submission.insured}</strong><span className="submission-line">{submission.line} · {submission.location}</span>
        <div className="submission-metrics"><span>${(submission.premium / 1000).toFixed(0)}k prem.</span><span>${(submission.tiv / 1000000).toFixed(1)}m TIV</span><span>Risk {submission.riskScore}</span></div>
        <div className="submission-footer"><StatusBadge tone={submission.statusTone} label={submission.appetite} /><span className="sla">SLA {submission.sla}</span></div>
      </button>)}
    </div>
  </aside>;
}
