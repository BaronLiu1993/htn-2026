import type {
  AnalysisRun,
  FailedRunDetail,
  GuidelinePackage,
  GuidelineSummary,
  HealthResponse,
  InvestigationProfile,
  ModelProvider,
  QueueSubmission,
} from "./types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://localhost:8000";

export class AnalysisRunError extends Error {
  readonly detail: FailedRunDetail;

  constructor(detail: FailedRunDetail) {
    super(detail.errors.join(" · ") || `Analysis run ${detail.run_id} failed.`);
    this.name = "AnalysisRunError";
    this.detail = detail;
  }
}

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
      const payload = (await response.json()) as {
        detail?: unknown;
      };
      if (typeof payload.detail === "string") message = payload.detail;
      if (payload.detail && typeof payload.detail === "object") {
        const detail = payload.detail as Partial<FailedRunDetail>;
        if (
          typeof detail.run_id === "string" &&
          Array.isArray(detail.errors) &&
          Array.isArray(detail.trace)
        ) {
          throw new AnalysisRunError(detail as FailedRunDetail);
        }
        message = JSON.stringify(payload.detail);
      }
    } catch (error) {
      if (error instanceof AnalysisRunError) throw error;
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

export function fetchGuideline(guidelineId: string, version?: string | null) {
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  return request<GuidelinePackage>(`/api/guidelines/${encodeURIComponent(guidelineId)}${query}`);
}

export function fetchProfiles() {
  return request<InvestigationProfile[]>("/api/profiles");
}

export function analyzeSubmissions(
  guideline: Pick<GuidelineSummary, "id" | "version">,
  modelProvider: ModelProvider = "openai",
) {
  return request<AnalysisRun>("/api/analysis/batch", {
    method: "POST",
    body: JSON.stringify({
      submission_ids: null,
      guideline_id: guideline.id,
      guideline_version: guideline.version,
      model_provider: modelProvider,
    }),
  });
}
