import type { ProgressResponse } from "@/types/progress";
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
import type {
  CourseSections,
  CurrentSchedule,
  PlanningProfile,
  PlanningProfileInput,
  PlanningProfileResponse,
  Readiness,
} from "@/types/personalization";
import type { PlanningContext, SavedPlan } from "@/types/plan";
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
  scheduleImports: () => request<{imports: {id: string; state: string; expires_at: string}[]}>("/student/schedule-imports"),
  uploadSchedule: (file: File) => request<import("@/types/schedule-import").ScheduleImport>("/student/schedule-imports", {method: "POST", headers: {"content-type": "application/pdf"}, body: file}),
  scheduleImport: (id: string) => request<import("@/types/schedule-import").ScheduleImport>(`/student/schedule-imports/${id}`),
  editScheduleRow: (id: string, rowId: string, body: Record<string, unknown>) => request<import("@/types/schedule-import").ScheduleImport>(`/student/schedule-imports/${id}/rows/${rowId}`, json("PATCH", body)),
  confirmSchedule: (id: string, expected_version: number) => request<import("@/types/schedule-import").ScheduleImport>(`/student/schedule-imports/${id}/confirm`, json("POST", {expected_version})),
  discardSchedule: (id: string) => request<import("@/types/schedule-import").ScheduleImport>(`/student/schedule-imports/${id}`, {method: "DELETE"}),
  registrations: () => request<{registrations: import("@/types/schedule-import").Registration[]}>("/student/registrations"),
  me: () => request<Me>("/me"),
  onboarding: () => request<Onboarding>("/student/onboarding"),
  programs: () => request<{ programs: Program[] }>("/programs"),
  createProfile: (program_key: string, catalog_year: string) =>
    request<{ created: boolean; profile: Profile }>(
      "/student/profile", json("POST", { program_key, catalog_year })),
  profile: () => request<Profile>("/student/profile"),
  changeProgram: (program_key: string, catalog_year: string) =>
    request<Profile>("/student/program", json("PUT", { program_key, catalog_year })),
  /** Prompt 10a: wizard progress; omit firstTerm to leave it unchanged. */
  saveSetup: (completed_steps: string[], firstTerm?: string | null,
    classStanding?: import("@/types/onboarding").ClassStanding | null) =>
    request<{ steps: string[]; completed_steps: string[] }>("/student/onboarding/setup",
      json("PUT", {
        completed_steps,
        ...(firstTerm === undefined ? {} : { first_term: firstTerm }),
        ...(classStanding === undefined ? {} : { class_standing: classStanding }),
      })),
  /** 10a follow-up: the "Your grades are in" Home reminder. */
  gradesReminder: () => request<import("@/types/onboarding").GradesReminder>("/student/grades-reminder"),
  dismissGradesReminder: () =>
    request<import("@/types/onboarding").GradesReminder>("/student/grades-reminder/dismiss", { method: "POST" }),
  /** 10a follow-up: the retakes the planner detects and when each could be taken. */
  retakes: () => request<import("@/types/onboarding").RetakesResponse>("/student/plans/retakes"),
  completeOnboarding: () => request<Profile>("/student/onboarding/complete", { method: "POST" }),
  setPlacement: (math_placement: string | null) =>
    request<Profile>("/student/placement", json("PUT", { math_placement })),
  setDeclaration: (declaration_status: "declared" | "undeclared" | null) =>
    request<Profile>("/student/declaration", json("PUT", { declaration_status })),

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
  // Phase 7.4: Degree Navigator audit import.
  dnImports: () => request<{ imports: import("@/types/degree-audit").DnImportSummary[] }>("/degree-audit-imports"),
  uploadDegreeAudit: (file: File) =>
    request<{ imports: import("@/types/degree-audit").DnImportDetail[] }>("/degree-audit-imports", {
      method: "POST",
      headers: { "content-type": "application/pdf" },
      body: file,
    }),
  dnImport: (id: string) => request<import("@/types/degree-audit").DnImportDetail>(`/degree-audit-imports/${id}`),
  resolveDnRow: (id: string, rowId: string, body: Record<string, unknown>) =>
    request<import("@/types/degree-audit").DnImportDetail>(`/degree-audit-imports/${id}/rows/${rowId}`, json("PATCH", body)),
  /** Task 3: scope "what_if" - a block for another program, used only by that program's what-if plans. */
  confirmDn: (id: string, expected_version: number, scope: "primary" | "what_if" = "primary") =>
    request<import("@/types/degree-audit").DnImportDetail>(`/degree-audit-imports/${id}/confirm`, json("POST", { expected_version, scope })),
  discardDn: (id: string) => request<import("@/types/degree-audit").DnImportSummary>(`/degree-audit-imports/${id}`, { method: "DELETE" }),
  dnProgress: () => request<import("@/types/degree-audit").DnProgress>("/degree-audit-imports/progress"),
  progress: () => request<ProgressResponse>("/student/progress"),
  scenarioAudit: (program_key: string, catalog_year: string | null) =>
    request<ScenarioAudit>("/student/scenarios/audit", json("POST", { program_key, catalog_year })),
  scenarioCompare: (program_key: string, catalog_year: string | null) =>
    request<ScenarioComparison>("/student/scenarios/compare",
                                json("POST", { program_key, catalog_year })),

  planningContext: () => request<PlanningContext>("/student/plans/context"),
  currentPlan: () => request<{ plan: SavedPlan | null }>("/student/plans/current"),
  /** Load settings come from the CONFIRMED planning profile, never the request (7.2.1). */
  savePlan: (start_term?: string) =>
    request<SavedPlan>("/student/plans", json("POST", start_term ? { start_term } : {})),
  deletePlan: () => request<void>("/student/plans/current", { method: "DELETE" }),
  /** Prompt 11 Part A: options, preview -> apply (revision-checked), undo. */
  courseOptions: (course: string, term: string) =>
    request<import("@/types/plan").CourseOptions>(`/student/plans/current/courses/${course.replace(/:/g, "-")}/${term}/options`),
  previewChange: (change: import("@/types/plan").PlanChange) =>
    request<import("@/types/plan").PlanPreview>("/student/plans/current/changes/preview", json("POST", change)),
  applyChange: (preview_id: string) =>
    request<SavedPlan>("/student/plans/current/changes/apply", json("POST", { preview_id })),
  undoChange: () => request<SavedPlan>("/student/plans/current/undo", { method: "POST" }),
  planChoices: () => request<import("@/types/plan").Choices>("/student/plans/current/choices"),
  searchCourses: (body: { text?: string; term_code?: string | null; requirements?: string[][] | null; topic?: string | null; limit?: number;
    filters?: { online?: boolean; async_only?: boolean; in_person?: boolean; campus?: string; earliest?: string; latest?: string;
      avoid_days?: string[]; level?: number; credits?: number; open_only?: boolean; fills_two?: boolean } }) =>
    request<import("@/types/plan").CourseSearchResult>("/student/plans/current/search", json("POST", body)),
  setDoingWell: (course: string, term: string, doing_well: boolean) =>
    request<void>(`/student/courses/${course.replace(/:/g, "-")}/${term}/doing-well`, json("PUT", { doing_well })),
  personalization: () => request<{ use_grades_for_suggestions: boolean }>("/student/plans/personalization"),
  setPersonalization: (use_grades_for_suggestions: boolean) =>
    request<{ use_grades_for_suggestions: boolean }>("/student/plans/personalization", json("PUT", { use_grades_for_suggestions })),

  schedules: (body: ScheduleRequest) =>
    request<ScheduleResult>("/student/schedules/generate", json("POST", body)),

  /* Phase 7.2.1 */
  planningProfile: () => request<PlanningProfileResponse>("/student/planning-profile"),
  confirmPlanningProfile: (body: PlanningProfileInput) =>
    request<PlanningProfile>("/student/planning-profile", json("PUT", body)),
  readiness: () => request<Readiness>("/student/planning-readiness"),
  currentSchedule: () => request<CurrentSchedule>("/student/current-schedule"),
  courseSections: (course: string) =>
    request<CourseSections>(`/student/current-schedule/sections?course=${encodeURIComponent(course)}`),
  confirmSection: (course: string, index_number: string) =>
    request<CurrentSchedule>("/student/current-schedule/sections", json("PUT", { course, index_number })),
  forgetSection: (course: string) =>
    request<void>(`/student/current-schedule/sections?course=${encodeURIComponent(course)}`,
                  { method: "DELETE" }),

  /* Prompt 11 Part B: the assistant (messages stream via lib/assistant.ts) */
  assistantStatus: (conversationId?: string) => request<import("@/types/assistant").AssistantStatus>(
    `/student/assistant${conversationId ? `?conversation_id=${conversationId}` : ""}`),
  newAssistantConversation: () => request<import("@/types/assistant").Conversation>(
    "/student/assistant/conversations", { method: "POST" }),
  editAssistantConversation: (id: string, body: { title?: string; archived?: boolean }) =>
    request<import("@/types/assistant").Conversation>(`/student/assistant/conversations/${id}`,
      json("PATCH", body)),
  deleteAssistantConversation: (id: string) => request<{ deleted: boolean }>(
    `/student/assistant/conversations/${id}`, { method: "DELETE" }),
  /** A tap on "Choose this" / "Confirm": the card's one-time token. */
  assistantAction: (token: string) =>
    request<{ reply: import("@/types/assistant").AssistantReply }>("/student/assistant/actions",
                                                                  json("POST", { token })),
  deleteAssistantHistory: () =>
    request<{ deleted_messages: number }>("/student/assistant/history", { method: "DELETE" }),
  webregPanel: (term: string) =>
    request<import("@/types/assistant").WebRegPanel>(`/student/plans/current/terms/${term}/webreg`),
  choosePlannedSection: (term: string, course: string, index_number: string) =>
    request<import("@/types/assistant").WebRegPanel>(`/student/plans/current/terms/${term}/sections`,
                                                    json("PUT", { course, index_number })),
  forgetPlannedSection: (term: string, course: string) =>
    request<import("@/types/assistant").WebRegPanel>(
      `/student/plans/current/terms/${term}/sections/${course.replace(/:/g, "-")}`, { method: "DELETE" }),
};
