"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/feedback";
import { termLabel } from "@/lib/academic/vocabulary";
import type { DroppedCourse, Registration, ScheduleImport, ScheduleImportRow } from "@/types/schedule-import";
import type { SectionChoice } from "@/types/schedule";
import { asOption, WeekCalendar } from "./week-calendar";

/**
 * The week of the student's CONFIRMED imported registrations (Phase 7.2.2).
 * Only registrations that exactly matched a published SOC section carry
 * meetings; anything else stays in the list below and is never drawn - the
 * importer never invents a meeting time.
 */
export function ImportedWeek({ registrations }: { registrations: Registration[] }) {
  const terms = [...new Set(registrations.map((r) => r.term_code))].sort();
  const term = terms[terms.length - 1];
  const drawable = registrations.filter((r) => r.term_code === term && r.metadata_state === "available");
  if (!term || drawable.length === 0) return null;
  const choices = drawable.map((r) => ({
    course: r.course_string, component: "primary", course_string: r.course_string,
    supplement_code: r.supplement_code, title: null, section_number: r.section_number,
    index_number: r.index_number ?? "", campus_code: "", meetings: r.normalized_meetings,
    instructors: r.instructors, cross_listed_indexes: [], time_verified: false, notes: null,
    equivalent_sections: [], credits: null, meeting_components: [], component_evidence: null,
    meeting_text_in_notes: null,
    availability: { state: "unknown", freshness: "unobserved", observed_at: null, source: null, source_hash: null, since: null, reasons: [] },
    restriction: { outcome: "unknown", entries: [], matched: null, open_to_text: null, eligibility_text: null, special_permission: null, reasons: [] },
  }) satisfies SectionChoice);
  const hidden = registrations.filter((r) => r.term_code === term).length - drawable.length;
  return (
    <section aria-labelledby="imported-week" className="space-y-2">
      <h5 id="imported-week" className="text-sm font-medium text-fg">{termLabel(term)} week, from your confirmed registrations</h5>
      <WeekCalendar option={asOption(choices)} />
      {hidden > 0 && <p className="text-xs text-fg-muted">{hidden} registration{hidden === 1 ? "" : "s"} without public meeting data {hidden === 1 ? "is" : "are"} listed below, not drawn.</p>}
    </section>
  );
}

const MATCH_LABEL: Record<ScheduleImportRow["match_status"], string> = {
  EXACT_MATCH: "Exact match with Rutgers section data",
  MULTIPLE_MATCHES: "Several Rutgers sections match - enter the exact index",
  NO_MATCH: "No matching Rutgers section data",
  NEEDS_CONFIRMATION: "Needs your review",
};
const ISSUE_LABEL: Record<string, string> = {
  IDENTIFIERS_DISAGREE: "the index and the course/section point to different sections",
  CONFIRM_REPLACE_EXISTING: "differs from a section you already confirmed",
  DUPLICATE_REGISTRATION: "listed more than once",
  RECORD_SAYS_COMPLETED: "your record shows this course COMPLETED in this term",
};

const HISTORY_EFFECT: Record<string, string> = {
  add_in_progress: "adds it to your record as in progress",
  already_in_progress: "it is already in progress on your record (unchanged)",
  planned_to_in_progress: "changes it from planned to in progress",
  keep_completed: "keeps your completed record; no section is recorded",
  record_says_completed: "needs your decision: your record says completed",
};
const SECTION_EFFECT: Record<string, string> = {
  add: "records this section as your current section",
  already_confirmed: "this section is already your confirmed section",
  replace: "replaces the section you confirmed before",
  differs: "differs from the section you confirmed before",
};

/** Phase 7.4: the pre-selected section and what confirming will do - before it does it. */
export function rowEffectText(row: ScheduleImportRow): string | null {
  if (row.excluded || row.state !== "matched" && row.state !== "conflict") return null;
  if (!row.history_effect) return null;
  const pre = row.resolved_index ? `Pre-selected: section ${row.section_number}, index ${row.resolved_index}. ` : "";
  const parts = [HISTORY_EFFECT[row.history_effect], row.section_effect ? SECTION_EFFECT[row.section_effect] : null].filter(Boolean);
  return `${pre}Confirming ${parts.join(" and ")}.`;
}

