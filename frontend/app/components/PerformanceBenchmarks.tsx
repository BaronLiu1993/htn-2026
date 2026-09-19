import { ArrowUpRight, BarChart3 } from "lucide-react";
import type { Benchmark } from "../lib/contracts";

export function PerformanceBenchmarks({ benchmarks, model }: { benchmarks: Benchmark[]; model: string }) {
  return <section className="benchmark-panel" aria-labelledby="benchmark-heading"><div className="inspector-title"><div><p className="eyebrow">Comparable run cohort</p><h2 id="benchmark-heading">Performance benchmarks</h2></div><BarChart3 size={17} /></div><p className="benchmark-context">{model === "frontier" ? "Frontier model" : "Distilled model"} · property underwriting</p><div className="benchmark-grid">{benchmarks.map((benchmark) => <article className="benchmark-card" key={benchmark.id}><span>{benchmark.label}</span><strong>{benchmark.value}</strong><div className={`benchmark-change ${benchmark.tone}`}><ArrowUpRight size={13} />{benchmark.change ?? "Baseline"}</div><small>{benchmark.detail}</small></article>)}</div></section>;
}
