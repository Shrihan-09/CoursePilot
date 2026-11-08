"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Checkbox } from "@/components/ui/field";
import { api } from "@/lib/api";

/**
 * Prompt 11 A3: "Use my grades to personalize suggestions" (default on).
 * Grades only ORDER the student's own course options ("similar to a course
 * you did well in"); they are never shown to anyone else, never logged, and
 * never change what the planner may plan.
 */
export function GradesPersonalization() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["personalization"], queryFn: api.personalization });
  const set = useMutation({
    mutationFn: (on: boolean) => api.setPersonalization(on),
    onSuccess: (data) => {
      client.setQueryData(["personalization"], data);
      void client.invalidateQueries({ queryKey: ["course-options"] });
      void client.invalidateQueries({ queryKey: ["plan-choices"] });
    },
  });
  if (!q.data) return null;
  return (
    <section aria-labelledby="grades-personalization" className="space-y-2 rounded-xl border border-border bg-surface-elevated p-4">
      <h2 id="grades-personalization" className="text-sm font-semibold text-fg">Course suggestions</h2>
      <Checkbox
        label="Use my grades to personalize suggestions"
        checked={q.data.use_grades_for_suggestions}
        disabled={set.isPending}
        onChange={(e) => set.mutate(e.target.checked)}
      />
      <p className="text-xs text-fg-secondary">
        When on, options similar to courses you did well in (A to B) or marked &quot;doing well&quot; come first. Your grades
        only order your own suggestions - they never change what CoursePilot can plan, and nobody else sees them.
      </p>
    </section>
  );
}
