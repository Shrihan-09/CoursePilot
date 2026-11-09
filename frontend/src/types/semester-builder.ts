import type { PrerequisiteEvidence } from "./plan";

export interface BrowserRequest {
  term_code: string; selection?: string[]; text?: string; requirements?: string[][]; still_needed?: boolean; degree_credit_only?: boolean;
  delivery?: "any" | "hybrid"; sort?: "recommended" | "name" | "credits" | "fills"; offset?: number; limit?: number;
  filters?: { online?: boolean; async_only?: boolean; in_person?: boolean; campus?: string; earliest?: string; latest?: string; avoid_days?: string[]; open_only?: boolean; level?: number; credits?: number };
}
export interface BrowserCourse {
  course: string; title: string; credits: string | null; workload_weight: string | null;
  requirements: string[]; requirement_codes: string[]; coverage: string; coverage_label: string;
  delivery: string[]; open_status: string; can_add: boolean; can_pin: boolean; gate: string | null; selected: boolean;
  prerequisite: PrerequisiteEvidence | null; recommended: boolean; similar_to: string | null; planned_term: string | null;
}
export interface BrowserResult {
  term_code: string; total_offered: number; matching: number; eligible: number; gated: number; selected: number; offset: number;
  items: BrowserCourse[]; requirements: { code: string; name: string }[]; membership_source: string;
  interpretation?: { did_you_mean: { phrase: string; options: { requirements: string[]; names: string[] }[] }[]; notes: string[] };
}