/** Courses confirmed earlier that this newer schedule does not list. Never deleted. */
export function DroppedList({ courses }: { courses: DroppedCourse[] }) {
  if (!courses.length) return null;
  return (
    <Alert tone="warning" title="Not on your latest WebReg schedule - dropped?">
      <ul className="list-disc pl-5">
        {courses.map((c) => (
          <li key={`${c.term_code}-${c.course_string}`}>{termLabel(c.term_code)} · {c.course_string}{c.index_number ? ` · index ${c.index_number}` : ""}</li>
        ))}
      </ul>
      <span className="block">CoursePilot removes nothing. If you dropped a course, update your record.</span>
    </Alert>
  );
}

/** Numbered export steps for the WebReg print (Prompt 04 import step). */
export function WebRegExportSteps() {
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm text-fg-secondary">
      <li>Sign in to WebReg yourself - CoursePilot never asks for your NetID.</li>
      <li>Open <span className="font-medium">View / Print Schedule</span> for the term.</li>
      <li>In Chrome, choose Print → Destination <span className="font-medium">Save as PDF</span> (not Microsoft Print to PDF) and save it.</li>
      <li>Upload that PDF here. It&apos;s read, then discarded; only the rows you review are kept.</li>
    </ol>
  );
}

/** The student-facing match state; internal row states are never shown raw. */
export function matchText(row: ScheduleImportRow): string {
  if (row.excluded) return "Excluded by you";
  const issues = row.issues.map((code) => ISSUE_LABEL[code]).filter(Boolean);
  return `${MATCH_LABEL[row.match_status]}${issues.length ? `: ${issues.join("; ")}` : ""}`;
}

