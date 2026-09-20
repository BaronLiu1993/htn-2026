import type { Assessment } from "./types";

export type FileRole = "current" | "requote" | "prior";

export type AccountGroup = {
  key: string;
  current: Assessment;
  files: Assessment[];
};

function recency(item: Assessment): number {
  const value = item.effective_date ?? item.received_date;
  if (!value) return 0;
  const time = Date.parse(value);
  return Number.isNaN(time) ? 0 : time;
}

export function accountKey(item: Assessment): string {
  const name = item.insured_name.trim().toLowerCase();
  if (!name || name === "unnamed account") {
    return item.insured_id ? `id:${item.insured_id}` : `sub:${item.submission_id}`;
  }
  return `name:${name}|${(item.primary_state ?? "").toLowerCase()}`;
}

export function groupAccounts(assessments: Assessment[]): AccountGroup[] {
  const rank = new Map(assessments.map((item, index) => [item.submission_id, index]));
  const buckets = new Map<string, Assessment[]>();
  for (const item of assessments) {
    const key = accountKey(item);
    const files = buckets.get(key);
    if (files) files.push(item);
    else buckets.set(key, [item]);
  }
  return [...buckets.entries()]
    .map(([key, files]) => {
      const sorted = [...files].sort((left, right) => {
        const delta = recency(right) - recency(left);
        if (delta !== 0) return delta;
        return right.submission_number.localeCompare(left.submission_number);
      });
      return { key, current: sorted[0], files: sorted };
    })
    .sort(
      (left, right) =>
        (rank.get(left.current.submission_id) ?? 0) - (rank.get(right.current.submission_id) ?? 0),
    );
}

export function fileRole(file: Assessment, current: Assessment): FileRole {
  if (file.submission_id === current.submission_id) return "current";
  const fileDate = file.effective_date ?? file.received_date;
  const currentDate = current.effective_date ?? current.received_date;
  if (fileDate && currentDate && fileDate.slice(0, 4) === currentDate.slice(0, 4)) return "requote";
  return "prior";
}

export function roleLabel(role: FileRole): string {
  if (role === "current") return "Current";
  if (role === "requote") return "Requote";
  return "Prior year";
}

export function matchesAccount(account: AccountGroup, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return account.files.some((item) =>
    `${item.insured_name} ${item.submission_number} ${item.primary_state ?? ""}`
      .toLowerCase()
      .includes(needle),
  );
}
