"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileText, Info, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Badge, ErrorState, Progress, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { Select } from "@/components/ui/field";
import { Tabs } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ImportDetail, ImportRow, RowState } from "@/types/onboarding";

import { DOC_WARNING, FAILURE, ORIGIN_LABEL, ROW_STATE, STATUS_LABEL, termLabel } from "./labels";

/* ------------------------------------------------------------------ upload */

export function TranscriptUpload({ onUploaded }: { onUploaded: (id: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadTranscript(file),
    onSuccess: (imp) => {
      if (imp.state === "extracted") onUploaded(imp.import_id);
    },
  });
  const failed = upload.data && upload.data.state === "failed" ? upload.data.failure_code : null;
  const refused = upload.error instanceof ApiError ? upload.error.issues[0]?.code : null;
  const problem = failed ?? refused;

  function choose(files: FileList | null) {
    const file = files?.[0];
    if (file) upload.mutate(file);
  }

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          choose(e.dataTransfer.files);
        }}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragging ? "border-primary bg-primary-subtle" : "border-border-strong bg-surface",
        )}
      >
        <FileText aria-hidden className="size-7 text-fg-muted" />
        <div className="space-y-1">
          <p className="text-sm font-medium text-fg">Your Rutgers unofficial transcript (PDF)</p>
          <p className="text-xs text-fg-muted">
            Download it from myRutgers. Up to 5 MB. The file is read, then deleted - it isn't kept.
          </p>
        </div>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          aria-label="Transcript PDF"
          tabIndex={-1}
          className="hidden"
          id="transcript-file"
          onChange={(e) => choose(e.target.files)}
        />
        <Button
          variant="primary"
          onClick={() => input.current?.click()}
          loading={upload.isPending}
          disabled={upload.isPending}
        >
          <Upload aria-hidden className="size-4" />
          {upload.isPending ? "Reading your transcript…" : "Choose PDF"}
        </Button>
        {upload.isPending && (
          <div className="w-full max-w-xs space-y-1">
            <Progress value={null} label="Reading your transcript" />
            <p className="text-xs text-fg-muted">This takes a few seconds.</p>
          </div>
        )}
      </div>
      {problem && (
        <Alert tone="danger" title="This transcript couldn't be used">
          {FAILURE[problem] ?? "Please try a different file."}
        </Alert>
      )}
      {upload.isError && !refused && (
        <Alert tone="danger">The upload didn't go through. Please try again.</Alert>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ review */

type Filter = "attention" | "all" | "matched" | "excluded";

function rowNeedsAttention(row: ImportRow) {
  if (row.excluded) return false;
  if (row.state === "conflict") return row.resolution === null;
  return row.state !== "matched";
}

export function TranscriptReview({
  importId,
  onConfirmed,
  onDiscarded,
}: {
  importId: string;
  onConfirmed: () => void;
  onDiscarded: () => void;
}) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["import", importId], queryFn: () => api.transcript(importId) });
  const [filter, setFilter] = useState<Filter>("attention");
  const [error, setError] = useState<ApiError | null>(null);

  const setData = (data: ImportDetail) => client.setQueryData(["import", importId], data);
  const edit = useMutation({
    mutationFn: ({ rowId, body }: { rowId: string; body: Record<string, unknown> }) =>
      api.editRow(importId, rowId, { expected_version: query.data!.version, ...body }),
    onSuccess: (d) => {
      setError(null);
      setData(d);
    },
    onError: (e) => {
      setError(e instanceof ApiError ? e : null);
      void query.refetch();
    },
  });
  const confirm = useMutation({
    mutationFn: () => api.confirmTranscript(importId, query.data!.version),
    onSuccess: (d) => {
      setData(d);
      onConfirmed();
    },
    onError: (e) => {
      setError(e instanceof ApiError ? e : null);
      void query.refetch();
    },
  });
  const discard = useMutation({
    mutationFn: () => api.discardTranscript(importId),
    onSuccess: onDiscarded,
  });

  const data = query.data;
  const rows = data?.rows ?? [];
  const attention = rows.filter(rowNeedsAttention);
  const visible = useMemo(() => {
    if (filter === "attention") return rows.filter(rowNeedsAttention);
    if (filter === "matched") return rows.filter((r) => !r.excluded && r.state === "matched");
    if (filter === "excluded") return rows.filter((r) => r.excluded);
    return rows;
  }, [rows, filter]);

  // Rows the transcript shows with no grade, in a term it shows no average for.
  const ungraded = rows.filter(
    (r) => !r.excluded && r.normalized.status === null
      && r.issues.some((i) => i.code === "TERM_NOT_YET_GRADED"));

  if (query.isPending) {
    return (
      <div className="space-y-3" aria-label="Loading transcript">
        <Skeleton className="h-20" />
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14" />)}
      </div>
    );
  }
  if (query.isError || !data) {
    return <ErrorState onRetry={() => query.refetch()}>The transcript review couldn't be loaded.</ErrorState>;
  }

  const counts = data.row_counts;
  const willWrite = rows.filter((r) => !r.excluded
    && !(r.state === "conflict" && r.resolution === "keep_existing")
    && !r.issues.some((i) => i.code === "ALREADY_RECORDED")).length;
  const alreadyRecorded = rows.filter((r) => !r.excluded
    && r.issues.some((i) => i.code === "ALREADY_RECORDED")).length;
  const byTerm = groupByTerm(visible);

  return (
    <section aria-labelledby="review-title" className="space-y-5">
      <div className="space-y-1">
        <h2 id="review-title" className="text-xl font-semibold tracking-tight text-fg">
          Review your transcript
        </h2>
        <p className="text-sm text-fg-secondary">
          CoursePilot read {rows.length} course{rows.length === 1 ? "" : "s"}. Nothing is saved until
          you confirm.
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4">
        {(
          [
            ["Recognized", counts.matched ?? 0, "success"],
            ["Needs review", (counts.needs_review ?? 0) + (counts.invalid ?? 0), "warning"],
            ["Not recognized", counts.unrecognized ?? 0, "neutral"],
            ["Conflicts", counts.conflict ?? 0, "danger"],
          ] as const
        ).map(([label, n]) => (
          <div key={label} className="bg-surface-elevated p-3">
            <dt className="text-xs text-fg-muted">{label}</dt>
            <dd className="tabular text-xl font-semibold text-fg">{n}</dd>
          </div>
        ))}
      </dl>

      {data.warnings.map((w) => {
        const known = DOC_WARNING[w];
        return (
          <Alert key={w} tone="warning" title={known?.title ?? "Check this transcript"}>
            {known?.body ?? "Some rows need your review."}
          </Alert>
        );
      })}

      {data.terms.some((t) => t.consistent === false) && (
        <div className="rounded-lg border border-border bg-surface p-3 text-sm">
          <p className="mb-2 font-medium text-fg">Term totals</p>
          <ul className="space-y-1">
            {data.terms.map((t) => (
              <li key={t.term_code} className="flex justify-between gap-4 text-fg-secondary">
                <span>{termLabel(t.term_code)}</span>
                <span className="tabular">
                  read {t.rows_credit_sum ?? "?"} of {t.credits_attempted ?? "?"} credits{" "}
                  {t.consistent === false && <Badge tone="warning">doesn&apos;t add up</Badge>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ungraded.length > 0 && (
        <Alert
          tone="info"
          title={`${ungraded.length} course${ungraded.length === 1 ? " has" : "s have"} no grade yet`}
          action={
            <Button
              size="sm"
              variant="secondary"
              loading={edit.isPending}
              onClick={async () => {
                // One edit per row, each against the CURRENT version.
                for (const r of ungraded) {
                  const fresh = client.getQueryData<ImportDetail>(["import", importId]);
                  if (!fresh) break;
                  const d = await api.editRow(importId, r.row_id,
                    { expected_version: fresh.version, status: "in_progress" });
                  setData(d);
                }
              }}
            >
              Mark as in progress
            </Button>
          }
        >
          The transcript shows no average for their term, so they&apos;re probably in progress.
          CoursePilot won&apos;t assume it - confirm if that&apos;s right.
        </Alert>
      )}

      {error && (
        <Alert tone="danger" title={error.message}>
          {error.issues.map((i) => i.message).join(" ")}
        </Alert>
      )}

      <Tabs<Filter>
        label="Show rows"
        value={filter}
        onChange={setFilter}
        items={[
          { value: "attention", label: "Needs you", count: attention.length },
          { value: "all", label: "All", count: rows.length },
          { value: "matched", label: "Recognized", count: rows.filter((r) => !r.excluded && r.state === "matched").length },
          { value: "excluded", label: "Left out", count: rows.filter((r) => r.excluded).length },
        ]}
      />

      {visible.length === 0 ? (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-4 py-6 text-sm text-fg-secondary">
          <CheckCircle2 aria-hidden className="size-4 text-success" />
          {filter === "attention" ? "Nothing needs your attention." : "No rows here."}
        </div>
      ) : (
        <div className="space-y-5">
          {byTerm.map(([term, termRows]) => (
            <div key={term} className="space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                {termLabel(term === "none" ? null : term)}
              </h3>
              <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface-elevated">
                {termRows.map((row) => (
                  <ReviewRow
                    key={row.row_id}
                    row={row}
                    busy={edit.isPending}
                    onEdit={(body) => edit.mutate({ rowId: row.row_id, body })}
                  />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      <div className="sticky bottom-3 z-10 space-y-3 rounded-lg border border-border bg-surface-elevated p-3 shadow-elevated sm:bottom-4 sm:p-4">
        <p className="text-sm text-fg-secondary">
          {data.ready ? (
            <>
              Confirming adds <strong className="tabular text-fg">{willWrite}</strong> course
              {willWrite === 1 ? "" : "s"} to your record
              {alreadyRecorded > 0 && <> ({alreadyRecorded} already there stay as they are)</>}
              {(counts.excluded ?? 0) > 0 && <>; {counts.excluded} left out</>}. You can edit any
              course afterwards.
            </>
          ) : (
            <>
              <strong className="tabular text-fg">{data.blocking_rows}</strong> row
              {data.blocking_rows === 1 ? " needs" : "s need"} your review before you can confirm.
            </>
          )}
        </p>
        <div className="flex gap-2 sm:justify-end">
          <Button variant="ghost" onClick={() => discard.mutate()} loading={discard.isPending}>
            Discard upload
          </Button>
          <Button variant="primary" disabled={!data.ready} loading={confirm.isPending}
                  className="flex-1 sm:flex-none" onClick={() => confirm.mutate()}>
            Confirm and save
          </Button>
        </div>
      </div>
    </section>
  );
}

function groupByTerm(rows: ImportRow[]): [string, ImportRow[]][] {
  const map = new Map<string, ImportRow[]>();
  for (const r of rows) {
    const key = r.normalized.term_code ?? "none";
    map.set(key, [...(map.get(key) ?? []), r]);
  }
  return [...map.entries()].sort(([a], [b]) => (a === "none" ? 1 : b === "none" ? -1 : a.localeCompare(b)));
}

function ReviewRow({
  row,
  busy,
  onEdit,
}: {
  row: ImportRow;
  busy: boolean;
  onEdit: (body: Record<string, unknown>) => void;
}) {
  const state = ROW_STATE[row.state as RowState];
  const blockingIssues = row.issues.filter((i) => i.severity !== "info");
  const infoIssues = row.issues.filter((i) => i.severity === "info" && i.code !== "TERM_NOT_YET_GRADED");
  const n = row.normalized;
  const needsStatus = !row.excluded && n.status === null;

  return (
    <li className={cn("space-y-2 p-3 sm:p-4", row.excluded && "bg-surface opacity-70")}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-0.5">
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className="code text-sm font-medium text-fg">
              {n.course_code ?? row.extracted.course_code ?? "-"}
            </span>
            <span className="text-sm text-fg-secondary [overflow-wrap:anywhere]">
              {n.course_title ?? row.extracted.title ?? ""}
            </span>
          </p>
          <p className="tabular text-xs text-fg-muted">
            {n.credits_earned ?? row.extracted.credits ?? "-"} cr ·{" "}
            {n.grade ? `Grade ${n.grade}` : row.extracted.grade ? `Read “${row.extracted.grade}”` : "No grade"}
            {n.status && ` · ${STATUS_LABEL[n.status] ?? n.status}`}
            {n.credit_origin && n.credit_origin !== "rutgers" && ` · ${ORIGIN_LABEL[n.credit_origin]}`}
            {row.edited && " · edited by you"}
          </p>
        </div>
        {row.excluded ? (
          <StatusBadge tone="neutral" label="Left out" className="self-start" />
        ) : (
          <StatusBadge tone={state.tone} icon={state.icon} label={state.label} className="self-start" />
        )}
      </div>

      {!row.excluded && blockingIssues.length > 0 && (
        <ul className="space-y-1 text-sm">
          {blockingIssues.map((i) => (
            <li key={i.code} className="flex gap-2 text-fg-secondary">
              <Info aria-hidden className="mt-0.5 size-3.5 shrink-0 text-warning" />
              <span>{i.message}</span>
            </li>
          ))}
        </ul>
      )}
      {!row.excluded && infoIssues.length > 0 && (
        <ul className="space-y-1 text-xs text-fg-muted">
          {infoIssues.map((i) => <li key={i.code}>{i.message}</li>)}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {needsStatus && (
          <label className="flex items-center gap-2 text-xs text-fg-muted">
            Status
            <Select
              aria-label={`Status for ${n.course_code ?? "course"}`}
              className="h-9 w-40"
              defaultValue=""
              disabled={busy}
              onChange={(e) => e.target.value && onEdit({ status: e.target.value })}
            >
              <option value="" disabled>Choose…</option>
              <option value="in_progress">In progress</option>
              <option value="completed">Completed</option>
              <option value="planned">Planned</option>
            </Select>
          </label>
        )}
        {!row.excluded && row.state === "conflict" && (
          <>
            <Button size="sm" variant={row.resolution === "keep_existing" ? "primary" : "secondary"}
                    disabled={busy} onClick={() => onEdit({ resolution: "keep_existing" })}>
              Keep my entry
            </Button>
            <Button size="sm" variant={row.resolution === "replace_existing" ? "primary" : "secondary"}
                    disabled={busy} onClick={() => onEdit({ resolution: "replace_existing" })}>
              Use the transcript
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" disabled={busy}
                onClick={() => onEdit({ excluded: !row.excluded })}>
          {row.excluded ? "Include" : "Leave out"}
        </Button>
      </div>

      <details className="text-xs text-fg-muted">
        <summary className="cursor-pointer select-none hover:text-fg">What the transcript said</summary>
        <p className="code mt-1 break-words rounded bg-surface px-2 py-1">{row.extracted.raw_text}</p>
        <p className="mt-1">
          Page {row.page_number}, line {row.line_number}
          {row.extracted.term && ` · ${row.extracted.term}`}
          {row.extracted.section && ` · section ${row.extracted.section}`}
        </p>
      </details>
    </li>
  );
}
