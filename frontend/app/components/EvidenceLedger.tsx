"use client";

import { useState } from "react";
import { ChevronDown, Database, FileSearch, ListTree, ScanSearch, Wrench } from "lucide-react";
import type { AgentEvent } from "../lib/contracts";
import { toolDisplay } from "../lib/config";

const eventIcon = (event: AgentEvent) => {
  if (event.type === "intake") return FileSearch;
  if (event.type === "recommendation") return ScanSearch;
  if (event.tool?.name === "loss_history") return Database;
  return event.tool ? Wrench : ListTree;
};

export function EvidenceLedger({ events }: { events: AgentEvent[] }) {
  const [expanded, setExpanded] = useState<string | null>("evt_01J9M5");
  return <section className="ledger-panel" aria-labelledby="ledger-heading"><div className="inspector-title"><div><p className="eyebrow">Append-only run trace</p><h2 id="ledger-heading">Evidence ledger</h2></div><span className="event-count">{events.length} events</span></div>
    <div className="ledger-list">{events.map((event, index) => {
      const Icon = eventIcon(event); const open = expanded === event.id; const display = event.tool ? toolDisplay[event.tool.name] : undefined;
      return <article className={`ledger-event ${open ? "open" : ""}`} key={event.id}><div className="ledger-rail"><span className="event-dot"><Icon size={14} /></span>{index < events.length - 1 && <span className="event-line" />}</div><div className="event-content"><button className="event-trigger" onClick={() => setExpanded(open ? null : event.id)} aria-expanded={open}><span className="event-time">{event.timestamp}</span><strong>{display?.label ?? event.type}</strong><ChevronDown size={15} /></button><p>{event.summary}</p><span className="event-meta">{event.duration ?? "recorded"} · {event.id}</span>
        {open && <div className="event-detail">{event.tool && <><p className="detail-label">Tool payload</p><pre>{JSON.stringify({ input: event.tool.input, output: event.tool.output, ...event.tool.metadata }, null, 2)}</pre></>}{event.evidence && <div className="evidence-list">{event.evidence.map((evidence) => <div key={evidence.label}><span>{evidence.label}</span><small>{evidence.source} {evidence.confidence ? `· ${Math.round(evidence.confidence * 100)}%` : ""}</small></div>)}</div>}</div>}
      </div></article>;
    })}</div>
  </section>;
}