export function ScheduleImportReview() {
  const client = useQueryClient();
  const [staged, setStaged] = useState<ScheduleImport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const imports = useQuery({queryKey: ["schedule-imports"], queryFn: api.scheduleImports});
  const registrations = useQuery({ queryKey: ["registrations"], queryFn: api.registrations });
  const upload = useMutation({
    mutationFn: api.uploadSchedule,
    onSuccess: (value) => { setStaged(value); setError(null); client.invalidateQueries({queryKey: ["schedule-imports"]}); },
    onError: () => setError("This PDF could not be read safely. CoursePilot reads only PDFs that keep their text: in WebReg, open View / Print Schedule and choose your browser's own \"Save as PDF\" destination. \"Microsoft Print to PDF\" and scans turn the text into drawings, which CoursePilot does not read (no OCR). You can still confirm sections manually."),
  });
  const edit = useMutation({
    mutationFn: ({row, changes}: {row: ScheduleImportRow; changes: Record<string, unknown>}) =>
      api.editScheduleRow(staged!.id, row.id, {expected_version: staged!.version, ...changes}),
    onSuccess: setStaged,
    onError: () => setError("The import changed or the correction was invalid. Reload it before confirming."),
  });
  const confirm = useMutation({
    mutationFn: () => api.confirmSchedule(staged!.id, staged!.version),
    onSuccess: (value) => { setStaged(value); client.invalidateQueries({queryKey: ["registrations"]}); },
    onError: () => setError("The import needs review or its public section data changed. Review it again before confirming."),
  });
  const discard = useMutation({
    mutationFn: () => api.discardSchedule(staged!.id),
    onSuccess: () => {setStaged(null); setError(null); client.invalidateQueries({queryKey: ["schedule-imports"]});},
  });
  const busy = upload.isPending || edit.isPending || confirm.isPending || discard.isPending;
  return <section aria-labelledby="schedule-import-title" className="space-y-4 rounded-lg border border-border p-4">
    <h3 id="schedule-import-title" className="font-semibold text-fg">Import my registered schedule</h3>
    <WebRegExportSteps />
    <p className="text-sm text-fg-secondary">Review every row before confirming. Confirming records these courses as in progress for the term and these sections as your current schedule. It does not register you or prove eligibility. Image-only PDFs are unsupported; OCR is not used.</p>
    <label className="block text-sm text-fg">Schedule PDF
      <input type="file" accept="application/pdf" disabled={busy || staged?.state === "extracted"} className="mt-2 block min-h-11 w-full text-sm file:mr-3 file:min-h-11 file:rounded file:border file:border-border file:bg-surface file:px-3 file:text-fg"
        onChange={(event) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.target.value = ""; }} />
    </label>
    {!staged && imports.data?.imports.filter((imp) => imp.state === "extracted").map((imp) => <Button key={imp.id} variant="secondary" disabled={busy} onClick={() => api.scheduleImport(imp.id).then(setStaged).catch(() => setError("Import could not be loaded."))}>Resume pending schedule review</Button>)}
    {error && <Alert tone="warning">{error}</Alert>}
    {staged && <div className="space-y-3">
      <p className="text-sm text-fg-secondary">{staged.state === "extracted"
        ? `Review every row. This review expires ${new Date(staged.expires_at).toLocaleString()}.`
        : staged.state === "confirmed" ? "Confirmed. These registrations are recorded below." : `This import is ${staged.state}.`}</p>
      <ul className="space-y-3">{staged.rows.map((row) => <li key={row.id} className="space-y-2 rounded border border-border p-3 text-sm">
        <p className="text-fg">{termLabel(row.term_code)} · {row.course_string} · Section {row.section_number} · Index {row.index_number ?? "not supplied"}</p>
        <p className="text-fg-secondary" data-match={row.match_status}>{matchText(row)}</p>
        {rowEffectText(row) && <p className="text-fg">{rowEffectText(row)}</p>}
        {row.outcome && <p className="text-fg-muted">Recorded: {[row.outcome.history && HISTORY_EFFECT[row.outcome.history], row.outcome.section && (SECTION_EFFECT[row.outcome.section] ?? row.outcome.section)].filter(Boolean).join("; ")}</p>}
        {staged.state === "extracted" && <>
          <label className="flex items-center gap-2"><input type="checkbox" checked={row.excluded} disabled={busy} onChange={(e) => edit.mutate({row, changes: {excluded: e.target.checked}})} />Exclude this row</label>
          <label className="block">Correct registration index (five digits)
            <input className="ml-2 min-h-11 rounded border border-border bg-surface px-2 text-fg" aria-label={`Registration index for ${row.course_string} section ${row.section_number}`} defaultValue={row.index_number ?? ""} inputMode="numeric" maxLength={5}
              disabled={busy} onBlur={(e) => { const value = e.target.value; if (value !== (row.index_number ?? "")) edit.mutate({row, changes: {index_number: value || null}}); }} />
          </label>
          {row.issues.includes("CONFIRM_REPLACE_EXISTING") && <label className="flex items-center gap-2"><input type="checkbox" checked={row.replace_existing} disabled={busy} onChange={(e) => edit.mutate({row, changes: {replace_existing: e.target.checked}})} />Replace the previously confirmed identity for this section</label>}
          {row.issues.includes("RECORD_SAYS_COMPLETED") || row.keep_record ? <label className="flex items-center gap-2"><input type="checkbox" checked={!!row.keep_record} disabled={busy} onChange={(e) => edit.mutate({row, changes: {keep_record: e.target.checked}})} />Keep my completed record (record no section for this course)</label> : null}
          {row.state === "metadata_unavailable" && <p className="text-fg-secondary">No published Rutgers section has this index. Confirmation preserves only the identity you reviewed - nothing is added to your history or current schedule, and no meetings are invented.</p>}
        </>}
      </li>)}</ul>
      {staged.state === "extracted" && <DroppedList courses={staged.possibly_dropped ?? []} />}
      {staged.state === "extracted" && <div className="flex flex-wrap gap-2">
        <Button disabled={busy || !staged.ready} onClick={() => confirm.mutate()}>Confirm reviewed registrations</Button>
        <Button variant="secondary" disabled={busy} onClick={() => api.scheduleImport(staged.id).then(setStaged).catch(() => setError("Import could not be reloaded."))}>Reload review</Button>
        <Button variant="ghost" disabled={busy} onClick={() => discard.mutate()}>Discard import</Button>
      </div>}
    </div>}
    {(registrations.data?.registrations.length ?? 0) > 0 && <div className="space-y-2">
      <h4 className="font-medium text-fg">Confirmed imported registrations</h4>
      <ImportedWeek registrations={registrations.data!.registrations} />
      <ul className="space-y-3 text-sm">{registrations.data!.registrations.map((r) => <li key={r.id} className="rounded border border-border p-3">
        <p className="text-fg">{termLabel(r.term_code)} · {r.course_string} · Section {r.section_number} · Index {r.index_number ?? "not supplied"}</p>
        <p className="text-fg-secondary">Confirmed by you from schedule PDF · Public metadata {r.metadata_state}</p>
        {r.on_latest_import === false && <p className="text-warning">Not on your latest WebReg schedule - dropped? Nothing was removed.</p>}
        {r.meetings.map((m, i) => <p key={i} className="text-fg-secondary">{m.day ?? "Day not published"} {m.start && m.end ? `${m.start}–${m.end}` : "No published meeting time"} · {[m.mode, m.campus, m.building, m.room].filter(Boolean).join(" · ")}</p>)}
        {r.metadata_state === "unavailable" && <p className="text-fg-secondary">Registration identity preserved; meeting details cannot be verified.</p>}
      </li>)}</ul>
    </div>}
  </section>;
}
