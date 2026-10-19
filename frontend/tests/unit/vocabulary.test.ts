/**
 * Phase 7.2: the student-facing words for engine states.
 *
 * The invariant that matters most: UNKNOWN is never presented as FAILED (or
 * as success). "CoursePilot cannot determine this" and "you do not meet
 * this" must never share a label, an icon or the danger tone.
 */
import { describe, expect, it } from "vitest";

import {
  AUDIT_STATUS,
  availabilityText,
  blockedReasonText,
  ELIGIBILITY_STATE,
  PLAN_SEVERITY,
  PLAN_STATUS,
  planReasonText,
  REQUIREMENT_STATUS,
  RESTRICTION_OUTCOME,
  RULE_STATUS,
  SCHEDULE_REASON,
  SCHEDULE_STATUS,
  STALE_REASON,
  termLabel,
} from "@/lib/academic/vocabulary";
import type { PlannedCourse, PlanReason } from "@/types/plan";

const UNKNOWN_REQ = ["indeterminate", "not_evaluable"] as const;

describe("UNKNOWN is not FAILED", () => {
  it("requirement UNKNOWN states are warnings with a question icon, never danger or success", () => {
    for (const s of UNKNOWN_REQ) {
      const p = REQUIREMENT_STATUS[s];
      expect(p.tone).toBe("warning");
      expect(p.icon).toBe("unknown");
      expect(p.meaning).toMatch(/cannot (determine|evaluate)/i);
      expect(p.meaning).toMatch(/not a finding that you fail/i);
      expect(p.label).not.toMatch(/not met|fail|ineligible/i);
    }
  });

  it("the same holds for program rules, where unsatisfied IS a violation", () => {
    expect(RULE_STATUS.unsatisfied.tone).toBe("danger");
    expect(RULE_STATUS.unsatisfied.icon).toBe("failed");
    for (const s of UNKNOWN_REQ) {
      expect(RULE_STATUS[s].tone).toBe("warning");
      expect(RULE_STATUS[s].icon).toBe("unknown");
      expect(RULE_STATUS[s].label).not.toBe(RULE_STATUS.unsatisfied.label);
    }
  });

  it("an incomplete requirement is 'still needed', not a failure", () => {
    expect(REQUIREMENT_STATUS.unsatisfied.tone).not.toBe("danger");
    expect(REQUIREMENT_STATUS.unsatisfied.icon).not.toBe("failed");
  });

  it("prerequisite needs_confirmation differs from unsatisfied in label, icon and tone", () => {
    const unknown = ELIGIBILITY_STATE.needs_confirmation;
    const failed = ELIGIBILITY_STATE.unsatisfied;
    expect(unknown.tone).toBe("warning");
    expect(unknown.icon).toBe("unknown");
    expect(failed.icon).toBe("failed");
    expect(unknown.label).not.toBe(failed.label);
    expect(unknown.meaning).toMatch(/cannot evaluate/i);
    expect(unknown.meaning).toMatch(/did not place/i);
  });

  it("schedule restriction unknown is not 'restricted'", () => {
    expect(RESTRICTION_OUTCOME.unknown.tone).toBe("warning");
    expect(RESTRICTION_OUTCOME.unknown.icon).toBe("unknown");
    expect(RESTRICTION_OUTCOME.not_satisfied.icon).toBe("failed");
  });

  it("only satisfied/complete states are presented as success", () => {
    const success = Object.entries(REQUIREMENT_STATUS).filter(([, p]) => p.tone === "success").map(([k]) => k);
    expect(success).toEqual(["satisfied"]);
    expect(AUDIT_STATUS.indeterminate.tone).toBe("warning");
    expect(AUDIT_STATUS.insufficient_data.tone).toBe("warning");
  });
});

