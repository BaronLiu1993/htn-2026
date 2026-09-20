import type {
  AnalysisRun,
  GuidelineSummary,
  HealthResponse,
  InvestigationProfile,
  ModelProvider,
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
        const detail = payload.detail as { errors?: unknown };
        if (
          Array.isArray(detail.errors) &&
          detail.errors.every((error) => typeof error === "string")
        ) {
          message = detail.errors.join(" ");
        } else {
          message = "The analysis failed. Review the backend run details.";
        }
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

export function fetchGuidelines() {
  return request<GuidelineSummary[]>("/api/guidelines");
}

export function fetchProfiles() {
  return request<InvestigationProfile[]>("/api/profiles");
}

export function analyzeSubmissions(
  guideline: Pick<GuidelineSummary, "id" | "version">,
  submissionIds?: string[],
  modelProvider: ModelProvider = "openai",
) {
  return request<AnalysisRun>("/api/analysis/batch", {
    method: "POST",
    body: JSON.stringify({
      submission_ids: submissionIds?.length ? submissionIds : null,
      guideline_id: guideline.id,
      guideline_version: guideline.version,
      model_provider: modelProvider,
    }),
  });
}
