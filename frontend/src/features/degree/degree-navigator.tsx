"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileText, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Badge, Skeleton, StatusBadge } from "@/components/ui/feedback";
import { Select } from "@/components/ui/field";
import { ApiError, api } from "@/lib/api";
import { termLabel } from "@/lib/academic/vocabulary";
import { cn } from "@/lib/utils";
import type {
  DnHistoryRow,
  DnImportDetail,
  DnImportSummary,
  DnProgress,
  DnRequirementRow,
  Reconciliation,
} from "@/types/degree-audit";

/**
 * Degree Navigator audit import (Phase 7.4). Every status, label and number
 * here is the API's; the page only lets the student review and decide.
 */

const DN_STATUS: Record<string, string> = {
  satisfied: "Fulfilled",
  in_progress_or_planned: "In progress or planned",
  not_satisfied: "Not fulfilled",
  unknown: "Couldn't read",
};

const PLANNING: Record<string, string> = {
  satisfied: "Satisfied",
  in_progress: "In progress",
  planned: "Planned (counts as not satisfied)",
  not_satisfied: "Not satisfied",
  unknown: "CoursePilot estimate",
};

const RECONCILIATION: Record<Reconciliation, { label: string; tone: "success" | "warning" | "neutral" }> = {
  agrees: { label: "Agrees with CoursePilot", tone: "success" },
  dn_satisfied_cp_not: { label: "Needs review: CoursePilot doesn't count it yet", tone: "warning" },
  cp_satisfied_dn_not: { label: "Needs review: Degree Navigator doesn't count it", tone: "warning" },
  allocation_differs: { label: "Needs review: different courses used", tone: "warning" },
  unmapped: { label: "Unmapped - shown, not used", tone: "neutral" },
  unknown: { label: "Can't compare", tone: "neutral" },
};

/* --------------------------------------------------------- source notice */

export function useDnProgress() {
  return useQuery({ queryKey: ["dn-progress"], queryFn: api.dnProgress });
}

/** "estimated" vs "from your Degree Navigator audit", with freshness. */
export function DnSourceNotice({ progress, onImport }: { progress?: DnProgress; onImport: () => void }) {
  if (!progress) return null;
  if (progress.source === "coursepilot_estimate") {
    return (
      <Alert
        tone="info"
        title="Requirement status is estimated"
        action={<Button size="sm" variant="secondary" onClick={onImport}>Import audit</Button>}
      >
        {progress.label}. CoursePilot's own audit can't see Rutgers exceptions or equivalencies;
        Degree Navigator can.
      </Alert>
    );
  }
  const stale = progress.imports.some((i) => i.freshness?.stale);
  const disagreements = progress.requirements.filter((r) => r.needs_review && !r.excluded).length;
  return (
    <Alert
      tone={stale || disagreements ? "warning" : "success"}
      title={progress.label}
      action={stale ? <Button size="sm" variant="secondary" onClick={onImport}>Re-import</Button> : undefined}
    >
      {progress.imports.map((i) => (
        <span key={i.id} className="block">{i.dn_subject}: {i.freshness?.message}</span>
      ))}
      {disagreements > 0 && (
        <span className="block">
          {disagreements} requirement{disagreements === 1 ? "" : "s"}: {progress.disagreement_label}.
        </span>
      )}
    </Alert>
  );
}

/** Per requirement node: which source decided it (used by the requirement tree). */
export function dnLookup(progress?: DnProgress) {
  const byCode = new Map<string, DnRequirementRow>();
  for (const r of progress?.requirements ?? []) {
    if (r.requirement_code && !r.excluded && !byCode.has(r.requirement_code)) byCode.set(r.requirement_code, r);
  }
  return (code: string) => byCode.get(code);
}