describe("every engine value has words", () => {
  it("covers every status the backend defines", () => {
    expect(Object.keys(REQUIREMENT_STATUS).sort()).toEqual(
      ["indeterminate", "not_evaluable", "partially_satisfied", "provisionally_satisfied", "satisfied", "unsatisfied"]);
    expect(Object.keys(AUDIT_STATUS).sort()).toEqual(
      ["complete", "in_progress", "incomplete", "indeterminate", "insufficient_data"]);
    expect(Object.keys(PLAN_STATUS).sort()).toEqual(["covers_all_requirements", "nothing_to_plan", "partial"]);
    expect(Object.keys(PLAN_SEVERITY).sort()).toEqual(["blocker", "needs_confirmation", "warning"]);
    expect(Object.keys(SCHEDULE_STATUS).sort()).toEqual(
      ["course_eligibility_failed", "no_valid_schedule", "options_found", "search_limit_reached", "term_schedule_not_published"]);
    expect(Object.keys(SCHEDULE_REASON)).toHaveLength(8);
    expect(Object.keys(STALE_REASON).sort()).toEqual(
      ["academic_record_changed", "engine_updated", "program_changed", "program_rules_changed", "rutgers_data_changed", "start_term_passed"]);
  });

  it("an unpublished term never claims a schedule exists or borrows another term", () => {
    expect(SCHEDULE_STATUS.term_schedule_not_published.meaning).toMatch(/never borrows another term/);
    expect(SCHEDULE_STATUS.term_schedule_not_published.tone).not.toBe("success");
  });

  it("unrecognised blocked reasons are shown as their code, not guessed at", () => {
    expect(blockedReasonText("something_new:x")).toBe("something_new:x");
    expect(blockedReasonText("unmodeled_condition:instructor_permission")).toMatch(/instructor permission/);
    expect(blockedReasonText("dependency_needs_confirmation:01:198:205")).toMatch(/01:198:205/);
  });

  it("formats term codes", () => {
    expect(termLabel("20271")).toBe("Spring 2027");
    expect(termLabel("20269")).toBe("Fall 2026");
    expect(termLabel("20267")).toBe("Summer 2026");
    expect(termLabel("20270")).toBe("Winter 2027");
  });
});

const course: PlannedCourse = {
  course: "01:198:211", title: "Computer Architecture", credits: "4", term_code: "20271",
  reasons: [],
  requirements: [{ requirement_code: "CS_CORE", requirement_name: "CS core", requirement_system: "major", provisional: true }],
  prerequisite: { state: "conditional_on_plan", rules_term: "20261", raw_text: null, canonical_text: null, combination: null,
                  corequisite_text: null, conditional_on: [], unknown_reasons: [] },
  offering: { kind: "historically_offered_in_season", target_term: "20271", observed_terms: ["20261"], same_season_terms: ["20261"],
              most_recent_term: "20261", rules_term: "20261", target_term_coverage: null },
  corequisite_group: [], unlocks: ["01:198:344"], interchangeable_alternatives: 2,
};

describe("placement reasons come from the course's own fields", () => {
  const all: PlanReason[] = [
    "satisfies_requirement", "prerequisites_satisfied_by_history", "prerequisites_conditional_on_plan",
    "no_prerequisite_published", "required_prerequisite_for", "required_corequisite_for", "unlocks_downstream_course",
    "confirmed_offering_in_term", "historically_offered_in_season", "rules_from_earlier_term", "selected_by_canonical_tie_break",
  ];

  it("has a sentence for every reason", () => {
    for (const r of all) expect(planReasonText(r, course).length).toBeGreaterThan(10);
  });

  it("uses the engine's requirement names, unlocks and evidence", () => {
    expect(planReasonText("satisfies_requirement", course)).toBe("Counts toward CS core");
    expect(planReasonText("required_prerequisite_for", course)).toContain("01:198:344");
    expect(planReasonText("historically_offered_in_season", course)).toContain("Spring 2026");
    expect(planReasonText("selected_by_canonical_tie_break", course)).toMatch(/One of 3 .*not by preference/);
  });
});

describe("live availability is dated unless current", () => {
  const base = { source: "openSections.json", source_hash: null, since: null, reasons: [] };
  it("says 'as of' only for a current observation", () => {
    const now = availabilityText({ ...base, state: "open", freshness: "current", observed_at: "2026-10-18T14:00:00Z" });
    expect(now.label).toMatch(/^Open as of /);
    expect(now.tone).toBe("success");
    const old = availabilityText({ ...base, state: "open", freshness: "stale", observed_at: "2026-10-14T02:31:00Z" });
    expect(old.label).toMatch(/^Open when last seen/);
    expect(old.tone).toBe("neutral");
    expect(old.meaning).toMatch(/not current/);
  });
  it("never states open or closed without an observation", () => {
    const none = availabilityText({ ...base, state: "unknown", freshness: "unobserved", observed_at: null });
    expect(none.label).toBe("Availability unknown");
  });
});
