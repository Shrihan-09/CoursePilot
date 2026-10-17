import { AlertTriangle, Ban, CheckCircle2, CircleHelp, GitCompareArrows, type LucideIcon } from "lucide-react";

import type { RowState } from "@/types/onboarding";

/** Every state has an icon AND words - never colour alone. */
export const ROW_STATE: Record<
  RowState,
  { label: string; tone: "success" | "warning" | "danger" | "neutral"; icon: LucideIcon; help: string }
> = {
  matched: {
    label: "Recognized",
    tone: "success",
    icon: CheckCircle2,
    help: "Matches a Rutgers course and can be saved as shown.",
  },
  needs_review: {
    label: "Needs your review",
    tone: "warning",
    icon: AlertTriangle,
    help: "Something wasn't stated on the transcript or couldn't be read. Only you can confirm it.",
  },
  unrecognized: {
    label: "Not recognized",
    tone: "neutral",
    icon: CircleHelp,
    help: "CoursePilot has no course with this code. Correct it or leave it out.",
  },
  conflict: {
    label: "Conflicts with your record",
    tone: "danger",
    icon: GitCompareArrows,
    help: "You already have this course in this term with different details. Choose which to keep.",
  },
  invalid: {
    label: "Can't be saved",
    tone: "danger",
    icon: Ban,
    help: "A value here isn't valid. Correct it or leave the row out.",
  },
};

const SEASON: Record<string, string> = { "0": "Winter", "1": "Spring", "7": "Summer", "9": "Fall" };

/** "20259" -> "Fall 2025". Unknown shapes are shown as-is, never guessed. */
export function termLabel(code: string | null | undefined): string {
  if (!code) return "No term";
  const m = /^(\d{4})([0179])$/.exec(code);
  return m ? `${SEASON[m[2]!]} ${m[1]}` : code;
}

export function termOptions(fromYear: number, toYear: number): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  for (let y = toYear; y >= fromYear; y--) {
    for (const s of ["9", "7", "1", "0"]) out.push({ value: `${y}${s}`, label: termLabel(`${y}${s}`) });
  }
  return out;
}

export const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  in_progress: "In progress",
  planned: "Planned",
};

export const ORIGIN_LABEL: Record<string, string> = {
  rutgers: "Rutgers course",
  transfer: "Transfer credit",
  exam_credit: "AP / exam credit",
};

/** Upload refusals and extraction failures, in plain words. */
export const FAILURE: Record<string, string> = {
  FILE_EMPTY: "That file is empty.",
  FILE_TOO_LARGE: "That file is larger than 5 MB. Your unofficial transcript PDF is much smaller.",
  FILE_NOT_PDF: "That file isn't a PDF. Upload the PDF of your unofficial transcript.",
  PDF_ENCRYPTED: "This PDF is password-protected, so CoursePilot can't read it.",
  PDF_TOO_MANY_PAGES: "This PDF has more pages than a transcript should.",
  PDF_TEXT_TOO_LARGE: "This PDF has more text than a transcript should.",
  PDF_NO_TEXT_LAYER:
    "This PDF is a scan or photo, so its text can't be read. Download the PDF from Rutgers instead of scanning it.",
  PDF_UNREADABLE: "This PDF couldn't be read.",
  PDF_EXTRACTION_TIMEOUT: "Reading this PDF took too long.",
  PDF_EXTRACTION_CRASHED: "This PDF couldn't be read.",
  NO_COURSE_ROWS: "No courses were found in this PDF. Is it your Rutgers unofficial transcript?",
  TOO_MANY_ROWS: "This PDF lists more courses than a transcript should.",
  PROCESSING_INTERRUPTED: "Reading this PDF was interrupted. Please upload it again.",
};

/** Document-level warnings from the parser. */
export const DOC_WARNING: Record<string, { title: string; body: string }> = {
  READING_ORDER_UNRELIABLE: {
    title: "Parts of this transcript were read out of order",
    body: "Its columns ran together, so CoursePilot can't be sure which term each course belongs to. Every row needs your check.",
  },
  TERM_TOTAL_MISMATCH: {
    title: "Some terms don't add up",
    body: "The courses read under a term don't match that term's total credits on the transcript. Those rows are marked for review.",
  },
  TRANSCRIPT_END_NOT_FOUND: {
    title: "This may not be the whole transcript",
    body: "The end of the transcript wasn't found - a page may be missing. Check that every term is here.",
  },
  NO_TERM_HEADINGS: {
    title: "No terms were found",
    body: "Courses were found but no term headings, so each course needs its term.",
  },
  TERM_ORDER_UNEXPECTED: {
    title: "Terms appear out of order",
    body: "Check that each course is in the right term.",
  },
};