export function DnNodeSource({ row, hasImport }: { row?: DnRequirementRow; hasImport: boolean }) {
  if (!row) {
    return <p className="text-xs text-fg-muted">{hasImport ? "Not in your Degree Navigator audit - CoursePilot estimate." : "Estimated by CoursePilot."}</p>;
  }
  return (
    <div className="space-y-1">
      <p className="text-xs text-fg-secondary">
        <span className="font-medium text-fg">From your Degree Navigator audit:</span>{" "}
        {DN_STATUS[row.dn_status]} → planning uses <span className="font-medium">{PLANNING[row.planning_status]}</span>. {row.planning_rule}
      </p>
      {row.needs_review && (
        <StatusBadge tone="warning" icon={AlertTriangle} label={row.label ?? "Needs review"} />
      )}
    </div>
  );
}

/* --------------------------------------------------------- import tab */

export function DegreeNavigatorImport() {
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const imports = useQuery({ queryKey: ["dn-imports"], queryFn: api.dnImports });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["dn-imports"] });
    void qc.invalidateQueries({ queryKey: ["dn-progress"] });
    void qc.invalidateQueries({ queryKey: ["audit"] });
  };
  const open = imports.data?.imports.filter((i) => i.state === "extracted") ?? [];
  const current = openId ?? open[0]?.id ?? null;

  return (
    <div className="space-y-6">
      <ExportInstructions />
      <DnUpload onUploaded={(id) => { setOpenId(id); refresh(); }} />
      {imports.isPending && <Skeleton className="h-24" />}
      <BlockPicker blocks={open} value={current} onChange={setOpenId} />
      {current && <DnReview id={current} onChanged={refresh} />}
      <ImportHistory imports={imports.data?.imports ?? []} />
    </div>
  );
}

/** 03a #4: one PDF may hold several degree blocks; the student picks which to review. */
export function BlockPicker({ blocks, value, onChange }: {
  blocks: Pick<DnImportSummary, "id" | "dn_subject" | "block_index" | "block_count" | "track_check">[];
  value: string | null;
  onChange: (id: string) => void;
}) {
  if (blocks.length < 2) return null;
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-fg-secondary">This PDF holds {blocks.length} degree audits. Choose one to review:</span>
      <Select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {blocks.map((i) => (
          <option key={i.id} value={i.id}>
            {i.dn_subject ?? "Unnamed degree"} (block {i.block_index + 1} of {i.block_count})
            {i.track_check === "mismatch" ? " - not your track" : ""}
          </option>
        ))}
      </Select>
    </label>
  );
}

export function ExportInstructions() {
  return (
    <section aria-labelledby="dn-how" className="space-y-2 rounded border border-border bg-surface-elevated p-4 text-sm">
      <h2 id="dn-how" className="font-semibold text-fg">Export your audit from Degree Navigator</h2>
      <ol className="list-decimal space-y-1 pl-5 text-fg-secondary">
        <li>Sign in to Degree Navigator (dn.rutgers.edu) yourself - CoursePilot never asks for your NetID.</li>
        <li>Open the audit you want: your major, or your school&apos;s Core.</li>
        <li>In Chrome, choose Print → Destination <span className="font-medium">Save as PDF</span> (not Microsoft Print to PDF) and save it.</li>
        <li>Upload that PDF below. It&apos;s read, then deleted; only the rows you review are kept.</li>
      </ol>
      <p className="text-xs text-fg-muted">
        Confirmed Degree Navigator statuses decide what planning treats as done. CoursePilot&apos;s own
        audit stays as a cross-check, and every disagreement is shown for you to take to your advisor.
      </p>
    </section>
  );
}

