"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";

import { Button, IconButton } from "@/components/ui/button";
import { Alert, EmptyState, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/field";
import { ApiError, api } from "@/lib/api";

import { ORIGIN_LABEL, STATUS_LABEL, termLabel, termOptions } from "./labels";

/**
 * One course at a time. All validation is the API's (structured issues shown
 * as returned) - this form decides nothing about grades, terms or courses.
 */
export function ManualEntry() {
  const client = useQueryClient();
  const courses = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const year = new Date().getFullYear();
  const [form, setForm] = useState({
    course_code: "", term_code: `${year}9`, status: "completed", grade: "",
    credits_earned: "", credit_origin: "rutgers",
  });
  const add = useMutation({
    mutationFn: () =>
      api.addCourse({
        course_code: form.course_code.trim(),
        term_code: form.term_code,
        status: form.status,
        grade: form.grade.trim() || null,
        credits_earned: form.credits_earned.trim() || null,
        credit_origin: form.credit_origin,
      }),
    onSuccess: () => {
      setForm((f) => ({ ...f, course_code: "", grade: "", credits_earned: "" }));
      void client.invalidateQueries({ queryKey: ["courses"] });
      void client.invalidateQueries({ queryKey: ["onboarding"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCourse(id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["courses"] });
      void client.invalidateQueries({ queryKey: ["onboarding"] });
    },
  });
  const issues = add.error instanceof ApiError ? add.error.issues : [];
  const issueFor = (field: string) => issues.find((i) => i.field === field)?.message;
  const gradeless = form.status !== "completed" || form.credit_origin !== "rutgers";

  return (
    <div className="space-y-5">
      <form
        className="space-y-4 rounded-lg border border-border bg-surface-elevated p-4"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Course code" hint="As Rutgers writes it, e.g. 01:198:111"
                 error={issueFor("course_code") ?? issueFor("supplement_code")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} aria-describedby={describedBy} aria-invalid={invalid}
                     className="code" placeholder="01:198:111" required
                     value={form.course_code}
                     onChange={(e) => setForm({ ...form, course_code: e.target.value })} />
            )}
          </Field>
          <Field label="Term" error={issueFor("term_code")}>
            {({ id, describedBy, invalid }) => (
              <Select id={id} aria-describedby={describedBy} aria-invalid={invalid}
                      value={form.term_code}
                      onChange={(e) => setForm({ ...form, term_code: e.target.value })}>
                {termOptions(year - 8, year + 2).map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Status" error={issueFor("status")}>
            {({ id, describedBy, invalid }) => (
              <Select id={id} aria-describedby={describedBy} aria-invalid={invalid}
                      value={form.status}
                      onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {Object.entries(STATUS_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Credit type" error={issueFor("credit_origin")}>
            {({ id, describedBy, invalid }) => (
              <Select id={id} aria-describedby={describedBy} aria-invalid={invalid}
                      value={form.credit_origin}
                      onChange={(e) => setForm({ ...form, credit_origin: e.target.value })}>
                {Object.entries(ORIGIN_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Grade" hint={gradeless ? "Not used for this course." : "e.g. A, B+, P, W"}
                 error={issueFor("grade")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} aria-describedby={describedBy} aria-invalid={invalid}
                     disabled={gradeless} value={gradeless ? "" : form.grade}
                     onChange={(e) => setForm({ ...form, grade: e.target.value })} />
            )}
          </Field>
          <Field label="Credits (optional)" hint="Leave empty to use the catalog credits."
                 error={issueFor("credits_earned")}>
            {({ id, describedBy, invalid }) => (
              <Input id={id} aria-describedby={describedBy} aria-invalid={invalid}
                     inputMode="decimal" className="tabular" value={form.credits_earned}
                     onChange={(e) => setForm({ ...form, credits_earned: e.target.value })} />
            )}
          </Field>
        </div>
        {issues.filter((i) => !i.field).map((i) => (
          <Alert key={i.code} tone="danger">{i.message}</Alert>
        ))}
        {add.isError && !issues.length && (
          <Alert tone="danger">That course couldn&apos;t be added. Please try again.</Alert>
        )}
        <div className="flex justify-end">
          <Button type="submit" variant="primary" loading={add.isPending}>Add course</Button>
        </div>
      </form>

      {courses.isPending && <Skeleton className="h-24" />}
      {courses.isSuccess && courses.data.entries.length === 0 && (
        <EmptyState title="No courses yet">Courses you add appear here.</EmptyState>
      )}
      {courses.isSuccess && courses.data.entries.length > 0 && (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface-elevated">
          {courses.data.entries.map((e) => (
            <li key={e.entry_id} className="flex items-center gap-3 px-3 py-2 sm:px-4">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
                  <span className="code font-medium text-fg">{e.course_code}</span>
                  <span className="text-fg-secondary [overflow-wrap:anywhere]">{e.title}</span>
                </p>
                <p className="tabular text-xs text-fg-muted">
                  {termLabel(e.term_code)} · {STATUS_LABEL[e.status]}
                  {e.grade && ` · ${e.grade}`} · {e.credits_earned ?? e.catalog_credits ?? "-"} cr
                  {e.credit_origin !== "rutgers" && ` · ${ORIGIN_LABEL[e.credit_origin]}`}
                </p>
              </div>
              <IconButton label={`Remove ${e.course_code}`} disabled={remove.isPending}
                          onClick={() => remove.mutate(e.entry_id)}>
                <Trash2 className="size-4" />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
