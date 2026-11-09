import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { YourChoicesCard, type PlanChanges } from "@/features/plan/plan-changes";
import { courseCategory, SemesterCard } from "@/features/plan/plan-result";
import type { PlannedCourse, PlanTerm } from "@/types/plan";

const course = (system: string | null): PlannedCourse => ({
  course: "01:198:210", title: "Data Management for Data Science", credits: "3", term_code: "20271", reasons: [],
  requirements: system ? [{ requirement_code: "R", requirement_name: "Data requirement", requirement_system: system, provisional: false }] : [],
  prerequisite: { state: "satisfied_by_history", conditional_on: [], unknown_reasons: [] },
  offering: { kind: "confirmed_in_term", observed_terms: [], same_season_terms: [] },
} as unknown as PlannedCourse);

describe("semester presentation", () => {
  it("groups using structured requirement systems and keeps unmapped evidence explicit", () => {
    expect(courseCategory(course("major"))).toBe("Major");
    expect(courseCategory(course("core"))).toBe("School Core / gen-ed");
    expect(courseCategory(course("minor"))).toBe("Electives");
    const term = { term_code: "20271", label: "Spring 2027", courses: [course(null)], credits: "3", free_elective_credits: "0" } as PlanTerm;
    const html = renderToStaticMarkup(<SemesterCard term={term} scheduleHref={null} />);
    expect(html).toContain("Requirement coverage not mapped");
    expect(html.indexOf("Data Management for Data Science")).toBeLessThan(html.indexOf("01:198:210"));
    expect(html).toContain(">Details</summary>");
  });

  it("later semesters are collapsed and keep their human semester name", () => {
    const term = { term_code: "20279", label: "Fall 2027", courses: [], credits: "0", free_elective_credits: "3" } as PlanTerm;
    const html = renderToStaticMarkup(<SemesterCard term={term} open={false} scheduleHref={null} />);
    expect(html).toContain("Fall 2027");
    expect(html.slice(0, html.indexOf(">"))).not.toContain("open=");
  });

  it("already answered choices start compact with Review and no inline option cards", () => {
    const client = new QueryClient();
    client.setQueryData(["plan-choices"], { term_code: "20271", questions: [{ kind: "already_set", title: "Calculus II", prompt: "Already set: Calculus II (retake)" }] });
    const html = renderToStaticMarkup(<QueryClientProvider client={client}><YourChoicesCard changes={{} as PlanChanges} /></QueryClientProvider>);
    expect(html).toContain("All set for Spring 2027");
    expect(html).toContain("Review");
    expect(html).not.toContain('data-testid="option-card"');
  });
});