function DnUpload({ onUploaded }: { onUploaded: (id: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadDegreeAudit(file),
    onSuccess: (out) => {
      const first = out.imports.find((i) => i.state === "extracted");
      if (first) onUploaded(first.id);
    },
  });
  const failed = upload.data?.imports.find((i) => i.state === "failed");
  const refused = upload.error instanceof ApiError ? upload.error.issues[0]?.message ?? upload.error.message : null;
  return (
    <div className="space-y-3">
      <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-strong bg-surface px-6 py-8 text-center">
        <FileText aria-hidden className="size-7 text-fg-muted" />
        <p className="text-sm font-medium text-fg">Your Degree Navigator audit (PDF)</p>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          aria-label="Degree Navigator audit PDF"
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); }}
        />
        <Button variant="primary" onClick={() => input.current?.click()} loading={upload.isPending} disabled={upload.isPending}>
          <Upload aria-hidden className="size-4" />
          {upload.isPending ? "Reading your audit…" : "Choose PDF"}
        </Button>
      </div>
      {failed && <Alert tone="danger" title="This PDF couldn't be read">{failed.failure_message ?? failed.failure_code}</Alert>}
      {refused && <Alert tone="danger" title="Upload not accepted">{refused}</Alert>}
    </div>
  );
}

function ImportHistory({ imports }: { imports: { id: string; state: string; dn_subject: string | null; confirmed_at: string | null; superseded_at: string | null; freshness: { message: string } | null }[] }) {
  const done = imports.filter((i) => i.state === "confirmed");
  if (!done.length) return null;
  return (
    <section aria-labelledby="dn-done" className="space-y-2">
      <h2 id="dn-done" className="text-sm font-semibold text-fg">Confirmed audits</h2>
      <ul className="divide-y divide-border rounded border border-border bg-surface-elevated text-sm">
        {done.map((i) => (
          <li key={i.id} className="flex flex-col gap-1 p-3 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-fg">{i.dn_subject}</span>
            <span className="text-xs text-fg-muted">
              {i.superseded_at ? "Replaced by a newer import" : i.freshness?.message}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* --------------------------------------------------------- review */

function DnReview({ id, onChanged }: { id: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["dn-import", id], queryFn: () => api.dnImport(id) });
  const set = (d: DnImportDetail) => { qc.setQueryData(["dn-import", id], d); onChanged(); };
  const resolve = useMutation({
    mutationFn: ({ rowId, body }: { rowId: string; body: Record<string, unknown> }) =>
      api.resolveDnRow(id, rowId, { expected_version: detail.data!.version, ...body }),
    onSuccess: set,
  });
  const confirm = useMutation({ mutationFn: () => api.confirmDn(id, detail.data!.version), onSuccess: set });
  const discard = useMutation({ mutationFn: () => api.discardDn(id), onSuccess: () => onChanged() });

  if (detail.isPending) return <Skeleton className="h-64" />;
  if (detail.isError || !detail.data) return <Alert tone="danger" title="This import couldn't be loaded" />;
  const error = (resolve.error ?? confirm.error) as unknown;
  return (
    <DnReviewBody
      detail={detail.data}
      busy={resolve.isPending || confirm.isPending}
      confirming={confirm.isPending}
      error={error instanceof ApiError ? { message: error.message, issues: error.issues } : null}
      onResolve={(rowId, body) => resolve.mutate({ rowId, body })}
      onConfirm={() => confirm.mutate()}
      onDiscard={() => discard.mutate()}
    />
  );
}

/** The staged review and reconciliation view, rendered from the API's detail only. */
export function DnReviewBody({ detail: d, busy, confirming = false, error, onResolve, onConfirm, onDiscard }: {
  detail: DnImportDetail;
  busy: boolean;
  confirming?: boolean;
  error: { message: string; issues: { code: string; message: string }[] } | null;
  onResolve: (rowId: string, body: Record<string, unknown>) => void;
  onConfirm: () => void;
  onDiscard: () => void;
}) {
  const requirements = d.requirements.filter((r) => r.kind === "requirement");
  const goals = d.requirements.filter((r) => r.kind === "goal");
  const lines = d.courses.filter((c) => c.state === "needs_review");
  const editable = d.state === "extracted";

  return (
    <section aria-labelledby="dn-review" className="space-y-5">
      <div className="space-y-1">
        <h2 id="dn-review" className="text-base font-semibold text-fg">
          Review: {d.dn_subject ?? "Degree Navigator audit"}
          {d.dn_version_label && <span className="font-normal text-fg-muted"> ({d.dn_version_label})</span>}
        </h2>
        <p className="text-sm text-fg-secondary">{d.freshness?.message}</p>
      </div>
      {d.track_message && (
        <Alert tone={d.track_check === "mismatch" ? "danger" : d.track_check === "no_mapping" ? "warning" : "success"}>
          {d.track_message}
        </Alert>
      )}
      {error && (
        <Alert tone="danger" title={error.message}>
          {error.issues.map((i) => <span key={i.code} className="block">{i.message}</span>)}
        </Alert>
      )}

      <RequirementTable title="Requirements" rows={requirements} busy={busy} onResolve={onResolve} editable={editable} />
      {goals.length > 0 && (
        <RequirementTable title="Core goals" rows={goals} busy={busy} onResolve={onResolve} editable={editable} />
      )}

      <section aria-labelledby="dn-history" className="space-y-2">
        <h3 id="dn-history" className="text-sm font-semibold text-fg">Courses to add to your history</h3>
        <p className="text-xs text-fg-muted">
          These go through the same review as a transcript upload. A course already on your record with
          the same values is skipped; one that differs is a conflict you resolve.
        </p>
        <ul className="divide-y divide-border rounded border border-border bg-surface-elevated">
          {d.history.map((h) => (
            <HistoryRow key={h.id} row={h} busy={busy} editable={editable} onResolve={(body) => onResolve(h.id, body)} />
          ))}
        </ul>
        {d.history_outcome && (
          <p className="text-xs text-fg-muted">
            Added {d.history_outcome.inserted}, replaced {d.history_outcome.replaced}, already recorded {d.history_outcome.skipped}, excluded {d.history_outcome.excluded}.
          </p>
        )}
      </section>

      {lines.length > 0 && (
        <section aria-labelledby="dn-lines" className="space-y-2">
          <h3 id="dn-lines" className="text-sm font-semibold text-fg">Lines CoursePilot couldn&apos;t classify</h3>
          <ul className="divide-y divide-border rounded border border-border bg-surface-elevated">
            {lines.map((c) => (
              <li key={c.id} className="flex flex-col gap-2 p-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 space-y-1">
                  <p className="code text-fg [overflow-wrap:anywhere]">{c.raw_text}</p>
                  {c.issues.map((i) => <p key={i.code} className="text-xs text-fg-muted">{i.message}</p>)}
                </div>
                {editable && (
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => onResolve(c.id, { excluded: !c.excluded })}>
                    {c.excluded ? "Include" : "Exclude"}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {editable ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-fg-secondary">
            {d.readiness.ready ? "Ready to confirm." : `${d.readiness.blocking} row(s) need your review${d.readiness.track_blocks ? ", and this audit is for a different program" : ""}.`}
          </p>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onDiscard} disabled={busy}>Discard</Button>
            <Button variant="primary" onClick={onConfirm} disabled={!d.readiness.ready || busy} loading={confirming}>
              <CheckCircle2 aria-hidden className="size-4" /> Confirm
            </Button>
          </div>
        </div>
      ) : (
        <StatusBadge tone={d.state === "confirmed" ? "success" : "neutral"} label={d.state === "confirmed" ? "Confirmed" : d.state} />
      )}
    </section>
  );
}

export function RequirementTable({ title, rows, busy, editable, onResolve }: {
  title: string;
  rows: DnRequirementRow[];
  busy: boolean;
  editable: boolean;
  onResolve: (rowId: string, body: Record<string, unknown>) => void;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      <ul className="divide-y divide-border rounded border border-border bg-surface-elevated">
        {rows.map((r) => {
          const rec = r.reconciliation ? RECONCILIATION[r.reconciliation] : null;
          return (
            <li key={r.id} className={cn("space-y-2 p-3 text-sm", r.excluded && "opacity-60")}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 space-y-0.5">
                  <p className="font-medium text-fg [overflow-wrap:anywhere]">
                    {r.kind === "goal" ? `[${r.dn_code}] ${r.title ?? ""}` : r.dn_heading}
                  </p>
                  <p className="text-xs text-fg-muted">
                    Degree Navigator: {DN_STATUS[r.dn_status]}
                    {r.extracted_needs && ` · ${r.extracted_needs}`}
                    {r.requirement_code ? ` · CoursePilot ${r.requirement_code}: ${r.cp_status?.replaceAll("_", " ") ?? "-"}` : ""}
                  </p>
                  {r.requirement_code && <p className="text-xs text-fg-secondary">Planning uses: {PLANNING[r.planning_status]}. {r.planning_rule}</p>}
                </div>
                <div className="flex shrink-0 flex-wrap gap-1">
                  {rec && <Badge tone={rec.tone}>{rec.label}</Badge>}
                  {r.state === "needs_review" && !r.excluded && <Badge tone="warning">Needs review</Badge>}
                  {r.mapping_review_status === "pending_review" && <Badge tone="neutral">Mapping not yet reviewed</Badge>}
                </div>
              </div>
              {r.needs_review && <p className="text-xs font-medium text-warning">{r.label}</p>}
              {r.issues.filter((i) => i.severity !== "info").map((i) => <p key={i.code} className="text-xs text-fg-muted">{i.message}</p>)}
              {editable && r.state !== "unmapped" && (
                <div className="flex flex-wrap items-center gap-2">
                  {r.state === "needs_review" && !r.excluded && (
                    <Select aria-label="Status as Degree Navigator shows it" value="" disabled={busy}
                      onChange={(e) => e.target.value && onResolve(r.id, { dn_status: e.target.value })}>
                      <option value="">Set status as Degree Navigator shows it…</option>
                      <option value="satisfied">Fulfilled</option>
                      <option value="in_progress_or_planned">In progress or planned</option>
                      <option value="not_satisfied">Not fulfilled</option>
                    </Select>
                  )}
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => onResolve(r.id, { excluded: !r.excluded })}>
                    {r.excluded ? "Use this row" : "Don't use this row"}
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function HistoryRow({ row, busy, editable, onResolve }: {
  row: DnHistoryRow;
  busy: boolean;
  editable: boolean;
  onResolve: (body: Record<string, unknown>) => void;
}) {
  const tone = row.state === "matched" ? "success" : row.state === "conflict" ? "warning" : row.state === "needs_review" ? "warning" : "danger";
  return (
    <li className={cn("space-y-1 p-3 text-sm", row.excluded && "opacity-60")}>
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <span>
          <span className="code font-medium text-fg">{row.course_code}</span>
          <span className="text-fg-secondary"> · {row.term_code ? termLabel(row.term_code) : "term unknown"} · {row.status?.replace("_", " ") ?? "status unknown"}{row.grade ? ` · ${row.grade}` : ""}</span>
        </span>
        <Badge tone={tone}>{row.state.replace("_", " ")}</Badge>
      </div>
      {row.issues.filter((i) => i.severity !== "info").map((i) => <p key={i.code} className="text-xs text-fg-muted">{i.message}</p>)}
      {editable && (
        <div className="flex flex-wrap gap-2">
          {row.state === "conflict" && (
            <>
              <Button size="sm" variant={row.resolution === "keep_existing" ? "primary" : "secondary"} disabled={busy} onClick={() => onResolve({ resolution: "keep_existing" })}>Keep my record</Button>
              <Button size="sm" variant={row.resolution === "replace_existing" ? "primary" : "secondary"} disabled={busy} onClick={() => onResolve({ resolution: "replace_existing" })}>Use Degree Navigator&apos;s</Button>
            </>
          )}
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onResolve({ excluded: !row.excluded })}>{row.excluded ? "Include" : "Exclude"}</Button>
        </div>
      )}
    </li>
  );
}
