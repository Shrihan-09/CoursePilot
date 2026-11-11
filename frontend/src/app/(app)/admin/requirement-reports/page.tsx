"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, ErrorState, PageHeader, Skeleton } from "@/components/ui/feedback";
import { Tabs } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import type { ReportStatus, RequirementReport } from "@/types/requirement-report";

/**
 * Prompt 08 B1: the administrators' list of "Report a wrong requirement"
 * reports. Each shows the program, the node, the message and when it was
 * sent; the reporter is a redacted reference only. Non-administrators get the
 * API's 403 and see that this page is for administrators.
 */
const VIEWS: { value: ReportStatus | "all"; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
  { value: "all", label: "All" },
];

export default function RequirementReportsPage() {
  const [view, setView] = useState<ReportStatus | "all">("open");
  const reports = useQuery({ queryKey: ["requirement-reports", view],
                             queryFn: () => api.requirementReports(view) });

  if (reports.isPending) {
    return <div className="space-y-3"><Skeleton className="h-10 w-72" /><Skeleton className="h-24" /></div>;
  }
  if (reports.isError) {
    const err = reports.error;
    if (err instanceof ApiError && err.status === 403) {
      return <Alert tone="info" title="Administrators only">This page lists requirement reports for CoursePilot administrators.</Alert>;
    }
    return (
      <ErrorState title="Reports couldn't be loaded" onRetry={() => void reports.refetch()}
                  requestId={err instanceof ApiError ? err.requestId : null}>
        Check your connection and try again.
      </ErrorState>
    );
  }
  return (
    <div className="space-y-6">
      <PageHeader title="Requirement reports" description="Students' reports that a requirement looks wrong." />
      <Tabs label="Show reports" value={view} onChange={setView} items={VIEWS} />
      {reports.data.length === 0 ? (
        <p className="rounded border border-dashed border-border-strong p-4 text-sm text-fg-muted">No reports here.</p>
      ) : (
        <ul className="space-y-3">
          {reports.data.map((r) => <ReportRow key={r.id} report={r} />)}
        </ul>
      )}
    </div>
  );
}

function ReportRow({ report }: { report: RequirementReport }) {
  const client = useQueryClient();
  const [note, setNote] = useState(report.resolved_note ?? "");
  const update = useMutation({
    mutationFn: (status: ReportStatus) => api.updateRequirementReport(report.id, status, note.trim() || null),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["requirement-reports"] }),
  });
  return (
    <li className="space-y-2 rounded border border-border bg-surface-elevated p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-fg">
          {report.requirement_name} <span className="code text-xs text-fg-muted">{report.requirement_code}</span>
        </p>
        <span className="text-xs text-fg-muted">{report.status}</span>
      </div>
      <p className="code text-xs text-fg-muted">
        {report.program_key} · {report.catalog_year} · {new Date(report.created_at).toLocaleString()} · reporter {report.reporter}
      </p>
      <blockquote className="border-l-2 border-border-strong pl-3 text-sm text-fg-secondary [overflow-wrap:anywhere]">
        {report.message}
      </blockquote>
      <label className="block text-xs text-fg-muted">
        Note
        <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000}
               className="mt-1 w-full rounded border border-border-strong bg-surface p-2 text-sm text-fg" />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={update.isPending} onClick={() => update.mutate("resolved")}>Mark resolved</Button>
        <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => update.mutate("dismissed")}>Dismiss</Button>
        {report.status !== "open" && (
          <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => update.mutate("open")}>Reopen</Button>
        )}
      </div>
      {update.isError && <p role="alert" className="text-xs text-danger">The update didn&apos;t save. Try again.</p>}
    </li>
  );
}
