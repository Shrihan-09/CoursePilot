"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, ErrorState, PageHeader, Skeleton } from "@/components/ui/feedback";
import { DeclarationEditor } from "@/features/onboarding/declaration-editor";
import { PreferencesForm, type PrefSectionId } from "@/features/planning/preferences-form";
import { FirstTermEditor } from "@/features/setup/first-term-editor";
import { ProfileSummary } from "@/features/planning/profile-summary";
import { ApiError, api } from "@/lib/api";
import type { PlanningProfileInput } from "@/types/personalization";

/**
 * Personalize my plan: preferences -> review -> explicit confirmation.
 *
 * Nothing is saved until the student confirms the summary. Confirming is the
 * only way a planning preference changes (PUT /student/planning-profile);
 * the planner then reads exactly what was confirmed.
 */
const SECTIONS: PrefSectionId[] = ["about", "intensity", "week", "schedule", "musts", "more"];

export default function PersonalizePage() {
  const router = useRouter();
  const client = useQueryClient();
  const meta = useQuery({ queryKey: ["planning-profile"], queryFn: api.planningProfile, staleTime: 0 });
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const [draft, setDraft] = useState<PlanningProfileInput | null>(null);
  // Prompt 10a: /plan/personalize#week opens that section.
  const [section, setSection] = useState<PrefSectionId>("about");
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (SECTIONS.includes(hash as PrefSectionId)) setSection(hash as PrefSectionId);
  }, []);

  const confirm = useMutation({
    mutationFn: (value: PlanningProfileInput) => api.confirmPlanningProfile(value),
    onSuccess: async () => {
      await Promise.all(["planning-profile", "readiness", "plan", "planning-context"].map(
        (key) => client.invalidateQueries({ queryKey: [key] })));
      router.push("/plan");
    },
  });

  if (meta.isPending) return <div className="space-y-6"><Skeleton className="h-10 w-72" /><Skeleton className="h-96" /></div>;
  if (meta.isError) return <ErrorState onRetry={() => meta.refetch()}>Your planning preferences couldn&apos;t be loaded.</ErrorState>;

  const existing = meta.data.profile;
  const initial = draft ?? existing ?? meta.data.defaults;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Plan"
        title={existing ? "Your planning preferences" : "Personalize your plan"}
        description="How you'd like your semesters to look. Your courses, grades and program come from your confirmed record - Coursenav doesn't ask for them again."
      />
      {draft === null ? (
        <>
        <div className="grid gap-3 sm:grid-cols-2">
          <FirstTermEditor />
          <DeclarationEditor />
        </div>
        <PreferencesForm
          key={section}
          openSection={section}
          meta={meta.data}
          initial={initial}
          busy={false}
          submitLabel="Review"
          onSubmit={setDraft}
        />
        </>
      ) : (
        <section aria-labelledby="review-heading" className="space-y-4">
          <h2 id="review-heading" className="text-base font-semibold text-fg">Here&apos;s how Coursenav will plan for you</h2>
          <ProfileSummary profile={draft} programName={profile.data?.program.program_name} />
          {confirm.isError && (
            <Alert tone="danger" title="Your preferences weren't saved">
              {confirm.error instanceof ApiError && confirm.error.status < 500 ? confirm.error.message : "Please try again."}
            </Alert>
          )}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={confirm.isPending} onClick={() => confirm.mutate(draft)}>
              Confirm preferences
            </Button>
            <Button variant="secondary" onClick={() => setDraft(null)} disabled={confirm.isPending}>Edit</Button>
          </div>
          {existing && (
            <p className="text-xs text-fg-muted">Your saved plan will be marked out of date if these change, until you regenerate it.</p>
          )}
        </section>
      )}
    </div>
  );
}
