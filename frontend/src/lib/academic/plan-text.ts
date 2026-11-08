/**
 * Plan wording shared by the plan board and its tests (correctness fixes).
 * Presentation only: every number comes from the plan the server returned.
 */
import { termLabel, WORKLOAD_LEVEL_LABEL } from "@/lib/academic/vocabulary";
import type { ConditionalDependency, PlanningConstraints } from "@/types/plan";

const num = (v: string | number) => String(Number(v));

/** "1 credit", "4 credits". */
export function creditsText(credits: number): string {
  return `${num(credits)} ${credits === 1 ? "credit" : "credits"}`;
}

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight"];

/**
 * Item 12: free-elective credits as course SLOTS - a student takes courses,
 * not loose credits. Up to 4 credits is one course ("one 1-credit elective",
 * "one ~3-credit elective"); more is that many ~3-credit courses.
 */
export function electiveSlotText(credits: number): string {
  if (credits <= 0) return "no elective";
  if (credits <= 4) {
    return credits === 3 ? "one ~3-credit elective" : `one ${num(credits)}-credit elective`;
  }
  const slots = Math.max(2, Math.round(credits / 3));
  return `${WORDS[slots] ?? slots} electives (about ${creditsText(credits)})`;
}

/**
 * Item 6: ONE credit range in the plan header. With a workload level, the
 * server sends that level's range (or the student's own) as the target and
 * maximum; without one, the target is soft and the maximum hard.
 */
export function loadText(c: PlanningConstraints): string {
  const max = num(c.max_credits_per_term);
  const courses = `${c.max_courses_per_term} courses`;
  if (c.workload_level) {
    const level = WORKLOAD_LEVEL_LABEL[c.workload_level] ?? c.workload_level;
    const low = c.credit_min ?? c.target_credits_per_term;
    return `${level}: ${low != null ? `${num(low)}-` : "up to "}${num(c.credit_max ?? max)} credits and at most ${courses} a semester`;
  }
  const aim = c.target_credits_per_term ? `aiming for ${num(c.target_credits_per_term)} credits, ` : "";
  return `${aim}never more than ${max} credits or ${courses} a semester`;
}

/**
 * Item 11: "Prerequisites met once you pass 01:640:152 in Spring 2027" - the
 * exact courses and terms, never "earlier courses".
 */
export function conditionalLabel(deps: ConditionalDependency[]): string {
  const parts = deps.map((d) => {
    const grade = d.required_grade ? ` with ${d.required_grade} or better` : "";
    const when = d.source === "in_progress"
      ? `(in progress, ${termLabel(d.term_code)})`
      : `in ${termLabel(d.term_code)}`;
    return `${d.course}${grade} ${when}`;
  });
  const list = parts.length <= 1 ? parts.join("")
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `Prerequisites met once you pass ${list}`;
}
