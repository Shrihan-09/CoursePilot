/**
 * Browser-side API client (Phase 7.1).
 *
 * Every call goes to the same-origin proxy (/api/cp/...), which attaches the
 * credential from the sealed session cookie. This module never sees a token,
 * never sends an account or student id, and never decides anything academic:
 * it returns what the API computed.
 */

import type {
  ApiErrorBody,
  Entry,
  ImportDetail,
  ImportSummary,
  Issue,
  Me,
  Onboarding,
  Profile,
  Program,
} from "@/types/onboarding";
import type { DegreeAudit, ScenarioAudit, ScenarioComparison } from "@/types/degree";
import type { PlanningConstraints, PlanningContext, SavedPlan } from "@/types/plan";
import type { ScheduleRequest, ScheduleResult } from "@/types/schedule";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly issues: Issue[],
    readonly requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/cp${path}`, {
    ...init,
    headers: { accept: "application/json", ...init?.headers },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 401 && typeof window !== "undefined") {
    // Session over (the ID token expired): sign in again and come back here.
    const here = `${window.location.pathname}${window.location.search}`;
    window.location.assign(`/auth/login?returnTo=${encodeURIComponent(here)}`);
  }
  if (!response.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // not JSON: keep the generic message below
    }
    throw new ApiError(
      body?.detail ?? `Request failed (${response.status})`,
      response.status,
      body?.error?.code ?? "internal_error",
      body?.error?.issues ?? [],
      body?.error?.request_id ?? response.headers.get("x-request-id"),
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => request<Me>("/me"),
  onboarding: () => request<Onboarding>("/student/onboarding"),
  programs: () => request<{ programs: Program[] }>("/programs"),
  createProfile: (program_key: string, catalog_year: string) =>
    request<{ created: boolean; profile: Profile }>(
      "/student/profile", json("POST", { program_key, catalog_year })),
  profile: () => request<Profile>("/student/profile"),
  changeProgram: (program_key: string, catalog_year: string) =>
    request<Profile>("/student/program", json("PUT", { program_key, catalog_year })),
  completeOnboarding: () => request<Profile>("/student/onboarding/complete", { method: "POST" }),

  courses: () => request<{ entries: Entry[] }>("/student/courses"),
  addCourse: (body: Record<string, string | null>) =>
    request<Entry>("/student/courses", json("POST", body)),
  deleteCourse: (id: string) => request<void>(`/student/courses/${id}`, { method: "DELETE" }),

  uploadTranscript: (file: File) =>
    request<ImportDetail>("/student/transcripts", {
      method: "POST",
      headers: { "content-type": "application/pdf" },
      body: file,
    }),
  imports: () => request<{ imports: ImportSummary[] }>("/student/transcripts"),
  transcript: (id: string) => request<ImportDetail>(`/student/transcripts/${id}`),
  editRow: (id: string, rowId: string, body: Record<string, unknown>) =>
    request<ImportDetail>(`/student/transcripts/${id}/rows/${rowId}`, json("PATCH", body)),
  confirmTranscript: (id: string, expected_version: number) =>
    request<ImportDetail>(`/student/transcripts/${id}/confirm`,
                          json("POST", { expected_version })),
  discardTranscript: (id: string) =>
    request<ImportSummary>(`/student/transcripts/${id}`, { method: "DELETE" }),

  /* Phase 7.2 - each returns an engine's result unchanged. */
  audit: () => request<DegreeAudit>("/student/audit"),
  scenarioAudit: (program_key: string, catalog_year: string | null) =>
    request<ScenarioAudit>("/student/scenarios/audit", json("POST", { program_key, catalog_year })),
  scenarioCompare: (program_key: string, catalog_year: string | null) =>
    request<ScenarioComparison>("/student/scenarios/compare",
                                json("POST", { program_key, catalog_year })),

  planningContext: () => request<PlanningContext>("/student/plans/context"),
  currentPlan: () => request<{ plan: SavedPlan | null }>("/student/plans/current"),
  savePlan: (start_term: string, constraints: PlanningConstraints) =>
    request<SavedPlan>("/student/plans", json("POST", { start_term, constraints })),
  deletePlan: () => request<void>("/student/plans/current", { method: "DELETE" }),

  schedules: (body: ScheduleRequest) =>
    request<ScheduleResult>("/student/schedules/generate", json("POST", body)),
};
