/** Prompt 11 Part B: the assistant's API shapes (backend app/services/assistant/contracts.py). */

import type { PlanDiff } from "@/types/plan";

export interface Chip {
  label: string;
  value: string;
  /** The clarify question it answers; null for a suggested prompt. */
  question?: string | null;
}

export interface Question {
  id: string;
  prompt: string;
  chips: Chip[];
}

export interface OptionCard {
  course: string;
  title: string;
  credits: string | number | null;
  term_code: string | null;
  /** The Rutgers catalog's own first sentences; null when the catalog has none. */
  description: string | null;
  description_source: string | null;
  why: string[];
  requirements: string[];
  requirement_codes: string[];
  delivery: string[];
  workload_weight: string | number | null;
  contact_hours: string | number | null;
  open_status: string | null;
  /** Verbatim: "confirmed" | "historically offered". */
  offering: string | null;
  replaces: string | null;
  action_token: string | null;
}

export interface ProposalCard {
  title: string;
  change: Record<string, unknown>;
  preview_id: string;
  summary: string;
  diff: PlanDiff | Record<string, never>;
  big: boolean;
  /** confirm | second_tap | refreshed */
  reason: string;
  action_token: string | null;
}

export interface SectionCard {
  course: string;
  title: string;
  term_code: string;
  index_number: string;
  section_number: string;
  campus: string | null;
  instructors: string[];
  meetings: string[];
  open_status: string;
  delivery: string | null;
  warnings: string[];
}

export interface Activity { tool: string; label: string }

export interface AssistantReply {
  text: string;
  intent: string;
  questions: Question[];
  cards: OptionCard[];
  proposals: ProposalCard[];
  sections: SectionCard[];
  suggestions: Chip[];
  activity: Activity[];
  webreg_term: string | null;
  applied_summary: string | null;
  undo_available: boolean;
  generated_by: string;
  footer: string;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reply: AssistantReply | null;
  created_at: string | null;
}

export interface Conversation {
  id: string; title: string; archived: boolean; created_at: string; updated_at: string;
}

export interface AssistantStatus {
  user_key?: string;
  conversation_id?: string;
  conversations?: Conversation[];
  enabled: boolean;
  reason: string | null;
  daily_limit: number;
  /** Longest message the API accepts (ASSISTANT_MAX_INPUT_CHARS). */
  max_input_chars?: number;
  remaining_today: number;
  suggestions: Chip[];
  history: HistoryMessage[];
  footer: string;
}

export type StreamEvent =
  | { type: "activity"; label: string }
  | { type: "reply"; reply: AssistantReply; remaining_today: number }
  | { type: "error"; status: number; detail: string };

export interface MessageBody {
  conversation_id?: string;
  context_term?: string;
  builder_selection?: string[];
  text?: string;
  chip?: Chip;
  answers?: Record<string, string>;
}

export interface WebRegItem {
  course: string;
  index_number: string;
  section_number: string | null;
  title: string;
  meetings: string[];
  open_status: string;
  warnings: string[];
}

export interface WebRegPanel {
  term_code: string;
  label: string | null;
  items: WebRegItem[];
  index_numbers: string[];
  without_section: { course: string; title: string }[];
  warnings: string[];
  steps: string[];
  webreg_url: string;
  note: string;
}
