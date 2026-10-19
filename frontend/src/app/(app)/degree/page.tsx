"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Clock3 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Alert, ErrorState, PageHeader, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { Tabs } from "@/components/ui/overlay";
import { DegreeView } from "@/features/degree/degree-view";
import { WhatIf } from "@/features/degree/what-if";
import { ApiError, api } from "@/lib/api";

/**
 * Degree: "What have I completed? What do I still need? Why? What is
 * uncertain?" - answered by GET /student/audit, the Degree Engine's result,
 * rendered as returned. The page decides nothing about any requirement.
 */
export default function DegreePage() {
  const [view, setView] = useState<"mine" | "whatif">("mine");
  const profile = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const onboarding = useQuery({ queryKey: ["onboarding"], queryFn: api.onboarding });
  const audit = useQuery({ queryKey: ["audit"], queryFn: api.audit, staleTime: 0 });

  if (profile.isPending || audit.isPending || onboarding.isPending) {
    return (
      <div className="space-y-6" aria-label="Loading your degree">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-44" />
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}
      </div>
    );
  }
  if (profile.isError || audit.isError) {
    const err = (audit.error ?? profile.error) as unknown;
    return (
      <ErrorState
        title="Your degree progress couldn't be loaded"
        onRetry={() => { void profile.refetch(); void audit.refetch(); }}
        requestId={err instanceof ApiError ? err.requestId : null}
      >
        {err instanceof ApiError && err.status >= 500
          ? "CoursePilot's degree engine hit a problem. Nothing about your record has changed."
          : "Check your connection and try again."}
      </ErrorState>
    );
  }
  const program = profile.data.program;
  const counts = onboarding.data?.history_counts;
  const noHistory = counts && (counts.completed ?? 0) + (counts.in_progress ?? 0) === 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Degree"
        title={program.program_name}
        description={<>{program.degree_type} · Catalog {program.catalog_year}</>}
        actions={
          program.support_status === "supported" ? (
            <StatusBadge tone="success" icon={BadgeCheck} label="Requirements reviewed" />
          ) : (
            <StatusBadge tone="warning" icon={Clock3} label="Requirements pending review" />
          )
        }
      />
      <Tabs
        label="Degree view"
        value={view}
        onChange={setView}
        items={[{ value: "mine", label: "My program" }, { value: "whatif", label: "What-if" }]}
      />
      {view === "mine" ? (
        <>
          {noHistory && (
            <Alert
              tone="info"
              title="No courses on your record yet"
              action={<Link href="/onboarding" className={buttonVariants({ variant: "secondary", size: "sm" })}>Add courses</Link>}
            >
              Every requirement below shows as still needed until you add your transcript or courses.
            </Alert>
          )}
          <DegreeView audit={audit.data} program={program} />
        </>
      ) : (
        <WhatIf currentKey={program.program_key} />
      )}
    </div>
  );
}
