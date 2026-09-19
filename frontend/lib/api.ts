import type {
  AnalysisRun,
  AppetiteStatus,
  HealthResponse,
  QueueSubmission,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
      if (payload.detail && typeof payload.detail === "object") {
        message = JSON.stringify(payload.detail);
      }
    } catch {
      // Preserve the status-based message if the response is not JSON.
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}

export function fetchHealth() {
  return request<HealthResponse>("/api/health");
}

export function fetchSubmissions() {
  return request<QueueSubmission[]>("/api/submissions");
}

export function fetchAppetiteStatus() {
  return request<AppetiteStatus>("/api/appetite/status");
}

export function analyzeSubmissions(submissionIds?: string[]) {
  return request<AnalysisRun>("/api/analysis/batch", {
    method: "POST",
    body: JSON.stringify({
      submission_ids: submissionIds?.length ? submissionIds : null,
    }),
  });
}
