import { Check, Circle, Clock3, LoaderCircle, TriangleAlert, X } from "lucide-react";
import type { StatusTone } from "../lib/contracts";
import { statusLabel } from "../lib/config";

const icons = { success: Check, warning: TriangleAlert, danger: X, info: LoaderCircle, neutral: Clock3 };

export function StatusBadge({ tone, label }: { tone: StatusTone; label?: string }) {
  const Icon = icons[tone] ?? Circle;
  return <span className={`status-badge status-${tone}`}><Icon size={13} aria-hidden="true" />{label ?? statusLabel[tone]}</span>;
}
