"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Alert, Badge, EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Dialog } from "@/components/ui/overlay";
import { api } from "@/lib/api";
import { meetingPlace, meetingText } from "@/lib/academic/schedule-time";
import { termLabel } from "@/lib/academic/vocabulary";
import type { CurrentCourse, CurrentSchedule } from "@/types/personalization";
import type { ScheduleOption, SectionChoice } from "@/types/schedule";

import { WeekCalendar } from "./week-calendar";

/**
 * My current schedule - DESCRIPTIVE (Phase 7.2.1).
 *
 * The record knows which courses are in progress, not which sections. So no
 * section is ever shown as "yours" until the student confirms it from the
 * real Rutgers list. A transcript's section number may MARK a matching
 * section; it never selects one.
 */

export function asOption(choices: SectionChoice[]): ScheduleOption {
  return {
    rank: 1, choices, total_credits: null, reasons: [], issues: [], days: [],
    score: { needs_confirmation: 0, unknown_time_sections: 0, preference_misses: 0, class_days: 0,
             gap_minutes: 0, closed_sections_if_current: 0, unknown_availability_if_current: 0 },
    availability: { state: "unknown", freshness: "unobserved", as_of: null, all_open_now: false, courses: [] },
  };
}

function SectionPicker({ course, onClose }: { course: CurrentCourse; onClose: () => void }) {
  const client = useQueryClient();
  const sections = useQuery({ queryKey: ["course-sections", course.course], queryFn: () => api.courseSections(course.course) });
  const [index, setIndex] = useState<string>(course.confirmed[0]?.index_number ?? "");
  const confirm = useMutation({
    mutationFn: () => api.confirmSection(course.course, index),
    onSuccess: (data: CurrentSchedule) => { client.setQueryData(["current-schedule"], data); onClose(); },
  });
  const list = sections.data?.sections ?? [];
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Your section of ${course.course}`}
      description="Choose the section you're actually registered in. CoursePilot doesn't guess it."
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!index} loading={confirm.isPending} onClick={() => confirm.mutate()}>Confirm section</Button>
      </>}
    >
      {sections.isPending ? <Skeleton className="h-40" /> : sections.isError ? (
        <p className="text-sm text-danger">The sections couldn&apos;t be loaded.</p>
      ) : list.length === 0 ? (
        <p className="text-sm text-fg-secondary">CoursePilot has no Rutgers sections for this course in {termLabel(course.term_code)}.</p>
      ) : (
        <fieldset className="max-h-[50vh] space-y-2 overflow-y-auto">
          <legend className="sr-only">Sections</legend>
          {list.map((s) => (
            <label key={s.index_number} className="flex cursor-pointer items-start gap-3 rounded border border-border p-3 has-[:checked]:border-primary has-[:checked]:bg-primary-subtle">
              <input type="radio" name="section" className="mt-1 size-4 accent-[rgb(var(--primary))]"
                checked={index === s.index_number} onChange={() => setIndex(s.index_number)} />
              <span className="min-w-0 flex-1 space-y-0.5 text-sm">
                <span className="block font-medium text-fg">
                  Section {s.section_number} · Index <span className="code">{s.index_number}</span>
                  {s.component === "required_companion" && <span className="text-fg-muted"> · linked {s.supplement_code || "component"}</span>}
                </span>
                {s.meetings.map((m, i) => (
                  <span key={i} className="block text-fg-secondary">{meetingText(m)}{meetingPlace(m) ? ` · ${meetingPlace(m)}` : ""}</span>
                ))}
                <span className="block text-fg-muted">{s.instructors.length ? s.instructors.join(", ") : "Instructor not listed"}</span>
                {course.transcript_section && course.transcript_section === s.section_number && (
                  <Badge tone="info">Matches the section number on your transcript</Badge>
                )}
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {confirm.isError && <p className="text-sm text-danger">That section couldn&apos;t be confirmed. Please try again.</p>}
    </Dialog>
  );
}

export function CurrentScheduleView() {
  const client = useQueryClient();
  const current = useQuery({ queryKey: ["current-schedule"], queryFn: api.currentSchedule, staleTime: 0 });
  const [picking, setPicking] = useState<CurrentCourse | null>(null);
  const forget = useMutation({
    mutationFn: (course: string) => api.forgetSection(course),
    onSuccess: () => client.invalidateQueries({ queryKey: ["current-schedule"] }),
  });

  if (current.isPending) return <Skeleton className="h-64" />;
  if (current.isError) return <ErrorState onRetry={() => current.refetch()}>Your current courses couldn&apos;t be loaded.</ErrorState>;
  const cs = current.data;
  if (!cs.term_code) {
    return (
      <EmptyState icon={CalendarDays} title="No courses in progress on your record"
        action={<Link href="/onboarding" className={buttonVariants({ variant: "secondary", size: "sm" })}>Add current courses</Link>}>
        Add the courses you&apos;re taking now as in progress, then choose your sections here.
      </EmptyState>
    );
  }
  const confirmed = cs.courses.flatMap((c) => c.confirmed);
  const open = cs.courses.filter((c) => c.confirmed.length === 0);

  return (
    <div className="space-y-5">
      <p className="text-sm text-fg-secondary">
        {cs.term_label}: the courses on your record as in progress. Only sections you confirm appear on the calendar -
        CoursePilot never infers a section from a course.
      </p>
      {cs.other_in_progress_terms.length > 0 && (
        <Alert tone="info">
          You also have in-progress courses recorded for {cs.other_in_progress_terms.map(termLabel).join(", ")}. Showing {cs.term_label}.
        </Alert>
      )}
      {!cs.term_published && (
        <Alert tone="warning" title={`No Rutgers section data for ${cs.term_label}`}>
          CoursePilot knows your courses but can&apos;t show or confirm their sections for this term.
        </Alert>
      )}

      <ul className="divide-y divide-border rounded-lg border border-border bg-surface-elevated">
        {cs.courses.map((c) => (
          <li key={c.course} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
            <div className="min-w-0 space-y-0.5 text-sm">
              <p><span className="code font-semibold text-fg">{c.course}</span> <span className="text-fg-secondary [overflow-wrap:anywhere]">{c.title ?? ""}</span></p>
              {c.confirmed.length > 0 ? (
                <p className="text-fg-secondary">
                  {c.confirmed.map((s) => `Section ${s.section_number}, index ${s.index_number}`).join(" + ")}
                  <span className="text-fg-muted"> - confirmed by you</span>
                </p>
              ) : (
                <p className="text-fg-muted">Section not chosen{c.sections_available ? ` · ${c.sections_available} Rutgers sections` : ""}</p>
              )}
              {c.unavailable.map((u) => (
                <p key={u.index_number} className="text-warning">
                  Index {u.index_number} is no longer in Rutgers&apos; data for this term. Choose your section again.
                </p>
              ))}
            </div>
            <div className="flex shrink-0 gap-2">
              {cs.term_published && (
                <Button size="sm" variant={c.confirmed.length ? "secondary" : "primary"} onClick={() => setPicking(c)}>
                  {c.confirmed.length ? "Change" : "Choose my section"}
                </Button>
              )}
              {(c.confirmed.length > 0 || c.unavailable.length > 0) && (
                <Button size="sm" variant="ghost" loading={forget.isPending && forget.variables === c.course} onClick={() => forget.mutate(c.course)}>
                  Forget
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>

      {confirmed.length > 0 ? (
        <section aria-labelledby="current-week" className="space-y-3">
          <h3 id="current-week" className="text-base font-semibold text-fg">My week</h3>
          <WeekCalendar option={asOption(confirmed)} />
          {open.length > 0 && <p className="text-xs text-fg-muted">{open.length} course{open.length === 1 ? "" : "s"} not shown until you choose a section.</p>}
        </section>
      ) : (
        <p className="text-sm text-fg-muted">Choose your sections to see your week.</p>
      )}
      {picking && <SectionPicker course={picking} onClose={() => setPicking(null)} />}
    </div>
  );
}
